"""Euler V2 asset adapter for collecting supply and borrow positions.

This adapter handles Euler V2 lending positions:
1. Queries e-vault share balances and converts to underlying via convertToAssets
2. Queries borrow positions via debtOf
3. Returns supply as positive amounts, borrows as negative amounts
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ...abi import load_euler_vault_abi
from ...logger import get_logger
from ...settings import Network
from .base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ...settings import OracleSettings

logger = get_logger(__name__)


class EulerV2Adapter(BaseAssetAdapter):
    """
    Collect Euler V2 supply and borrow positions for a given subvault address.

    This adapter:
    - Queries e-vault share balances and converts to underlying via convertToAssets
    - Queries borrow positions via debtOf (returns underlying units directly)
    - Returns supply as positive AssetData, borrows as negative AssetData
    - Caches underlying asset addresses (same vault may be in supply and borrow)

    Configuration via TOML:
    [adapters.euler_v2]

    [adapters.euler_v2.supply_vaults]
    eUSDC = "0x..."
    eWETH = "0x..."

    [adapters.euler_v2.borrow_vaults]
    eUSDC = "0x..."
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize Euler V2 adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - supply_vaults: Dict of name -> e-vault address
                - borrow_vaults: Dict of name -> e-vault address
        """
        super().__init__(config)

        # Skip if not on mainnet
        self._skip = config.network != Network.MAINNET
        if self._skip:
            logger.info(
                "Skipping Euler V2 adapter: network=%s (only mainnet supported)",
                config.network.value,
            )
            return

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for Euler V2 adapter")

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout

        # Load configuration
        adapter_config = config.adapters.euler_v2

        # Supply vaults
        if "supply_vaults" in overrides:
            self.supply_vaults = overrides["supply_vaults"]
        elif adapter_config.supply_vaults:
            self.supply_vaults = adapter_config.supply_vaults
        else:
            self.supply_vaults = {}

        # Borrow vaults
        if "borrow_vaults" in overrides:
            self.borrow_vaults = overrides["borrow_vaults"]
        elif adapter_config.borrow_vaults:
            self.borrow_vaults = adapter_config.borrow_vaults
        else:
            self.borrow_vaults = {}

        # Validate configuration
        self._validate_vaults_config()

        # Cache for vault underlying asset addresses
        self._asset_cache: dict[str, str] = {}

        logger.info(
            "Euler V2: initialized with supply_vaults=%d, borrow_vaults=%d",
            len(self.supply_vaults),
            len(self.borrow_vaults),
        )

    def _validate_vaults_config(self) -> None:
        """Validate vault configuration for duplicates within each dict."""
        # Check supply vaults for duplicate addresses
        seen_supply: dict[str, str] = {}  # address -> name
        for name, address in self.supply_vaults.items():
            addr_lower = address.lower()
            if addr_lower in seen_supply:
                raise ValueError(
                    f"Duplicate supply vault address {address} "
                    f"for '{seen_supply[addr_lower]}' and '{name}'"
                )
            seen_supply[addr_lower] = name

        # Check borrow vaults for duplicate addresses
        seen_borrow: dict[str, str] = {}  # address -> name
        for name, address in self.borrow_vaults.items():
            addr_lower = address.lower()
            if addr_lower in seen_borrow:
                raise ValueError(
                    f"Duplicate borrow vault address {address} "
                    f"for '{seen_borrow[addr_lower]}' and '{name}'"
                )
            seen_borrow[addr_lower] = name

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "euler_v2"

    @backoff.on_exception(
        backoff.expo,
        (ProviderConnectionError,),
        max_time=30,
        jitter=backoff.full_jitter,
    )
    async def _rpc(self, fn, *args, **kwargs):
        """Execute RPC call with throttling, timeout, and retry."""
        async with self._rpc_sem:
            result = await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs),
                timeout=self._rpc_timeout,
            )
        delay = self._rpc_delay + random.random() * self._rpc_jitter
        if delay > 0:
            await asyncio.sleep(delay)
        return result

    async def _get_vault_asset(self, vault_address: str) -> str:
        """Get underlying asset address for a vault, with caching.

        Args:
            vault_address: e-vault address

        Returns:
            Underlying token address
        """
        addr_lower = vault_address.lower()
        if addr_lower in self._asset_cache:
            return self._asset_cache[addr_lower]

        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(vault_address),
            abi=load_euler_vault_abi(),
        )
        asset_address = await self._rpc(
            contract.functions.asset().call,
            block_identifier=self.block_number,
        )
        self._asset_cache[addr_lower] = asset_address
        return asset_address

    async def _balance_of(self, vault_address: str, account: str) -> int:
        """Query e-vault share balance.

        Args:
            vault_address: e-vault address
            account: Account address

        Returns:
            Share balance
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(vault_address),
            abi=load_euler_vault_abi(),
        )
        return await self._rpc(
            contract.functions.balanceOf(Web3.to_checksum_address(account)).call,
            block_identifier=self.block_number,
        )

    async def _convert_to_assets(self, vault_address: str, shares: int) -> int:
        """Convert vault shares to underlying asset amount.

        Args:
            vault_address: e-vault address
            shares: Number of shares

        Returns:
            Underlying asset amount
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(vault_address),
            abi=load_euler_vault_abi(),
        )
        return await self._rpc(
            contract.functions.convertToAssets(shares).call,
            block_identifier=self.block_number,
        )

    async def _debt_of(self, vault_address: str, account: str) -> int:
        """Query borrow debt for an account.

        Args:
            vault_address: e-vault address
            account: Account address

        Returns:
            Debt amount in underlying units
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(vault_address),
            abi=load_euler_vault_abi(),
        )
        return await self._rpc(
            contract.functions.debtOf(Web3.to_checksum_address(account)).call,
            block_identifier=self.block_number,
        )

    async def fetch_assets(
        self,
        subvault_address: str,
        previous_assets: list[AssetData] | None = None,
    ) -> list[AssetData]:
        """Fetch Euler V2 positions for a subvault.

        This method:
        1. Queries all configured supply vault balances and converts to underlying
        2. Queries all configured borrow vault debts
        3. Returns supply as positive, borrow as negative

        Args:
            subvault_address: Subvault address to query
            previous_assets: Optional assets from previous adapters

        Returns:
            List of AssetData with positions
        """
        if self._skip:
            return previous_assets or []

        results: list[AssetData] = []

        # --- Supply positions ---
        if self.supply_vaults:
            supply_vault_addresses = list(self.supply_vaults.values())

            # Fetch balances and underlying assets in parallel
            balances, assets = await asyncio.gather(
                asyncio.gather(
                    *[
                        self._balance_of(vault_addr, subvault_address)
                        for vault_addr in supply_vault_addresses
                    ]
                ),
                asyncio.gather(
                    *[
                        self._get_vault_asset(vault_addr)
                        for vault_addr in supply_vault_addresses
                    ]
                ),
            )

            # Convert non-zero balances to underlying amounts
            convert_tasks = []
            convert_indices = []
            for i, (balance, vault_addr) in enumerate(
                zip(balances, supply_vault_addresses)
            ):
                if balance > 0:
                    convert_tasks.append(
                        self._convert_to_assets(vault_addr, balance)
                    )
                    convert_indices.append(i)

            if convert_tasks:
                underlying_amounts = await asyncio.gather(*convert_tasks)

                for idx, underlying_amount in zip(convert_indices, underlying_amounts):
                    name = list(self.supply_vaults.keys())[idx]
                    asset_address = assets[idx]
                    results.append(
                        AssetData(
                            asset_address=Web3.to_checksum_address(asset_address),
                            amount=underlying_amount,
                        )
                    )
                    logger.info(
                        "Euler V2: %s supply shares=%d, underlying=%d (asset=%s)",
                        name,
                        balances[idx],
                        underlying_amount,
                        asset_address,
                    )

        # --- Borrow positions ---
        if self.borrow_vaults:
            borrow_vault_addresses = list(self.borrow_vaults.values())

            # Fetch debts and underlying assets in parallel
            debts, borrow_assets = await asyncio.gather(
                asyncio.gather(
                    *[
                        self._debt_of(vault_addr, subvault_address)
                        for vault_addr in borrow_vault_addresses
                    ]
                ),
                asyncio.gather(
                    *[
                        self._get_vault_asset(vault_addr)
                        for vault_addr in borrow_vault_addresses
                    ]
                ),
            )

            for (name, vault_addr), debt, asset_address in zip(
                self.borrow_vaults.items(), debts, borrow_assets
            ):
                if debt > 0:
                    results.append(
                        AssetData(
                            asset_address=Web3.to_checksum_address(asset_address),
                            amount=-debt,
                        )
                    )
                    logger.info(
                        "Euler V2: %s borrow debt=%d (asset=%s, stored as negative)",
                        name,
                        debt,
                        asset_address,
                    )

        logger.info(
            "Euler V2: fetched %d positions for subvault %s",
            len(results),
            subvault_address,
        )

        # Merge with previous adapter results
        if previous_assets:
            results = list(previous_assets) + results

        return results

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for Euler V2.

        Returns:
            Empty list
        """
        logger.debug("Euler V2 adapter does not support global asset fetching")
        return []
