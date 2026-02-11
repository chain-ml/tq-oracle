"""sUSDe cooldown adapter for tracking USDe in unstaking queue.

This adapter handles sUSDe cooldown positions:
1. Queries cooldown state for a given address
2. Returns the underlyingAmount (USDe) in cooldown
3. Works alongside the ERC4626 adapter which tracks active sUSDe

The sUSDe contract has a cooldown mechanism where users must wait
before claiming their underlying USDe after initiating unstaking.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ....abi import load_susde_cooldown_abi
from ....logger import get_logger
from ..base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ....settings import OracleSettings

logger = get_logger(__name__)

# Default sUSDe token on mainnet
DEFAULT_SUSDE_TOKEN = "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"
# Default USDe token on mainnet
DEFAULT_USDE_TOKEN = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"


@dataclass(frozen=True, slots=True)
class CooldownState:
    """Represents sUSDe cooldown state for an address."""

    cooldown_end: int  # Timestamp when cooldown ends (0 if no cooldown)
    underlying_amount: int  # Amount of USDe in cooldown/claimable
    is_claimable: bool  # True if cooldown has ended


class SUSDeCooldownAdapter(BaseAssetAdapter):
    """
    Adapter for sUSDe cooldown positions.

    This adapter:
    - Queries cooldown state via cooldowns(address)
    - Returns underlyingAmount as USDe (both pending and claimable)
    - Works alongside ERC4626 adapter which handles active sUSDe balance

    Configuration via TOML:
    [adapters.susde_cooldown]
    susde_token = "0x9D39A5DE30e57443BfF2A8307A4256c8797A3497"  # Optional
    usde_token = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"   # Optional
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize sUSDe cooldown adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - susde_token: sUSDe token address
                - usde_token: USDe underlying token address
        """
        super().__init__(config)

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError(
                "Failed to connect to RPC for sUSDe cooldown adapter"
            )

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout

        # Load configuration
        adapter_config = config.adapters.susde_cooldown

        self.susde_token = Web3.to_checksum_address(
            overrides.get("susde_token")
            or adapter_config.susde_token
            or DEFAULT_SUSDE_TOKEN
        )

        self.usde_token = Web3.to_checksum_address(
            overrides.get("usde_token")
            or adapter_config.usde_token
            or DEFAULT_USDE_TOKEN
        )

        logger.info(
            "sUSDe Cooldown: initialized with susde=%s, usde=%s",
            self.susde_token,
            self.usde_token,
        )

        # Initialize contract
        self.susde_contract = self.w3.eth.contract(
            address=self.susde_token,
            abi=load_susde_cooldown_abi(),
        )

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

    async def _get_cooldown_state(self, account: str) -> CooldownState:
        """Get cooldown state for an account.

        Args:
            account: Address to query

        Returns:
            CooldownState with cooldown info
        """
        cooldown_data = await self._rpc(
            self.susde_contract.functions.cooldowns(
                Web3.to_checksum_address(account)
            ).call,
            block_identifier=self.block_number,
        )

        # Parse response: (uint104 cooldownEnd, uint152 underlyingAmount)
        cooldown_end = cooldown_data[0]
        underlying_amount = cooldown_data[1]

        # Get current block timestamp to check if claimable
        block_info = self.w3.eth.get_block(self.block_number)
        current_timestamp = block_info["timestamp"]

        is_claimable = cooldown_end > 0 and cooldown_end <= current_timestamp

        logger.debug(
            "sUSDe Cooldown: account=%s, cooldownEnd=%d, underlyingAmount=%d, isClaimable=%s",
            account,
            cooldown_end,
            underlying_amount,
            is_claimable,
        )

        return CooldownState(
            cooldown_end=cooldown_end,
            underlying_amount=underlying_amount,
            is_claimable=is_claimable,
        )

    async def fetch_assets_for_subvault(
        self, subvault_address: str
    ) -> list[AssetData]:
        """Fetch sUSDe cooldown position for a subvault.

        Args:
            subvault_address: Subvault address to query

        Returns:
            List containing USDe amount in cooldown (if any)
        """
        logger.info(
            "sUSDe Cooldown: fetching cooldown for subvault %s", subvault_address
        )

        # Get cooldown state
        cooldown = await self._get_cooldown_state(subvault_address)

        if cooldown.underlying_amount == 0:
            logger.info(
                "sUSDe Cooldown: no USDe in cooldown for %s", subvault_address
            )
            return []

        # Log status
        if cooldown.is_claimable:
            logger.info(
                "sUSDe Cooldown: subvault %s has %d USDe CLAIMABLE (cooldown ended)",
                subvault_address,
                cooldown.underlying_amount,
            )
        else:
            logger.info(
                "sUSDe Cooldown: subvault %s has %d USDe PENDING (cooldown ends at %d)",
                subvault_address,
                cooldown.underlying_amount,
                cooldown.cooldown_end,
            )

        # Return as USDe
        return [
            AssetData(
                asset_address=Web3.to_checksum_address(self.usde_token),
                amount=cooldown.underlying_amount,
            )
        ]

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for sUSDe cooldown adapter.

        Returns:
            Empty list
        """
        logger.warning("sUSDe Cooldown: fetch_all_assets not supported")
        return []
