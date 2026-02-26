"""wstETH withdrawal queue adapter for tracking pending and unclaimed withdrawals.

This adapter handles wstETH withdrawal positions from the Lido withdrawal queue:
1. Queries withdrawal request IDs for a given address
2. Fetches status for each request (finalized, claimed, amounts)
3. Sums up unclaimed stETH amounts
4. Returns as ETH for pricing

The Lido withdrawal queue has a multi-step process:
- Users request withdrawal and receive an NFT (request ID)
- Withdrawals are finalized after a waiting period
- Once finalized, users can claim their stETH (which is 1:1 with ETH)
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ....abi import load_lido_withdrawal_queue_abi
from ....constants import ETH_ADDRESS
from ....logger import get_logger
from ..base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ....settings import OracleSettings

logger = get_logger(__name__)

# Default Lido Withdrawal Queue on mainnet
DEFAULT_WITHDRAWAL_QUEUE = "0x889edC2eDab5f40e902b864aD4d7AdE8E412F9B1"


@dataclass(frozen=True, slots=True)
class WithdrawalRequest:
    """Represents a single withdrawal request."""

    request_id: int
    amount_of_steth: int  # Amount of stETH to be claimed
    amount_of_shares: int  # Amount of shares
    owner: str
    timestamp: int
    is_finalized: bool  # True if withdrawal is ready
    is_claimed: bool  # True if already claimed


@dataclass(frozen=True, slots=True)
class WithdrawalExposure:
    """Aggregated withdrawal position."""

    pending_steth: int = 0  # stETH in pending (not finalized) withdrawals
    claimable_steth: int = 0  # stETH ready to claim (finalized but not claimed)
    total_requests: int = 0  # Total number of withdrawal requests

    @property
    def total_steth(self) -> int:
        """Total stETH exposure across all unclaimed withdrawals."""
        return self.pending_steth + self.claimable_steth


class WstETHWithdrawalAdapter(BaseAssetAdapter):
    """
    Adapter for wstETH withdrawal queue positions.

    This adapter:
    - Queries withdrawal request IDs for a given subvault address
    - Fetches withdrawal status for each request
    - Sums up stETH amounts where isClaimed == false
    - Returns as ETH (stETH is 1:1 with ETH for pricing purposes)

    Configuration via TOML:
    [adapters.wsteth_withdrawal]
    withdrawal_queue = "0x889edC2eDab5f40e902b864aD4d7AdE8E412F9B1"  # Optional, defaults to mainnet
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize wstETH withdrawal adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - withdrawal_queue: Lido withdrawal queue address
        """
        super().__init__(config)

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError(
                "Failed to connect to RPC for wstETH withdrawal adapter"
            )

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout

        # Load configuration
        adapter_config = config.adapters.wsteth_withdrawal

        self.withdrawal_queue = Web3.to_checksum_address(
            overrides.get("withdrawal_queue")
            or adapter_config.withdrawal_queue
            or DEFAULT_WITHDRAWAL_QUEUE
        )

        logger.info(
            "WstETH Withdrawal: initialized with queue=%s", self.withdrawal_queue
        )

        # Initialize contract
        self.queue_contract = self.w3.eth.contract(
            address=self.withdrawal_queue,
            abi=load_lido_withdrawal_queue_abi(),
        )

    @property
    def adapter_name(self) -> str:
        return "wsteth_withdrawal"

    @backoff.on_exception(
        backoff.constant,
        (ProviderConnectionError, TimeoutError),
        max_tries=3,
        interval=1,
        jitter=backoff.full_jitter,
    )
    async def _rpc(self, call, **kwargs):
        """Execute RPC call with throttling, timeout, and retry logic."""
        async with self._rpc_sem:
            if self._rpc_delay > 0:
                jitter = random.uniform(0, self._rpc_jitter) if self._rpc_jitter else 0
                await asyncio.sleep(self._rpc_delay + jitter)

            try:
                result = await asyncio.wait_for(
                    asyncio.to_thread(call, **kwargs),
                    timeout=self._rpc_timeout,
                )
                return result
            except asyncio.TimeoutError as e:
                logger.error(
                    "RPC timeout after %ds: %s", self._rpc_timeout, str(call)[:100]
                )
                raise TimeoutError(
                    f"RPC call timed out after {self._rpc_timeout}s"
                ) from e

    async def _get_withdrawal_requests(self, owner: str) -> list[int]:
        """Get withdrawal request IDs for a given owner.

        Args:
            owner: Address to query

        Returns:
            List of withdrawal request IDs
        """
        request_ids = await self._rpc(
            self.queue_contract.functions.getWithdrawalRequests(
                Web3.to_checksum_address(owner)
            ).call,
            block_identifier=self.block_number,
        )

        logger.debug(
            "WstETH Withdrawal: found %d request(s) for %s", len(request_ids), owner
        )
        return request_ids

    async def _get_withdrawal_status(
        self, request_ids: list[int]
    ) -> list[WithdrawalRequest]:
        """Get withdrawal status for given request IDs.

        Args:
            request_ids: List of request IDs to query

        Returns:
            List of WithdrawalRequest objects
        """
        if not request_ids:
            return []

        # Call getWithdrawalStatus with array of IDs
        statuses = await self._rpc(
            self.queue_contract.functions.getWithdrawalStatus(request_ids).call,
            block_identifier=self.block_number,
        )

        # Parse results into WithdrawalRequest objects
        requests = []
        for request_id, status in zip(request_ids, statuses):
            requests.append(
                WithdrawalRequest(
                    request_id=request_id,
                    amount_of_steth=status[0],  # amountOfStETH
                    amount_of_shares=status[1],  # amountOfShares
                    owner=status[2],  # owner
                    timestamp=status[3],  # timestamp
                    is_finalized=status[4],  # isFinalized
                    is_claimed=status[5],  # isClaimed
                )
            )

        logger.debug(
            "WstETH Withdrawal: fetched status for %d request(s)", len(requests)
        )
        return requests

    async def _aggregate_withdrawals(
        self, requests: list[WithdrawalRequest]
    ) -> WithdrawalExposure:
        """Aggregate withdrawal requests into total exposure.

        Args:
            requests: List of withdrawal requests

        Returns:
            Aggregated withdrawal exposure
        """
        pending_steth = 0
        claimable_steth = 0

        for req in requests:
            # Skip already claimed requests
            if req.is_claimed:
                logger.debug(
                    "WstETH Withdrawal: request %d already claimed, skipping",
                    req.request_id,
                )
                continue

            # Add to appropriate bucket based on finalization status
            if req.is_finalized:
                claimable_steth += req.amount_of_steth
                logger.debug(
                    "WstETH Withdrawal: request %d claimable: %d stETH",
                    req.request_id,
                    req.amount_of_steth,
                )
            else:
                pending_steth += req.amount_of_steth
                logger.debug(
                    "WstETH Withdrawal: request %d pending: %d stETH",
                    req.request_id,
                    req.amount_of_steth,
                )

        return WithdrawalExposure(
            pending_steth=pending_steth,
            claimable_steth=claimable_steth,
            total_requests=len([r for r in requests if not r.is_claimed]),
        )

    async def fetch_assets(
        self,
        subvault_address: str,
        previous_assets: list[AssetData] | None = None,
    ) -> list[AssetData]:
        """Fetch withdrawal queue positions for a subvault.

        This adapter is standalone - it preserves previous_assets and
        adds its own discovered assets.

        Args:
            subvault_address: Subvault address to query
            previous_assets: Assets from previous adapters (preserved)

        Returns:
            Previous assets plus unclaimed stETH as ETH
        """
        my_assets = await self.fetch_assets_for_subvault(subvault_address)

        # Preserve previous assets and add our own
        result = list(previous_assets) if previous_assets else []
        result.extend(my_assets)
        return result

    async def fetch_assets_for_subvault(
        self, subvault_address: str
    ) -> list[AssetData]:
        """Fetch withdrawal queue positions for a subvault (internal).

        Args:
            subvault_address: Subvault address to query

        Returns:
            List containing total unclaimed stETH as ETH
        """
        logger.info(
            "WstETH Withdrawal: fetching positions for subvault %s", subvault_address
        )

        # Get withdrawal request IDs
        request_ids = await self._get_withdrawal_requests(subvault_address)

        if not request_ids:
            logger.info(
                "WstETH Withdrawal: no withdrawal requests for %s", subvault_address
            )
            return []

        # Get status for all requests
        requests = await self._get_withdrawal_status(request_ids)

        # Aggregate unclaimed amounts
        exposure = await self._aggregate_withdrawals(requests)

        if exposure.total_steth == 0:
            logger.info(
                "WstETH Withdrawal: no unclaimed withdrawals for %s", subvault_address
            )
            return []

        logger.info(
            "WstETH Withdrawal: subvault %s has %d stETH in withdrawals "
            "(pending: %d, claimable: %d, requests: %d)",
            subvault_address,
            exposure.total_steth,
            exposure.pending_steth,
            exposure.claimable_steth,
            exposure.total_requests,
        )

        # Return as ETH (stETH is 1:1 with ETH for pricing)
        return [
            AssetData(
                asset_address=Web3.to_checksum_address(ETH_ADDRESS),
                amount=exposure.total_steth,
            )
        ]

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for wstETH withdrawal adapter.

        Returns:
            Empty list
        """
        logger.warning("WstETH Withdrawal: fetch_all_assets not supported")
        return []
