"""Generic ERC4626 vault adapter.

This adapter handles any ERC4626-compliant vault token by:
1. Checking the vault token balance
2. Converting to underlying assets via convertToAssets()
3. Returning the underlying asset amount for pricing
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ....abi import load_erc20_abi, load_erc4626_abi
from ....logger import get_logger
from ..base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ....settings import OracleSettings

logger = get_logger(__name__)


class ERC4626VaultAdapter(BaseAssetAdapter):
    """
    Generic adapter for ERC4626-compliant vault tokens.

    This adapter:
    - Queries vault token balances
    - Calls convertToAssets() to get underlying asset amounts
    - Returns underlying assets for pricing pipeline

    Configuration via TOML:
    [adapters.erc4626]
    # Configure vault tokens to track
    [adapters.erc4626.vaults.vault_name]
    vault_token = "0x..."  # ERC4626 vault token address
    underlying_asset = "0x..."  # Underlying asset address
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize ERC4626 vault adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - vaults: Dict of vault configurations
        """
        super().__init__(config)

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for ERC4626 adapter")

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout

        # Load configuration
        adapter_config = config.adapters.erc4626

        # Vaults configuration
        if "vaults" in overrides:
            self.vaults = overrides["vaults"]
        elif adapter_config.vaults:
            self.vaults = adapter_config.vaults
        else:
            self.vaults = {}

        # Validate vault configuration (FYEO-TQO-03)
        self._validate_vaults_config()

        logger.debug(
            "ERC4626 adapter initialized: %d vaults configured",
            len(self.vaults),
        )

    def _validate_vaults_config(self) -> None:
        """Validate vault configuration for duplicates and required fields."""
        seen_vault_tokens: dict[str, str] = {}  # vault_token -> vault_name

        for vault_name, vault_config in self.vaults.items():
            if "vault_token" not in vault_config:
                raise ValueError(
                    f"ERC4626 vault '{vault_name}' missing required 'vault_token' config"
                )

            vault_token = vault_config["vault_token"].lower()
            if vault_token in seen_vault_tokens:
                raise ValueError(
                    f"Duplicate vault_token address {vault_config['vault_token']} "
                    f"in ERC4626 vaults '{seen_vault_tokens[vault_token]}' and '{vault_name}'"
                )
            seen_vault_tokens[vault_token] = vault_name

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "erc4626"

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

    async def _convert_to_assets(
        self, vault_token: str, shares: int, discount_tenths_bps: int = 0
    ) -> int:
        """Convert vault shares to underlying assets with optional market discount.

        Args:
            vault_token: ERC4626 vault token address
            shares: Number of shares to convert
            discount_tenths_bps: Optional discount in tenths of bps to apply (default: 0)
                                 e.g., 100 = 10 bps, 5 = 0.5 bps
                                 Used to account for withdrawal fees, market slippage, or illiquidity

        Returns:
            Amount of underlying assets after applying discount
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(vault_token),
            abi=load_erc4626_abi(),
        )
        assets = await self._rpc(
            contract.functions.convertToAssets(shares).call,
            block_identifier=self.block_number,
        )

        # Apply market discount if specified
        if discount_tenths_bps > 0:
            discounted_assets = int(assets) * (100000 - discount_tenths_bps) // 100000
            # Convert tenths of bps to bps for logging readability
            discount_bps = discount_tenths_bps / 10
            logger.debug(
                "ERC4626: applied %.1f bps discount (%d tenths bps): %d -> %d",
                discount_bps,
                discount_tenths_bps,
                assets,
                discounted_assets,
            )
            return discounted_assets

        return int(assets)

    async def _get_underlying_asset(self, vault_token: str) -> str:
        """Get underlying asset address from vault.

        Args:
            vault_token: ERC4626 vault token address

        Returns:
            Underlying asset address
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(vault_token),
            abi=load_erc4626_abi(),
        )
        asset = await self._rpc(
            contract.functions.asset().call,
            block_identifier=self.block_number,
        )
        return str(asset)

    async def _process_vault_position(
        self,
        vault_name: str,
        vault_config: dict[str, str],
        subvault_address: str,
    ) -> list[AssetData]:
        """Process vault position.

        Args:
            vault_name: Vault identifier
            vault_config: Vault configuration with 'vault_token' and 'underlying_asset'
            subvault_address: Subvault address to query

        Returns:
            List of AssetData with underlying asset amounts
        """
        vault_token = vault_config["vault_token"]
        underlying_asset = vault_config.get("underlying_asset")
        discount_tenths_bps = int(vault_config.get("market_discount", 0))

        # Get vault token balance
        vault_balance = await self._balance_of(vault_token, subvault_address)
        if vault_balance == 0:
            logger.debug(
                "ERC4626 %s: zero balance for %s", vault_name, subvault_address
            )
            return []

        # Convert to underlying assets with optional discount
        underlying_amount = await self._convert_to_assets(
            vault_token, vault_balance, discount_tenths_bps
        )

        # If underlying_asset not configured, fetch it from vault
        if not underlying_asset:
            underlying_asset = await self._get_underlying_asset(vault_token)
            logger.debug(
                "ERC4626 %s: fetched underlying asset %s from vault",
                vault_name,
                underlying_asset,
            )

        if discount_tenths_bps > 0:
            logger.info(
                "ERC4626 %s: shares=%d, underlying=%d (%s) [%.1f bps discount applied]",
                vault_name,
                vault_balance,
                underlying_amount,
                underlying_asset,
                discount_tenths_bps / 10,
            )
        else:
            logger.info(
                "ERC4626 %s: shares=%d, underlying=%d (%s)",
                vault_name,
                vault_balance,
                underlying_amount,
                underlying_asset,
            )

        return [
            AssetData(
                asset_address=Web3.to_checksum_address(underlying_asset),
                amount=underlying_amount,
            )
        ]

    async def fetch_assets(
        self,
        subvault_address: str,
        previous_assets: list[AssetData] | None = None,
    ) -> list[AssetData]:
        """Fetch ERC4626 vault positions for a subvault.

        This method:
        1. Queries vault token balances for all configured vaults
        2. Converts shares to underlying assets via convertToAssets()
        3. Converts any ERC4626 vault tokens from previous adapters
        4. Returns underlying assets for pricing pipeline

        Args:
            subvault_address: Subvault address to query
            previous_assets: Optional assets from previous adapters (for conversion)

        Returns:
            List of AssetData with underlying asset amounts
        """
        results: list[AssetData] = []

        # Process each vault owned by this subvault
        if self.vaults:
            for vault_name, vault_config in self.vaults.items():
                if "vault_token" not in vault_config:
                    logger.warning(
                        "Skipping ERC4626 vault %s: missing 'vault_token' config",
                        vault_name,
                    )
                    continue

                try:
                    vault_results = await self._process_vault_position(
                        vault_name, vault_config, subvault_address
                    )
                    results.extend(vault_results)
                except Exception as e:
                    logger.error(
                        "ERC4626 vault %s failed for %s: %s",
                        vault_name,
                        subvault_address,
                        e,
                    )

        # Convert any ERC4626 vault tokens from previous adapters
        if previous_assets:
            for asset in previous_assets:
                converted = await self._try_convert_vault_token(asset)
                results.append(converted if converted else asset)

        logger.info(
            "ERC4626: fetched %d positions for subvault %s",
            len(results),
            subvault_address,
        )

        return results

    async def _try_convert_vault_token(self, asset: AssetData) -> AssetData | None:
        """Try to convert asset if it's an ERC4626 vault token we know about.

        This enables conversion of vault tokens from other adapters (e.g., Aave aTokens
        backed by ERC4626 vault tokens) into their underlying assets.

        Args:
            asset: Asset to potentially convert

        Returns:
            Converted AssetData if this was a vault token, None otherwise (pass through)
        """
        vault_address = asset.asset_address.lower()

        # Check if this is a vault token in our config
        for vault_name, vault_config in self.vaults.items():
            if vault_config["vault_token"].lower() == vault_address:
                # This IS a vault token we can convert!
                underlying_asset = vault_config.get("underlying_asset")
                discount_tenths_bps = int(vault_config.get("market_discount", 0))

                logger.info(
                    "ERC4626: detected vault token %s from previous adapter, converting to underlying",
                    vault_address,
                )

                # Convert shares to assets with optional discount
                underlying_amount = await self._convert_to_assets(
                    vault_config["vault_token"], asset.amount, discount_tenths_bps
                )

                # Fetch underlying asset if not configured
                if not underlying_asset:
                    underlying_asset = await self._get_underlying_asset(
                        vault_config["vault_token"]
                    )
                    logger.debug(
                        "ERC4626: fetched underlying asset %s from vault",
                        underlying_asset,
                    )

                if discount_tenths_bps > 0:
                    logger.info(
                        "ERC4626: converted vault token %s: shares=%d, underlying=%d (%s) [%.1f bps discount applied]",
                        vault_name,
                        asset.amount,
                        underlying_amount,
                        underlying_asset,
                        discount_tenths_bps / 10,
                    )
                else:
                    logger.info(
                        "ERC4626: converted vault token %s: shares=%d, underlying=%d (%s)",
                        vault_name,
                        asset.amount,
                        underlying_amount,
                        underlying_asset,
                    )

                return AssetData(
                    asset_address=Web3.to_checksum_address(underlying_asset),
                    amount=underlying_amount,
                    tvl_only=asset.tvl_only,
                )

        # Not a vault token we know - return None to pass through unchanged
        return None

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for ERC4626.

        ERC4626 adapter works per-subvault only.

        Returns:
            Empty list
        """
        logger.debug("ERC4626 adapter does not support global asset fetching")
        return []
