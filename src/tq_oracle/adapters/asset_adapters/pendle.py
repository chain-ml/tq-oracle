"""Pendle PT and LP mark-to-market adapter.

This adapter marks Pendle Principal Token (PT) and LP positions to market
using the Pendle oracle to get current values in terms of accounting assets.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ...abi import load_erc20_abi
from ...constants import PENDLE_ORACLE_MAINNET
from ...logger import get_logger
from ...settings import Network
from .base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ...settings import OracleSettings

logger = get_logger(__name__)


class PendleAdapter(BaseAssetAdapter):
    """
    Mark-to-market Pendle PT and LP positions using Pendle oracle.

    This adapter:
    - Queries PT token balances and LP token balances
    - Uses Pendle oracle convenience functions (getPtToAssetRate, getLpToAssetRate)
    - Converts positions to accounting asset value
    - Returns positions valued in the accounting asset (e.g., USDC, WETH)

    Configuration via TOML:
    [adapters.pendle]
    oracle_address = "0x9a9Fa8338dd5E5B2188006f1Cd2Ef26d921650C2"

    [adapters.pendle.markets.market1]
    market = "0x..."  # Pendle market address
    accounting_asset = "0x..."  # Asset to value in (USDC, WETH, etc.)
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize Pendle adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - oracle_address: Pendle oracle address
                - markets: Dict of market configurations
        """
        super().__init__(config)

        # Skip if not on mainnet (for now)
        self._skip = config.network != Network.MAINNET
        if self._skip:
            logger.info(
                "Skipping Pendle adapter: network=%s (only mainnet supported)",
                config.network.value,
            )
            return

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for Pendle adapter")

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter

        # Load configuration
        adapter_config = config.adapters.pendle

        self.oracle_address = overrides.get(
            "oracle_address",
            adapter_config.oracle_address or PENDLE_ORACLE_MAINNET,
        )

        # Markets configuration
        if "markets" in overrides:
            self.markets = overrides["markets"]
        elif adapter_config.markets:
            self.markets = adapter_config.markets
        else:
            self.markets = {}

        logger.debug(
            "Pendle adapter initialized: oracle=%s, markets=%d",
            self.oracle_address,
            len(self.markets),
        )

        # Pendle oracle ABI - using convenience functions
        # https://docs.pendle.finance/Developers/Contracts/PendleOracle
        self._oracle_abi = [
            {
                "inputs": [
                    {"internalType": "address", "name": "market", "type": "address"},
                    {"internalType": "uint32", "name": "duration", "type": "uint32"},
                ],
                "name": "getPtToAssetRate",
                "outputs": [{"internalType": "uint256", "name": "ptToAssetRate", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
            },
            {
                "inputs": [
                    {"internalType": "address", "name": "market", "type": "address"},
                    {"internalType": "uint32", "name": "duration", "type": "uint32"},
                ],
                "name": "getLpToAssetRate",
                "outputs": [{"internalType": "uint256", "name": "lpToAssetRate", "type": "uint256"}],
                "stateMutability": "view",
                "type": "function",
            },
        ]

        # Pendle market ABI for getting PT token address
        self._market_abi = [
            {
                "inputs": [],
                "name": "readTokens",
                "outputs": [
                    {"internalType": "contract IStandardizedYield", "name": "SY", "type": "address"},
                    {"internalType": "contract IPPrincipalToken", "name": "PT", "type": "address"},
                    {"internalType": "contract IPYieldToken", "name": "YT", "type": "address"},
                ],
                "stateMutability": "view",
                "type": "function",
            },
        ]

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "pendle"

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

    async def _balance_of(self, token: str, owner: str) -> int:
        """Query ERC20 balance.

        Args:
            token: Token address (PT or LP)
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

    async def _get_pt_address(self, market_address: str) -> str:
        """Get PT token address from market.

        Args:
            market_address: Pendle market address

        Returns:
            PT token address
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(market_address),
            abi=self._market_abi,
        )
        _, pt_address, _ = await self._rpc(
            contract.functions.readTokens().call,
            block_identifier=self.block_number,
        )
        return pt_address

    async def _get_pt_to_asset_rate(
        self,
        market_address: str,
        duration: int = 900,  # 15 minutes TWAP
    ) -> int:
        """Get PT to asset conversion rate from oracle.

        Uses Pendle oracle's convenience function that handles PT -> SY -> Asset
        conversion internally.

        Args:
            market_address: Pendle market address
            duration: TWAP duration in seconds (default 900 = 15 min)

        Returns:
            PT to asset rate (18 decimals)
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.oracle_address),
            abi=self._oracle_abi,
        )
        rate = await self._rpc(
            contract.functions.getPtToAssetRate(
                Web3.to_checksum_address(market_address),
                duration,
            ).call,
            block_identifier=self.block_number,
        )
        return rate

    async def _get_lp_to_asset_rate(
        self,
        market_address: str,
        duration: int = 900,  # 15 minutes TWAP
    ) -> int:
        """Get LP to asset conversion rate from oracle.

        Uses Pendle oracle's convenience function that handles LP -> SY -> Asset
        conversion internally.

        Args:
            market_address: Pendle market address
            duration: TWAP duration in seconds (default 900 = 15 min)

        Returns:
            LP to asset rate (18 decimals)
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.oracle_address),
            abi=self._oracle_abi,
        )
        rate = await self._rpc(
            contract.functions.getLpToAssetRate(
                Web3.to_checksum_address(market_address),
                duration,
            ).call,
            block_identifier=self.block_number,
        )
        return rate

    async def _process_pt_position(
        self,
        market_name: str,
        market_config: dict[str, str],
        subvault_address: str,
    ) -> list[AssetData]:
        """Process PT position for a market.

        Args:
            market_name: Market identifier
            market_config: Market configuration with 'market' and 'accounting_asset'
            subvault_address: Subvault address to query

        Returns:
            List of AssetData with PT position valued in accounting asset
        """
        market_address = market_config["market"]
        accounting_asset = market_config["accounting_asset"]

        # Get PT token address
        pt_address = await self._get_pt_address(market_address)

        # Get PT balance
        pt_balance = await self._balance_of(pt_address, subvault_address)
        if pt_balance == 0:
            return []

        # Get PT to asset rate
        pt_rate = await self._get_pt_to_asset_rate(market_address)

        # Calculate value in accounting asset
        # pt_rate is in 18 decimals, so: value = (balance * rate) / 10^18
        asset_value = (pt_balance * pt_rate) // (10**18)

        logger.info(
            "Pendle PT %s: balance=%d, rate=%d, value=%d %s",
            market_name,
            pt_balance,
            pt_rate,
            asset_value,
            accounting_asset,
        )

        return [
            AssetData(
                asset_address=Web3.to_checksum_address(accounting_asset),
                amount=asset_value,
            )
        ]

    async def _process_lp_position(
        self,
        market_name: str,
        market_config: dict[str, str],
        subvault_address: str,
    ) -> list[AssetData]:
        """Process LP position for a market.

        Args:
            market_name: Market identifier
            market_config: Market configuration with 'market' and 'accounting_asset'
            subvault_address: Subvault address to query

        Returns:
            List of AssetData with LP position valued in accounting asset
        """
        market_address = market_config["market"]
        accounting_asset = market_config["accounting_asset"]

        # Market address IS the LP token for Pendle
        lp_balance = await self._balance_of(market_address, subvault_address)
        if lp_balance == 0:
            return []

        # Get LP to asset rate
        lp_rate = await self._get_lp_to_asset_rate(market_address)

        # Calculate value in accounting asset
        asset_value = (lp_balance * lp_rate) // (10**18)

        logger.info(
            "Pendle LP %s: balance=%d, rate=%d, value=%d %s",
            market_name,
            lp_balance,
            lp_rate,
            asset_value,
            accounting_asset,
        )

        return [
            AssetData(
                asset_address=Web3.to_checksum_address(accounting_asset),
                amount=asset_value,
            )
        ]

    async def fetch_assets(self, subvault_address: str) -> list[AssetData]:
        """Fetch Pendle PT and LP positions for a subvault.

        This method:
        1. Queries PT balances for all configured markets
        2. Queries LP balances for all configured markets
        3. Uses Pendle oracle to get current market rates
        4. Returns positions valued in accounting assets

        Args:
            subvault_address: Subvault address to query

        Returns:
            List of AssetData with positions valued in accounting assets
        """
        if self._skip:
            return []

        results: list[AssetData] = []

        # Process each market
        for market_name, market_config in self.markets.items():
            if "market" not in market_config or "accounting_asset" not in market_config:
                logger.warning(
                    "Skipping Pendle market %s: missing 'market' or 'accounting_asset' config",
                    market_name,
                )
                continue

            # Process PT and LP positions concurrently
            pt_results, lp_results = await asyncio.gather(
                self._process_pt_position(market_name, market_config, subvault_address),
                self._process_lp_position(market_name, market_config, subvault_address),
            )

            results.extend(pt_results)
            results.extend(lp_results)

        logger.info(
            "Pendle: fetched %d positions for subvault %s",
            len(results),
            subvault_address,
        )

        return results

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for Pendle.

        Pendle adapter works per-subvault only.

        Returns:
            Empty list
        """
        logger.debug("Pendle adapter does not support global asset fetching")
        return []
