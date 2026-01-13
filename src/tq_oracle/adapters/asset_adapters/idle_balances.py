from __future__ import annotations

import asyncio
import random

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ...abi import (
    fetch_subvault_addresses,
    get_oracle_address_from_vault,
    load_erc20_abi,
    load_oracle_abi,
)
from ...constants import DEFAULT_ADDITIONAL_ASSETS
from ...logger import get_logger
from ...settings import OracleSettings
from .base import AssetData, BaseAssetAdapter

logger = get_logger(__name__)


class IdleBalancesAdapter(BaseAssetAdapter):
    """Adapter for querying idle balances on the vault chain."""

    eth_address: str
    usdc_address: str

    def __init__(self, config: OracleSettings):
        """Initialize the adapter.

        Args:
            config: Oracle configuration
        """
        super().__init__(config)

        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        self.block_number = config.block_number_required

        assets = config.assets
        logger.debug(f"Assets available: {assets}")
        eth_address = assets["ETH"]
        if eth_address is None:
            raise ValueError("ETH address is required for IdleBalances adapter")
        self.eth_address = eth_address
        usdc_address = assets["USDC"]
        if usdc_address is None:
            raise ValueError("USDC address is required for IdleBalances adapter")
        self.usdc_address = usdc_address

        idle_cfg = config.adapters.idle_balances

        default_additional_raw = (
            DEFAULT_ADDITIONAL_ASSETS.get(config.network.value, {})
            if config.additional_asset_support
            else {}
        )
        extra_additional_raw = (
            idle_cfg.extra_tokens if config.additional_asset_support else {}
        )
        non_tvl_raw = idle_cfg.non_tvl_tokens if config.additional_asset_support else {}

        self._default_additional_assets: list[str] = [
            self.w3.to_checksum_address(address)
            for address in default_additional_raw.values()
            if address
        ]
        self._extra_additional_assets_by_symbol: dict[str, str] = {
            symbol: self.w3.to_checksum_address(address)
            for symbol, address in extra_additional_raw.items()
            if address
        }
        self._extra_additional_assets: list[str] = list(
            self._extra_additional_assets_by_symbol.values()
        )
        self._non_tvl_assets_by_symbol: dict[str, str] = {
            symbol: self.w3.to_checksum_address(address)
            for symbol, address in non_tvl_raw.items()
            if address
        }
        self._non_tvl_assets: list[str] = list(self._non_tvl_assets_by_symbol.values())
        self._additional_assets: list[str] = [
            *self._default_additional_assets,
            *self._extra_additional_assets,
        ]
        # Additional tokens (defaults + extras) are marked tvl_only to avoid conflicts with other adapters
        # non_tvl_tokens are NOT marked as tvl_only
        self._additional_asset_lookup: set[str] = {
            addr.lower() for addr in self._additional_assets
        }
        if self._additional_assets:
            logger.debug(
                "Idle balances additional tokens configured: defaults=%s extra=%s",
                self._default_additional_assets,
                self._extra_additional_assets_by_symbol,
            )
        if self._non_tvl_assets:
            logger.debug(
                "Idle balances non-tvl tokens configured: %s",
                self._non_tvl_assets_by_symbol,
            )

        extra_address_candidates = (
            [
                self.w3.to_checksum_address(address)
                for address in idle_cfg.extra_addresses
                if address
            ]
            if config.additional_asset_support
            else []
        )

        deduped_addresses: dict[str, str] = {}
        for checksum in extra_address_candidates:
            deduped_addresses.setdefault(checksum.lower(), checksum)

        self._extra_addresses = list(deduped_addresses.values())
        self._extra_addresses_lookup = set(deduped_addresses.keys())
        if self._extra_addresses:
            logger.debug(
                "Idle balances extra addresses configured: %s",
                self._extra_addresses,
            )

        self._rpc_sem = asyncio.Semaphore(getattr(self.config, "max_calls", 5))
        self._rpc_delay = getattr(self.config, "rpc_delay", 0.15)  # seconds
        self._rpc_jitter = getattr(self.config, "rpc_jitter", 0.10)  # seconds

    @backoff.on_exception(
        backoff.expo, (ProviderConnectionError), max_time=30, jitter=backoff.full_jitter
    )
    async def _rpc(self, fn, *args, **kwargs):
        """Throttle + backoff a single RPC."""
        async with self._rpc_sem:
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            finally:
                delay = self._rpc_delay + random.random() * self._rpc_jitter
                if delay > 0:
                    await asyncio.sleep(delay)

    @property
    def adapter_name(self) -> str:
        return "idle_balances"

    async def fetch_assets(self, subvault_address: str, previous_assets: list[AssetData] | None = None) -> list[AssetData]:
        """Fetch idle balances for the given subvault on the configured chain.

        Args:
            subvault_address: The specific subvault contract address to query

        Returns:
            List of AssetData objects containing asset addresses and balances
        """
        supported_assets = await self._fetch_supported_assets()

        logger.debug(
            "Fetching L1 balances for subvault %s across %d assets",
            subvault_address,
            len(supported_assets),
        )

        asset_tasks = [
            self._fetch_asset_balance(
                self.w3,
                subvault_address,
                asset_addr,
                asset_addr.lower() in self._additional_asset_lookup,
            )
            for asset_addr in supported_assets
        ]

        asset_results = await asyncio.gather(*asset_tasks, return_exceptions=True)

        assets: list[AssetData] = []
        for asset_addr, result in zip(supported_assets, asset_results):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to fetch balance for asset %s in subvault %s: %s",
                    asset_addr,
                    subvault_address,
                    result,
                )
            elif isinstance(result, AssetData):
                assets.append(result)

        logger.debug("Fetched %d L1 asset balances for subvault", len(assets))
        return assets

    async def fetch_all_assets(self) -> list[AssetData]:
        """Fetch idle balances for the main vault and ALL subvaults on the configured chain.

        This method discovers all subvaults and fetches idle balances for each,
        including the main vault contract itself.
        Use this for the default idle_balances collection on L1.

        Returns:
            List of AssetData objects from main vault and all subvaults
        """
        subvault_addresses = await self._fetch_subvault_addresses()
        vault_addresses = [self.config.vault_address_required] + subvault_addresses
        seen_addresses = {addr.lower() for addr in vault_addresses}
        for extra_address in self._extra_addresses:
            normalized = extra_address.lower()
            if normalized not in seen_addresses:
                vault_addresses.append(extra_address)
                seen_addresses.add(normalized)
        logger.info(
            "Fetching vault-chain idle balances for main vault + %d subvaults + %d extra addresses",
            len(subvault_addresses),
            len(vault_addresses) - 1 - len(subvault_addresses),
        )

        asset_results = await asyncio.gather(
            *[self.fetch_assets(addr) for addr in vault_addresses],
            return_exceptions=True,
        )

        all_assets: list[AssetData] = []
        failed_vaults: list[tuple[str, Exception]] = []

        for vault_addr, result in zip(vault_addresses, asset_results):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to fetch idle balances for vault/subvault %s: %s",
                    vault_addr,
                    result,
                )
                failed_vaults.append((vault_addr, result))
            elif isinstance(result, list):
                all_assets.extend(result)

        if failed_vaults:
            vault_list = ", ".join(addr for addr, _ in failed_vaults)
            raise ValueError(
                f"Failed to fetch assets from {len(failed_vaults)} vault(s): {vault_list}"
            )

        logger.info(
            "Fetched %d total idle balance entries from main vault + %d subvaults",
            len(all_assets),
            len(subvault_addresses),
        )
        return all_assets

    async def _fetch_contract_list(
        self,
        contract_address: str,
        abi: list,
        count_function: str,
        item_function: str,
        item_type: str,
    ) -> list[str]:
        """Generic method to fetch a list of items from a contract.

        Args:
            contract_address: The contract address to query
            abi: The contract ABI
            count_function: Name of the function that returns the count
            item_function: Name of the function that returns an item at index
            item_type: Description for logging (e.g., "subvault", "supported asset")

        Returns:
            List of addresses fetched from the contract
        """
        checksum_address = self.w3.to_checksum_address(contract_address)
        logger.debug("Fetching %ss from contract: %s", item_type, checksum_address)

        contract = self.w3.eth.contract(address=checksum_address, abi=abi)
        count = await self._rpc(
            getattr(contract.functions, count_function)().call,
            block_identifier=self.block_number,
        )
        logger.debug("Found %d %ss", count, item_type)

        async def fetch_item_at(index: int) -> str:
            item: str = await self._rpc(
                getattr(contract.functions, item_function)(index).call,
                block_identifier=self.block_number,
            )
            logger.debug("%s %d: %s", item_type.capitalize(), index, item)
            return item

        item_results = await asyncio.gather(
            *[fetch_item_at(i) for i in range(count)], return_exceptions=True
        )

        items: list[str] = []
        for index, result in enumerate(item_results):
            if isinstance(result, Exception):
                logger.error(
                    "Failed to fetch %s at index %d from contract %s: %s",
                    item_type,
                    index,
                    checksum_address,
                    result,
                )
            elif isinstance(result, str):
                items.append(result)

        logger.debug("Retrieved %d %ss", len(items), item_type)
        return items

    async def _fetch_subvault_addresses(self) -> list[str]:
        """Get the subvault addresses for the given vault."""
        return await fetch_subvault_addresses(self.config)

    async def _fetch_supported_assets(self) -> list[str]:
        """Get the supported assets for the given vault."""
        oracle_abi = load_oracle_abi()
        oracle_address = get_oracle_address_from_vault(self.config)
        raw_assets = await self._fetch_contract_list(
            contract_address=oracle_address,
            abi=oracle_abi,
            count_function="supportedAssets",
            item_function="supportedAssetAt",
            item_type="supported asset",
        )

        base_assets: list[str] = []
        for asset in raw_assets:
            try:
                checksum = self.w3.to_checksum_address(asset)
            except (TypeError, ValueError):
                logger.warning(
                    "Skipping unsupported asset address %r from oracle contract",
                    asset,
                )
                continue
            base_assets.append(checksum)
        return self._merge_supported_assets(base_assets)

    def _merge_supported_assets(self, base_assets: list[str]) -> list[str]:
        """Merge contract supported assets with configured additional assets."""
        combined: list[str] = []
        seen: set[str] = set()

        base_lookup = {addr.lower() for addr in base_assets if isinstance(addr, str)}
        effective_defaults = [
            addr
            for addr in self._default_additional_assets
            if isinstance(addr, str) and addr.lower() not in base_lookup
        ]
        effective_extras = [
            addr
            for addr in self._extra_additional_assets
            if isinstance(addr, str) and addr.lower() not in base_lookup
        ]
        effective_non_tvl = [
            addr
            for addr in self._non_tvl_assets
            if isinstance(addr, str) and addr.lower() not in base_lookup
        ]
        effective_additional = [*effective_defaults, *effective_extras]
        self._additional_asset_lookup = {addr.lower() for addr in effective_additional}

        for address in base_assets:
            if not isinstance(address, str):
                continue
            normalized = address.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            combined.append(address)

        for address in effective_additional:
            normalized = address.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            combined.append(address)

        for address in effective_non_tvl:
            normalized = address.lower()
            if normalized in seen:
                continue
            seen.add(normalized)
            combined.append(address)

        if effective_additional:
            logger.debug(
                "Idle balances applying additional tokens (tvl_only): extras=%s defaults=%s",
                [addr for addr in effective_extras],
                [addr for addr in effective_defaults],
            )

        if effective_non_tvl:
            logger.debug(
                "Idle balances applying non-tvl tokens: %s",
                [addr for addr in effective_non_tvl],
            )

        return combined

    async def _fetch_asset_balance(
        self,
        w3: Web3,
        subvault_address: str,
        asset_address: str,
        tvl_only: bool = False,
    ) -> AssetData:
        """Fetch the balance of an asset for the given subvault."""
        checksum_subvault_address = w3.to_checksum_address(subvault_address)

        if asset_address == self.eth_address:
            balance = await self._rpc(
                w3.eth.get_balance,
                checksum_subvault_address,
                block_identifier=self.block_number,
            )
        else:
            erc20_abi = load_erc20_abi()
            checksum_asset_address = w3.to_checksum_address(asset_address)
            erc20_contract = w3.eth.contract(
                address=checksum_asset_address, abi=erc20_abi
            )
            balance = await self._rpc(
                erc20_contract.functions.balanceOf(checksum_subvault_address).call,
                block_identifier=self.block_number,
            )

        return AssetData(
            asset_address=asset_address,
            amount=balance,
            tvl_only=tvl_only,
        )
