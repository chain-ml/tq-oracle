"""sNUSD adapter with redemption queue tracking.

This adapter handles sNUSD positions including:
1. Active sNUSD balance (converted to nUSD via convertToAssets)
2. Pending redemptions in cooldown (10-day waiting period)
3. Claimable nUSD after cooldown ends

The sNUSD contract has a cooldown mechanism where users must wait 10 days
before claiming their underlying nUSD.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ....abi import load_erc20_abi, load_snusd_abi
from ....logger import get_logger
from ..base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ....settings import OracleSettings

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CooldownState:
    """Represents a user's cooldown state in sNUSD."""

    cooldown_end: int  # Timestamp when cooldown ends
    underlying_amount: int  # Amount of nUSD to be claimed
    is_claimable: bool  # True if cooldown has ended


@dataclass(frozen=True, slots=True)
class SNUSDExposure:
    """Aggregated sNUSD position."""

    active_nusd: int = 0  # Active sNUSD converted to nUSD
    pending_nusd: int = 0  # nUSD waiting in cooldown
    claimable_nusd: int = 0  # nUSD ready to claim

    @property
    def total_nusd(self) -> int:
        """Total nUSD exposure across all states."""
        return self.active_nusd + self.pending_nusd + self.claimable_nusd


class SNUSDAdapter(BaseAssetAdapter):
    """
    Adapter for sNUSD positions including redemption queue tracking.

    This adapter:
    - Queries sNUSD token balance and converts to nUSD
    - Checks cooldown state for pending redemptions
    - Tracks both pending (in cooldown) and claimable nUSD
    - Returns total nUSD exposure for pricing

    Configuration via TOML:
    [adapters.snusd]
    snusd_token = "0x..."  # sNUSD token address
    nusd_token = "0x..."   # nUSD underlying token address
    cooldown_period = 864000  # 10 days in seconds (optional, for validation)
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize sNUSD adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - snusd_token: sNUSD token address
                - nusd_token: nUSD underlying token address
                - cooldown_period: Cooldown period in seconds (for logging/validation)
        """
        super().__init__(config)

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for sNUSD adapter")

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter

        # Load configuration
        adapter_config = config.adapters.snusd

        snusd_token = overrides.get("snusd_token") or adapter_config.snusd_token
        nusd_token = overrides.get("nusd_token") or adapter_config.nusd_token
        self.cooldown_period = (
            overrides.get("cooldown_period") or adapter_config.cooldown_period or 864000
        )

        if not snusd_token:
            raise ValueError("sNUSD adapter requires snusd_token configuration")
        if not nusd_token:
            raise ValueError("sNUSD adapter requires nusd_token configuration")

        self.snusd_token = self.w3.to_checksum_address(snusd_token)
        self.nusd_token = self.w3.to_checksum_address(nusd_token)

        # Get current block timestamp for cooldown calculations
        self._block_timestamp_cache: int | None = None

        logger.debug(
            "sNUSD adapter initialized: snusd=%s, nusd=%s, cooldown=%ds",
            self.snusd_token,
            self.nusd_token,
            self.cooldown_period,
        )

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "snusd"

    @backoff.on_exception(
        backoff.expo,
        (ProviderConnectionError,),
        max_time=30,
        jitter=backoff.full_jitter,
    )
    async def _rpc(self, fn, *args, **kwargs):
        """Execute RPC call with throttling and retry."""
        async with self._rpc_sem:
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            finally:
                delay = self._rpc_delay + random.random() * self._rpc_jitter
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _get_block_timestamp(self) -> int:
        """Get timestamp of current block."""
        if self._block_timestamp_cache is not None:
            return self._block_timestamp_cache

        block = await self._rpc(self.w3.eth.get_block, self.block_number)
        timestamp = int(block["timestamp"])
        self._block_timestamp_cache = timestamp
        return timestamp

    async def _balance_of(self, token: str, owner: str) -> int:
        """Query ERC20 balance.

        Args:
            token: Token address
            owner: Owner address

        Returns:
            Balance in native token units
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(token),
            abi=load_erc20_abi(),
        )
        return await self._rpc(
            contract.functions.balanceOf(Web3.to_checksum_address(owner)).call,
            block_identifier=self.block_number,
        )

    async def _convert_to_assets(self, shares: int) -> int:
        """Convert sNUSD shares to nUSD assets.

        Args:
            shares: Number of sNUSD shares

        Returns:
            Amount of nUSD
        """
        contract = self.w3.eth.contract(
            address=self.snusd_token,
            abi=load_snusd_abi(),
        )
        assets = await self._rpc(
            contract.functions.convertToAssets(shares).call,
            block_identifier=self.block_number,
        )
        return int(assets)

    async def _get_cooldown_state(self, account: str) -> CooldownState:
        """Get cooldown state for an account.

        Args:
            account: Account address to check

        Returns:
            CooldownState with cooldown end time and underlying amount
        """
        contract = self.w3.eth.contract(
            address=self.snusd_token,
            abi=load_snusd_abi(),
        )
        cooldown_end, underlying_amount = await self._rpc(
            contract.functions.cooldowns(Web3.to_checksum_address(account)).call,
            block_identifier=self.block_number,
        )

        # Check if cooldown has ended
        block_timestamp = await self._get_block_timestamp()
        is_claimable = cooldown_end > 0 and block_timestamp >= cooldown_end

        return CooldownState(
            cooldown_end=int(cooldown_end),
            underlying_amount=int(underlying_amount),
            is_claimable=is_claimable,
        )

    async def _compute_exposure(
        self,
        active_balance: int,
        cooldown: CooldownState,
    ) -> SNUSDExposure:
        """Compute total sNUSD exposure.

        Args:
            active_balance: Active sNUSD balance in nUSD
            cooldown: Cooldown state

        Returns:
            SNUSDExposure with breakdown of active, pending, and claimable
        """
        pending_nusd = 0
        claimable_nusd = 0

        if cooldown.underlying_amount > 0:
            if cooldown.is_claimable:
                claimable_nusd = cooldown.underlying_amount
            else:
                pending_nusd = cooldown.underlying_amount

        return SNUSDExposure(
            active_nusd=active_balance,
            pending_nusd=pending_nusd,
            claimable_nusd=claimable_nusd,
        )

    async def fetch_assets(
        self, subvault_address: str, previous_assets: list[AssetData] | None = None
    ) -> list[AssetData]:
        """Fetch sNUSD positions for a subvault.

        This method:
        1. Queries sNUSD balance and converts to nUSD
        2. Checks cooldown state for pending/claimable redemptions
        3. Returns total nUSD exposure for pricing

        Args:
            subvault_address: Subvault address to query

        Returns:
            List of AssetData with total nUSD exposure
        """
        user = self.w3.to_checksum_address(subvault_address)
        logger.info(
            "sNUSD adapter collecting balances — user=%s block=%s",
            user,
            self.block_number,
        )

        # Get active sNUSD balance
        snusd_balance = await self._balance_of(self.snusd_token, user)

        # Convert to nUSD
        active_nusd = 0
        if snusd_balance > 0:
            active_nusd = await self._convert_to_assets(snusd_balance)

        # Get cooldown state
        cooldown = await self._get_cooldown_state(user)

        # Compute exposure
        exposure = await self._compute_exposure(active_nusd, cooldown)

        # Log details
        if cooldown.cooldown_end > 0:
            block_timestamp = await self._get_block_timestamp()
            remaining_seconds = max(0, cooldown.cooldown_end - block_timestamp)
            logger.info(
                "sNUSD cooldown state — cooldownEnd=%d, underlyingAmount=%d, "
                "remainingSeconds=%d, isClaimable=%s",
                cooldown.cooldown_end,
                cooldown.underlying_amount,
                remaining_seconds,
                cooldown.is_claimable,
            )

        logger.info(
            "sNUSD summary for %s — total_nusd=%d (active=%d, pending=%d, claimable=%d)",
            user,
            exposure.total_nusd,
            exposure.active_nusd,
            exposure.pending_nusd,
            exposure.claimable_nusd,
        )

        # Return total nUSD exposure
        assets: list[AssetData] = []
        if exposure.total_nusd > 0:
            assets.append(
                AssetData(
                    asset_address=self.nusd_token,
                    amount=exposure.total_nusd,
                )
            )

        logger.info("sNUSD returned %d assets for %s", len(assets), user)
        return assets

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for sNUSD.

        sNUSD adapter works per-subvault only.

        Returns:
            Empty list
        """
        logger.debug("sNUSD adapter does not support global asset fetching")
        return []
