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

        logger.info("Initializing Aave V3 adapter...")

        # Load adapter configuration with overrides
        # Note: adapter_config may be a list when using multi-instance configuration
        # In that case, instance-specific config is passed via overrides from pipeline
        logger.debug("Loading adapter configuration (overrides: %s)", bool(overrides))
        raw_config = config.adapters.aave_v3

        # Handle list config (multi-instance) - get first or use empty config
        if isinstance(raw_config, list):
            # When using multi-instance, config is passed via overrides
            # Use first config as fallback, or empty config if list is empty
            from ...settings import AaveV3AdapterSettings

            adapter_config = raw_config[0] if raw_config else AaveV3AdapterSettings()
            logger.debug(
                "Multi-instance config detected, using overrides or first config"
            )
        else:
            adapter_config = raw_config

        # Instance name for multi-pool tracking - set early so it's always available
        self.instance_name = overrides.get("name", adapter_config.name)
        logger.debug("Instance name: %s", self.instance_name)

        # Skip adapter if not on mainnet (for now)
        self._skip = config.network != Network.MAINNET
        if self._skip:
            logger.info(
                "Skipping Aave V3 adapter: network=%s (only mainnet supported)",
                config.network.value,
            )
            return

        logger.debug("Network check passed, initializing Web3...")

        # Initialize Web3 connection
        logger.debug("RPC URL: %s", config.vault_rpc_required)
        try:
            self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
            logger.debug("Web3 provider created, checking connection...")
            if not self.w3.is_connected():
                raise ConnectionError("Failed to connect to RPC for Aave V3 adapter")
            logger.debug("Web3 connection verified")
        except Exception as e:
            logger.error(
                "Failed to initialize Web3: %s (type: %s)",
                str(e),
                type(e).__name__,
                exc_info=True,
            )
            raise

        logger.debug("Web3 connected, block_number=%s", config.block_number)
        self.block_number = config.block_number or "latest"
        logger.debug("Using block_number: %s", self.block_number)

        # RPC throttling configuration
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout
        logger.debug(
            "RPC throttling configured: max_concurrent=%d, delay=%s, jitter=%s, timeout=%s",
            config.rpc_max_concurrent_calls,
            self._rpc_delay,
            self._rpc_jitter,
            self._rpc_timeout,
        )

        # Pool address
        self.pool_address = overrides.get(
            "pool_address", adapter_config.pool_address or AAVE_V3_POOL_MAINNET
        )
        logger.debug("Pool address: %s", self.pool_address)

        # Supply tokens (aTokens)
        if "supply_tokens" in overrides:
            self.supply_tokens = overrides["supply_tokens"]
            logger.debug("Using override supply_tokens: %s", self.supply_tokens)
        elif adapter_config.supply_tokens:
            self.supply_tokens = adapter_config.supply_tokens
            logger.debug("Using config supply_tokens: %s", self.supply_tokens)
        else:
            self.supply_tokens = AAVE_V3_SUPPLY_TOKENS_MAINNET
            logger.debug("Using default supply_tokens: %s", self.supply_tokens)

        # Borrow tokens (variable debt)
        if "borrow_tokens" in overrides:
            self.borrow_tokens = overrides["borrow_tokens"]
            logger.debug("Using override borrow_tokens: %s", self.borrow_tokens)
        elif adapter_config.borrow_tokens:
            self.borrow_tokens = adapter_config.borrow_tokens
            logger.debug("Using config borrow_tokens: %s", self.borrow_tokens)
        else:
            self.borrow_tokens = AAVE_V3_BORROW_TOKENS_MAINNET
            logger.debug("Using default borrow_tokens: %s", self.borrow_tokens)

        # Base asset type
        self.base_asset_type = overrides.get(
            "base_asset_type", adapter_config.base_asset_type
        )
        logger.debug("Base asset type: %s", self.base_asset_type)

        # Validate token configuration (FYEO-TQO-03)
        self._validate_tokens_config()

        logger.info(
            "Aave V3 adapter initialization complete: pool=%s, supply_tokens=%d, borrow_tokens=%d, base_asset=%s, instance=%s",
            self.pool_address,
            len(self.supply_tokens),
            len(self.borrow_tokens),
            self.base_asset_type,
            self.instance_name or "default",
        )

    def _validate_tokens_config(self) -> None:
        """Validate token configuration for duplicates."""
        # Check supply tokens for duplicates
        seen_supply: dict[str, str] = {}  # address -> symbol
        for symbol, address in self.supply_tokens.items():
            addr_lower = address.lower()
            if addr_lower in seen_supply:
                raise ValueError(
                    f"Duplicate supply token address {address} "
                    f"for symbols '{seen_supply[addr_lower]}' and '{symbol}'"
                )
            seen_supply[addr_lower] = symbol

        # Check borrow tokens for duplicates
        seen_borrow: dict[str, str] = {}  # address -> symbol
        for symbol, address in self.borrow_tokens.items():
            addr_lower = address.lower()
            if addr_lower in seen_borrow:
                raise ValueError(
                    f"Duplicate borrow token address {address} "
                    f"for symbols '{seen_borrow[addr_lower]}' and '{symbol}'"
                )
            seen_borrow[addr_lower] = symbol

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
        """Execute RPC call with throttling, timeout, and retry (FYEO-TQO-09)."""
        async with self._rpc_sem:
            result = await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs),
                timeout=self._rpc_timeout,
            )
        # Sleep outside semaphore to not block other tasks
        delay = self._rpc_delay + random.random() * self._rpc_jitter
        if delay > 0:
            await asyncio.sleep(delay)
        return result

    async def _balance_of(self, token: str, owner: str) -> int:
        """Query ERC20 balance for a token and owner.

        Args:
            token: Token contract address (aToken or debt token)
            owner: Owner address (subvault)

        Returns:
            Balance in native token units
        """
        try:
            logger.debug("Querying balance: token=%s, owner=%s", token, owner)
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token),
                abi=load_erc20_abi(),
            )
            balance = await self._rpc(
                contract.functions.balanceOf(Web3.to_checksum_address(owner)).call,
                block_identifier=self.block_number,
            )
            logger.debug("Balance query successful: %d", balance)
            return balance
        except Exception as e:
            logger.error(
                "Failed to query balance for token=%s, owner=%s: %s (type: %s)",
                token,
                owner,
                str(e),
                type(e).__name__,
                exc_info=True,
            )
            raise

    async def _get_underlying_asset(self, atoken_or_debt_token: str) -> str:
        """Get the underlying asset address from an aToken or debt token.

        Args:
            atoken_or_debt_token: aToken or variable debt token address

        Returns:
            Underlying asset address (e.g., WETH, USDC)
        """
        try:
            logger.debug(
                "Querying underlying asset for token: %s", atoken_or_debt_token
            )
            # Aave V3 aTokens and debt tokens have UNDERLYING_ASSET_ADDRESS() function
            abi = [
                {
                    "inputs": [],
                    "name": "UNDERLYING_ASSET_ADDRESS",
                    "outputs": [
                        {"internalType": "address", "name": "", "type": "address"}
                    ],
                    "stateMutability": "view",
                    "type": "function",
                }
            ]
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(atoken_or_debt_token),
                abi=abi,
            )
            underlying = await self._rpc(
                contract.functions.UNDERLYING_ASSET_ADDRESS().call,
                block_identifier=self.block_number,
            )
            logger.debug("Underlying asset query successful: %s", underlying)
            return underlying
        except Exception as e:
            logger.error(
                "Failed to query underlying asset for token=%s: %s (type: %s)",
                atoken_or_debt_token,
                str(e),
                type(e).__name__,
                exc_info=True,
            )
            raise

    async def fetch_assets(
        self, subvault_address: str, previous_assets: list[AssetData] | None = None
    ) -> list[AssetData]:
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
        instance_label = f" ({self.instance_name})" if self.instance_name else ""
        logger.info(
            "Aave V3%s fetch_assets called for subvault %s",
            instance_label,
            subvault_address,
        )

        if self._skip:
            logger.debug("Adapter is skipped, returning empty list")
            return []

        logger.debug("Starting to fetch Aave V3 positions...")
        results: list[AssetData] = []

        # Fetch supply positions (aTokens) in parallel - both balances and underlying assets
        supply_token_addresses = list(self.supply_tokens.values())

        logger.debug(
            "Fetching supply positions for %d tokens: %s",
            len(supply_token_addresses),
            supply_token_addresses,
        )

        try:
            supply_balances, supply_underlyings = await asyncio.gather(
                asyncio.gather(
                    *[
                        self._balance_of(token_address, subvault_address)
                        for token_address in supply_token_addresses
                    ]
                ),
                asyncio.gather(
                    *[
                        self._get_underlying_asset(token_address)
                        for token_address in supply_token_addresses
                    ]
                ),
            )
            logger.debug(
                "Successfully fetched %d supply balances and underlyings",
                len(supply_balances),
            )
        except Exception as e:
            logger.error(
                "Failed to fetch supply positions: %s (type: %s)",
                str(e),
                type(e).__name__,
                exc_info=True,
            )
            raise

        for (symbol, token_address), balance, underlying_address in zip(
            self.supply_tokens.items(), supply_balances, supply_underlyings
        ):
            if balance > 0:
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(underlying_address),
                        amount=balance,
                    )
                )
                logger.debug(
                    "Aave V3%s: %s supply balance for %s: %d (underlying: %s)",
                    instance_label,
                    symbol,
                    subvault_address,
                    balance,
                    underlying_address,
                )

        # Fetch borrow positions (variable debt tokens) in parallel - both balances and underlying assets
        borrow_token_addresses = list(self.borrow_tokens.values())
        borrow_balances, borrow_underlyings = await asyncio.gather(
            asyncio.gather(
                *[
                    self._balance_of(token_address, subvault_address)
                    for token_address in borrow_token_addresses
                ]
            ),
            asyncio.gather(
                *[
                    self._get_underlying_asset(token_address)
                    for token_address in borrow_token_addresses
                ]
            ),
        )

        for (symbol, token_address), balance, underlying_address in zip(
            self.borrow_tokens.items(), borrow_balances, borrow_underlyings
        ):
            if balance > 0:
                # Borrows are represented as NEGATIVE amounts
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(underlying_address),
                        amount=-balance,
                    )
                )
                logger.debug(
                    "Aave V3%s: %s borrow balance for %s: %d (stored as negative, underlying: %s)",
                    instance_label,
                    symbol,
                    subvault_address,
                    balance,
                    underlying_address,
                )

        logger.info(
            "Aave V3%s: fetched %d positions for subvault %s",
            instance_label,
            len(results),
            subvault_address,
        )

        # Merge with previous adapter results if provided (for adapter chaining)
        if previous_assets:
            logger.debug(
                "Aave V3%s: merging %d previous assets with %d new assets",
                instance_label,
                len(previous_assets),
                len(results),
            )
            results = list(previous_assets) + results

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
