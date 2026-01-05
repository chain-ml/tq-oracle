"""Aave V3 asset adapter for collecting supply and borrow positions."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ...abi import load_erc20_abi
from ...constants import (
    AAVE_V3_BORROW_TOKENS_MAINNET,
    AAVE_V3_POOL_MAINNET,
    AAVE_V3_SUPPLY_TOKENS_MAINNET,
)
from ...logger import get_logger
from ...settings import Network
from .base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ...settings import OracleSettings

logger = get_logger(__name__)


class AaveV3Adapter(BaseAssetAdapter):
    """
    Collect Aave V3 supply (aTokens) and variable borrow balances
    for a given subvault address.

    This adapter:
    - Queries aToken balances (supply positions) as positive amounts
    - Queries variable debt token balances (borrow positions) as negative amounts
    - Supports per-chain configuration via TOML
    - Can be configured per-subvault with adapter_overrides
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize Aave V3 adapter with configuration.

        Args:
            config: Oracle settings containing network and RPC configuration
            **overrides: Optional overrides for adapter-specific settings
                - pool_address: Aave V3 pool address (if different from default)
                - supply_tokens: Dict of token symbols to aToken addresses
                - borrow_tokens: Dict of token symbols to variable debt token addresses
                - base_asset_type: 'eth' or 'usd' for asset denomination
        """
        super().__init__(config)

        # Skip adapter if not on mainnet (for now)
        self._skip = config.network != Network.MAINNET
        if self._skip:
            logger.info(
                "Skipping Aave V3 adapter: network=%s (only mainnet supported)",
                config.network.value,
            )
            return

        # Initialize Web3 connection
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for Aave V3 adapter")

        self.block_number = config.block_number_required

        # RPC throttling configuration
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter

        # Load adapter configuration with overrides
        adapter_config = config.adapters.aave_v3

        # Pool address
        self.pool_address = overrides.get(
            "pool_address", adapter_config.pool_address or AAVE_V3_POOL_MAINNET
        )

        # Supply tokens (aTokens)
        if "supply_tokens" in overrides:
            self.supply_tokens = overrides["supply_tokens"]
        elif adapter_config.supply_tokens:
            self.supply_tokens = adapter_config.supply_tokens
        else:
            self.supply_tokens = AAVE_V3_SUPPLY_TOKENS_MAINNET

        # Borrow tokens (variable debt)
        if "borrow_tokens" in overrides:
            self.borrow_tokens = overrides["borrow_tokens"]
        elif adapter_config.borrow_tokens:
            self.borrow_tokens = adapter_config.borrow_tokens
        else:
            self.borrow_tokens = AAVE_V3_BORROW_TOKENS_MAINNET

        # Base asset type
        self.base_asset_type = overrides.get(
            "base_asset_type", adapter_config.base_asset_type
        )

        logger.debug(
            "Aave V3 adapter initialized: pool=%s, supply_tokens=%d, borrow_tokens=%d, base_asset=%s",
            self.pool_address,
            len(self.supply_tokens),
            len(self.borrow_tokens),
            self.base_asset_type,
        )

    @property
    def adapter_name(self) -> str:
        """Return the adapter identifier."""
        return "aave_v3"

    @backoff.on_exception(
        backoff.expo,
        (ProviderConnectionError,),
        max_time=30,
        jitter=backoff.full_jitter,
    )
    async def _rpc(self, fn, *args, **kwargs):
        """Execute RPC call with throttling and retry logic."""
        async with self._rpc_sem:
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            finally:
                delay = self._rpc_delay + random.random() * self._rpc_jitter
                if delay > 0:
                    await asyncio.sleep(delay)

    async def _balance_of(self, token: str, owner: str) -> int:
        """Query ERC20 balance for a token and owner.

        Args:
            token: Token contract address (aToken or debt token)
            owner: Owner address (subvault)

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

    async def fetch_assets(self, subvault_address: str) -> list[AssetData]:
        """Fetch Aave V3 positions for a specific subvault.

        This method:
        1. Queries all configured aToken (supply) balances
        2. Queries all configured variable debt token (borrow) balances
        3. Returns supply as positive amounts and borrows as negative amounts

        Args:
            subvault_address: The subvault address to query

        Returns:
            List of AssetData with positive amounts for supply and negative for borrows
        """
        if self._skip:
            return []

        results: list[AssetData] = []

        # Fetch supply positions (aTokens)
        supply_tasks = []
        for symbol, token_address in self.supply_tokens.items():
            supply_tasks.append((symbol, token_address, self._balance_of(token_address, subvault_address)))

        for symbol, token_address, balance_coro in supply_tasks:
            balance = await balance_coro
            if balance > 0:
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(token_address),
                        amount=balance,
                    )
                )
                logger.debug(
                    "Aave V3: %s supply balance for %s: %d",
                    symbol,
                    subvault_address,
                    balance,
                )

        # Fetch borrow positions (variable debt tokens)
        borrow_tasks = []
        for symbol, token_address in self.borrow_tokens.items():
            borrow_tasks.append((symbol, token_address, self._balance_of(token_address, subvault_address)))

        for symbol, token_address, balance_coro in borrow_tasks:
            balance = await balance_coro
            if balance > 0:
                # Borrows are represented as NEGATIVE amounts
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(token_address),
                        amount=-balance,
                    )
                )
                logger.debug(
                    "Aave V3: %s borrow balance for %s: %d (stored as negative)",
                    symbol,
                    subvault_address,
                    balance,
                )

        logger.info(
            "Aave V3: fetched %d positions for subvault %s",
            len(results),
            subvault_address,
        )
        return results

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for Aave V3.

        Aave V3 adapter is designed to work per-subvault only.
        Use the per-subvault configuration in subvault_adapters TOML section.

        Returns:
            Empty list (not supported)
        """
        logger.debug("Aave V3 adapter does not support global asset fetching")
        return []
