from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Iterable, Optional, cast

import backoff
from web3 import Web3
from web3.contract import Contract
from web3.contract.contract import ContractEvent
from web3.exceptions import ContractLogicError, ProviderConnectionError
from web3.types import EventData

from ...abi import fetch_subvault_addresses, load_stakewise_vault_abi
from ...clients.etherscan_logs import EtherscanLogsClient
from ...constants import (
    STAKEWISE_ADDRESSES,
    STAKEWISE_EXIT_LOG_CHUNK,
)
from ...logger import get_logger
from ...settings import OracleSettings
from .base import AssetData, BaseAssetAdapter

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class StakeWiseAddressesResolved:
    vault: str
    os_token: str


@dataclass(frozen=True, slots=True)
class ExitQueueTicket:
    ticket: int
    shares: int
    receiver: str
    block_number: int
    log_index: int
    timestamp: int
    assets_hint: int | None = None


@dataclass(frozen=True, slots=True)
class AccountState:
    assets: int = 0
    os_shares: int = 0


@dataclass(frozen=True, slots=True)
class ExitExposure:
    staked_eth: int = 0
    eth_in_queue: int = 0
    eth_claimable: int = 0
    os_shares_liability: int = 0
    ticket_count: int = 0

    @property
    def eth_collateral(self) -> int:
        return self.staked_eth + self.eth_in_queue + self.eth_claimable


@dataclass(frozen=True, slots=True)
class StakewiseVaultContext:
    address: str
    contract: Contract
    exit_events: list[ContractEvent]


class StakeWiseAdapter(BaseAssetAdapter):
    """Adapter for StakeWise vault positions (direct staking only)."""

    def __init__(
        self,
        config: OracleSettings,
        *,
        stakewise_vault_address: str | None = None,
        stakewise_vault_addresses: list[str] | None = None,
        stakewise_exit_queue_start_block: int | None = None,
        stakewise_exit_max_lookback_blocks: int | None = None,
        skip_exit_queue_scan: bool | None = None,
    ):
        super().__init__(config)

        self.w3 = self._build_web3(config.vault_rpc_required)

        adapter_config = config.adapters.stakewise
        config_vaults = adapter_config.stakewise_vault_addresses
        fallback_single = stakewise_vault_address or (
            config_vaults[0] if config_vaults else None
        )
        resolved = self._resolve_addresses(config, fallback_single)

        self.block_identifier = config.block_number_required
        self.eth_asset = self._resolve_eth_asset(config)
        self.os_token_address = self.w3.to_checksum_address(resolved.os_token)

        # Explicit list > Adapter config > Single explicit or default
        resolved_vaults = (
            stakewise_vault_addresses
            or config_vaults
            or [stakewise_vault_address or resolved.vault]
        )
        if not resolved_vaults:
            raise ValueError("StakeWise adapter requires at least one vault address")

        self.vault_contexts: list[StakewiseVaultContext] = [
            self._build_vault_context(address) for address in resolved_vaults
        ]
        self.vault_address = self.vault_contexts[0].address

        self._exit_log_chunk = STAKEWISE_EXIT_LOG_CHUNK
        self._exit_queue_start_block = (
            stakewise_exit_queue_start_block
            or adapter_config.stakewise_exit_queue_start_block
        )
        self._exit_max_lookback_blocks = (
            stakewise_exit_max_lookback_blocks
            or adapter_config.stakewise_exit_max_lookback_blocks
        )
        self._skip_exit_queue_scan = (
            skip_exit_queue_scan
            if skip_exit_queue_scan is not None
            else adapter_config.skip_exit_queue_scan
        )
        self._rpc_sem = asyncio.Semaphore(getattr(config, "max_calls", 5))
        self._rpc_delay = getattr(config, "rpc_delay", 0.15)
        self._rpc_jitter = getattr(config, "rpc_jitter", 0.10)
        self._block_timestamp_cache: dict[int, int] = {}

        # Optional Etherscan client for faster log queries
        self._etherscan_client: EtherscanLogsClient | None = None
        if adapter_config.etherscan_api_key:
            self._etherscan_client = EtherscanLogsClient(
                api_key=adapter_config.etherscan_api_key,
                chain_id=config.chain_id,
                page_size=adapter_config.etherscan_page_size,
            )
            logger.debug("StakeWise Etherscan client enabled for log queries")

        extra_address_candidates = [
            self.w3.to_checksum_address(addr)
            for addr in adapter_config.extra_addresses
            if addr
        ]
        deduped: dict[str, str] = {}
        for checksum in extra_address_candidates:
            deduped.setdefault(checksum.lower(), checksum)
        self._extra_addresses = list(deduped.values())
        if self._extra_addresses:
            logger.debug(
                "StakeWise extra addresses configured: %s", self._extra_addresses
            )

    @property
    def adapter_name(self) -> str:
        return "stakewise"

    @backoff.on_exception(
        backoff.expo,
        (ProviderConnectionError,),
        max_time=30,
        jitter=backoff.full_jitter,
    )
    async def _rpc(self, fn, *args, **kwargs):
        async with self._rpc_sem:
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            finally:
                delay = self._rpc_delay + random.random() * self._rpc_jitter
                if delay > 0:
                    await asyncio.sleep(delay)

    async def fetch_assets(self, subvault_address: str) -> list[AssetData]:
        user = self.w3.to_checksum_address(subvault_address)
        logger.info(
            "StakeWise adapter collecting balances — user=%s block=%s skip_exit_queue=%s",
            user,
            self.block_identifier,
            self._skip_exit_queue_scan,
        )

        total_staked = 0
        total_queue = 0
        total_claimable = 0
        total_os_liabilities = 0
        total_tickets = 0

        for context in self.vault_contexts:
            user_state = await self._fetch_account_state(context.contract, user)

            # Skip expensive exit queue scan if user has no position
            if user_state.assets == 0 and user_state.os_shares == 0:
                logger.debug(
                    "StakeWise Adapter skipping — no position for user=%s vault=%s",
                    user,
                    context.address,
                )
                continue

            if self._skip_exit_queue_scan:
                total_staked += user_state.assets
                total_os_liabilities += user_state.os_shares
                continue

            tickets = await self._scan_exit_queue_tickets(context, user)
            exposure = await self._compute_exit_exposure(context, user_state, tickets)

            total_staked += exposure.staked_eth
            total_queue += exposure.eth_in_queue
            total_claimable += exposure.eth_claimable
            total_os_liabilities += exposure.os_shares_liability
            total_tickets += exposure.ticket_count

        aggregated = ExitExposure(
            staked_eth=total_staked,
            eth_in_queue=total_queue,
            eth_claimable=total_claimable,
            os_shares_liability=total_os_liabilities,
            ticket_count=total_tickets,
        )

        assets: list[AssetData] = []
        self._append_asset(assets, self.eth_asset, aggregated.eth_collateral)
        if aggregated.os_shares_liability:
            self._append_asset(
                assets,
                self.os_token_address,
                -aggregated.os_shares_liability,
                tvl_only=True,
            )

        self._log_summary(user, aggregated)
        return assets

    async def fetch_all_assets(self) -> list[AssetData]:
        """Fetch StakeWise positions for all subvaults plus extra addresses."""

        subvault_addresses = await fetch_subvault_addresses(self.config)
        addresses_to_scan = [self.config.vault_address_required] + subvault_addresses

        seen = {addr.lower() for addr in addresses_to_scan}
        for extra in self._extra_addresses:
            if extra.lower() not in seen:
                addresses_to_scan.append(extra)
                seen.add(extra.lower())

        logger.info(
            "StakeWise fetching positions for main vault + %d subvaults + %d extra addresses",
            len(subvault_addresses),
            len(addresses_to_scan) - 1 - len(subvault_addresses),
        )

        results = await asyncio.gather(
            *[self.fetch_assets(addr) for addr in addresses_to_scan],
            return_exceptions=True,
        )

        all_assets: list[AssetData] = []
        failed: list[tuple[str, BaseException]] = []

        for addr, result in zip(addresses_to_scan, results):
            if isinstance(result, BaseException):
                logger.error(
                    "StakeWise failed to fetch assets for %s: %s", addr, result
                )
                failed.append((addr, result))
            elif isinstance(result, list):
                all_assets.extend(result)

        if failed:
            addr_list = ", ".join(addr for addr, _ in failed)
            raise ValueError(
                f"StakeWise failed to fetch assets from {len(failed)} address(es): {addr_list}"
            )

        logger.info(
            "StakeWise fetched %d total asset entries from %d addresses",
            len(all_assets),
            len(addresses_to_scan),
        )
        return all_assets

    @staticmethod
    def _build_web3(rpc_url: str) -> Web3:
        w3 = Web3(Web3.HTTPProvider(rpc_url))
        if not w3.is_connected():
            raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")
        return w3

    @staticmethod
    def _resolve_eth_asset(config: OracleSettings) -> str:
        eth_asset = config.assets.get("ETH")
        if eth_asset is None:
            raise ValueError("ETH address must be configured for StakeWise adapter")
        return eth_asset

    def _resolve_addresses(
        self, config: OracleSettings, override_vault: Optional[str]
    ) -> StakeWiseAddressesResolved:
        defaults = STAKEWISE_ADDRESSES.get(config.network.value) or {}

        values = {
            "stakewise_vault_address": override_vault or defaults.get("vault"),
            "stakewise_os_token_address": defaults["os_token"],
        }
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(
                "Missing StakeWise configuration values: "
                f"{', '.join(missing)}. Provide them via configuration or ensure defaults exist for network '{config.network.value}'."
            )

        return StakeWiseAddressesResolved(
            vault=cast(str, values["stakewise_vault_address"]),
            os_token=cast(str, values["stakewise_os_token_address"]),
        )

    def _build_contract(self, address: str, abi: Iterable[dict]) -> Contract:
        checksum = self.w3.to_checksum_address(address)
        return self.w3.eth.contract(address=checksum, abi=list(abi))

    def _build_vault_context(self, address: str) -> StakewiseVaultContext:
        contract = self._build_contract(address, load_stakewise_vault_abi())
        exit_events: list[ContractEvent] = [contract.events.ExitQueueEntered()]
        v2_event = getattr(contract.events, "V2ExitQueueEntered", None)
        if callable(v2_event):
            exit_events.append(cast(ContractEvent, v2_event()))
        return StakewiseVaultContext(
            address=self.w3.to_checksum_address(address),
            contract=contract,
            exit_events=exit_events,
        )

    async def _fetch_account_state(
        self, vault_contract: Contract, account: str
    ) -> AccountState:
        shares, os_shares = await asyncio.gather(
            self._rpc(
                vault_contract.functions.getShares(account).call,
                block_identifier=self.block_identifier,
            ),
            self._rpc(
                vault_contract.functions.osTokenPositions(account).call,
                block_identifier=self.block_identifier,
            ),
        )
        assets = (
            0
            if not shares
            else await self._rpc(
                vault_contract.functions.convertToAssets(shares).call,
                block_identifier=self.block_identifier,
            )
        )
        return AccountState(assets=int(assets), os_shares=int(os_shares))

    async def _scan_exit_queue_tickets(
        self, context: StakewiseVaultContext, user: str
    ) -> list[ExitQueueTicket]:
        if not context.exit_events:
            return []

        tickets: dict[int, ExitQueueTicket] = {}

        # Try Etherscan first (one-shot from block 0, no chunking needed)
        if self._etherscan_client:
            logger.debug(
                "StakeWise exit queue scan via Etherscan — vault=%s user=%s",
                context.address,
                user,
            )
            for event in context.exit_events:
                logs = await self._get_exit_logs_etherscan(
                    event, user, 0, self.block_identifier, vault_address=context.address
                )
                if logs is not None:
                    await self._process_exit_logs(logs, tickets)
                else:
                    # Etherscan failed, fall back to RPC for everything
                    tickets.clear()
                    return await self._scan_exit_queue_tickets_rpc(context, user)
        else:
            return await self._scan_exit_queue_tickets_rpc(context, user)

        ordered = sorted(tickets.values(), key=lambda t: (t.block_number, t.log_index))
        logger.info(
            "StakeWise exit queue scan completed (Etherscan) — vault=%s user=%s tickets=%d",
            context.address,
            user,
            len(ordered),
        )
        return ordered

    async def _scan_exit_queue_tickets_rpc(
        self, context: StakewiseVaultContext, user: str
    ) -> list[ExitQueueTicket]:
        """Chunked RPC fallback for exit queue scanning."""
        tickets: dict[int, ExitQueueTicket] = {}
        min_block = self._resolve_min_block()

        iterations = 0
        logger.warning(
            "StakeWise exit queue scan via RPC — vault=%s user=%s from_block=%d (this may take time)",
            context.address,
            user,
            min_block,
        )
        for from_block, to_block in self._block_ranges(
            self.block_identifier, min_block
        ):
            iterations += 1
            for event in context.exit_events:
                logs = await self._get_exit_logs_rpc(event, user, from_block, to_block)
                await self._process_exit_logs(logs, tickets)

        ordered = sorted(tickets.values(), key=lambda t: (t.block_number, t.log_index))
        logger.info(
            "StakeWise exit queue scan completed (RPC) — vault=%s user=%s tickets=%d iterations=%d",
            context.address,
            user,
            len(ordered),
            iterations,
        )
        return ordered

    async def _process_exit_logs(
        self, logs: list[EventData], tickets: dict[int, ExitQueueTicket]
    ) -> None:
        """Process exit logs and update tickets dict."""
        for log in logs:
            args = log["args"]
            ticket_id = int(args["positionTicket"])
            block_number = int(log["blockNumber"])
            log_index = int(log["logIndex"])

            existing = tickets.get(ticket_id)
            if existing and not (
                block_number > existing.block_number
                or (
                    block_number == existing.block_number
                    and log_index > existing.log_index
                )
            ):
                continue

            timestamp = await self._resolve_block_timestamp(block_number)
            assets_value = args.get("assets")
            tickets[ticket_id] = ExitQueueTicket(
                ticket=ticket_id,
                shares=int(args["shares"]),
                receiver=self.w3.to_checksum_address(args["receiver"]),
                block_number=block_number,
                log_index=log_index,
                timestamp=timestamp,
                assets_hint=None if assets_value is None else int(assets_value),
            )

    async def _get_exit_logs_etherscan(
        self,
        event: ContractEvent,
        user: str,
        from_block: int,
        to_block: int,
        *,
        vault_address: str | None = None,
    ) -> list[EventData] | None:
        # Try Etherscan first if available
        if self._etherscan_client is None:
            raise ValueError("Etherscan client not configured")

        if vault_address is None:
            raise ValueError("Vault address required for Etherscan")

        try:
            return await asyncio.to_thread(
                self._etherscan_client.fetch_logs,
                event,
                vault_address,
                {"owner": user},
                from_block,
                to_block,
            )

        except ValueError as exc:  # pragma: no cover - provider variance
            event_name = getattr(event, "abi", {}).get("name", "unknown")
            logger.warning(
                "StakeWise exit log query failed — event=%s user=%s chunk=[%d,%d] err=%s",
                event_name,
                user,
                from_block,
                to_block,
                exc,
            )
            return None

    async def _get_exit_logs_rpc(
        self,
        event: ContractEvent,
        user: str,
        from_block: int,
        to_block: int,
    ) -> list[EventData]:
        try:
            return await self._rpc(
                event.get_logs,
                from_block=from_block,
                to_block=to_block,
                argument_filters={"owner": user},
            )
        except ValueError as exc:  # pragma: no cover - provider variance
            event_name = getattr(event, "abi", {}).get("name", "unknown")
            logger.warning(
                "StakeWise exit log query failed — event=%s user=%s chunk=[%d,%d] err=%s",
                event_name,
                user,
                from_block,
                to_block,
                exc,
            )
            return []

    async def _resolve_block_timestamp(self, block_number: int) -> int:
        cached = self._block_timestamp_cache.get(block_number)
        if cached is not None:
            return cached
        block = await self._rpc(self.w3.eth.get_block, block_number)
        timestamp = int(block["timestamp"])
        self._block_timestamp_cache[block_number] = timestamp
        return timestamp

    async def _compute_exit_exposure(
        self,
        context: StakewiseVaultContext,
        user_state: AccountState,
        tickets: list[ExitQueueTicket],
    ) -> ExitExposure:
        eth_in_queue = 0
        eth_claimable = 0

        for ticket in tickets:
            ticket_assets = ticket.assets_hint
            if ticket_assets is None:
                ticket_assets = await self._rpc(
                    context.contract.functions.convertToAssets(ticket.shares).call,
                    block_identifier=self.block_identifier,
                )
                ticket_assets = int(ticket_assets)

            exit_queue_index = await self._fetch_exit_queue_index(
                context.contract, ticket.ticket
            )
            if exit_queue_index is None or exit_queue_index < 0:
                eth_in_queue += ticket_assets
                continue

            (
                left_tickets,
                exited_tickets,
                exit_assets,
            ) = await self._calculate_exited_assets(
                context.contract,
                ticket.receiver,
                ticket.ticket,
                ticket.timestamp,
                exit_queue_index,
            )
            if left_tickets == 0 and exited_tickets == 0 and exit_assets == 0:
                logger.debug(
                    "StakeWise skipping claimed ticket — ticket=%d receiver=%s",
                    ticket.ticket,
                    ticket.receiver,
                )
                continue

            eth_claimable += exit_assets
            if left_tickets > 0:
                queue_assets = await self._rpc(
                    context.contract.functions.convertToAssets(left_tickets).call,
                    block_identifier=self.block_identifier,
                )
                eth_in_queue += int(queue_assets)

        return ExitExposure(
            staked_eth=user_state.assets,
            eth_in_queue=eth_in_queue,
            eth_claimable=eth_claimable,
            os_shares_liability=user_state.os_shares,
            ticket_count=len(tickets),
        )

    async def _fetch_exit_queue_index(
        self, vault_contract: Contract, ticket: int
    ) -> int | None:
        try:
            index = await self._rpc(
                vault_contract.functions.getExitQueueIndex(ticket).call,
                block_identifier=self.block_identifier,
            )
        except (ContractLogicError, ValueError):  # pragma: no cover - defensive
            logger.warning(
                "StakeWise exit queue index lookup failed — ticket=%d",
                ticket,
            )
            return None
        return int(index)

    async def _calculate_exited_assets(
        self,
        vault_contract: Contract,
        receiver: str,
        ticket: int,
        timestamp: int,
        exit_queue_index: int,
    ) -> tuple[int, int, int]:
        """Return (left_tickets, exited_tickets, exited_assets).

        If all three are zero, the ticket was already claimed or never existed.
        """
        try:
            left_tickets, exited_tickets, exit_assets = await self._rpc(
                vault_contract.functions.calculateExitedAssets(
                    receiver,
                    ticket,
                    timestamp,
                    exit_queue_index,
                ).call,
                block_identifier=self.block_identifier,
            )
            return int(left_tickets), int(exited_tickets), int(exit_assets)
        except (ContractLogicError, ValueError):  # pragma: no cover - defensive
            logger.debug(
                "StakeWise calculateExitedAssets failed — receiver=%s ticket=%d index=%d",
                receiver,
                ticket,
                exit_queue_index,
            )
            return 0, 0, 0

    def _block_ranges(
        self, start_block: int, min_block: int
    ) -> Iterable[tuple[int, int]]:
        to_block = start_block
        while to_block >= min_block:
            from_block = max(min_block, to_block - self._exit_log_chunk + 1)
            yield from_block, to_block
            to_block = from_block - 1

    def _resolve_min_block(self) -> int:
        lookback_floor = max(self.block_identifier - self._exit_max_lookback_blocks, 0)
        configured_floor = max(self._exit_queue_start_block, 0)
        return max(lookback_floor, configured_floor)

    @staticmethod
    def _append_asset(
        assets: list[AssetData], address: str, amount: int, *, tvl_only: bool = False
    ) -> None:
        if amount:
            assets.append(
                AssetData(asset_address=address, amount=amount, tvl_only=tvl_only)
            )

    def _log_summary(self, user: str, exposure: ExitExposure) -> None:
        logger.debug(
            "StakeWise summary for %s — eth_collateral=%d (staked=%d, queue=%d, claimable=%d), os_liabilities=%d tickets=%d",
            user,
            exposure.eth_collateral,
            exposure.staked_eth,
            exposure.eth_in_queue,
            exposure.eth_claimable,
            exposure.os_shares_liability,
            exposure.ticket_count,
        )
