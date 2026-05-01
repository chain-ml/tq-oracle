"""Morpho Blue asset adapter for collecting supply, borrow, and collateral positions.

This adapter handles Morpho Blue lending positions:
1. Queries market params and state for configured market IDs
2. Gets user position (supplyShares, borrowShares, collateral)
3. Accrues interest to get real-time supply/borrow values
4. Returns collateral as positive, supply as positive, borrow as negative
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ...abi import load_morpho_blue_abi, load_morpho_irm_abi
from ...logger import get_logger

from .base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ...settings import OracleSettings

logger = get_logger(__name__)

# Default Morpho Blue address on mainnet
DEFAULT_MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"

# WAD for 18 decimal math
WAD = 10**18


@dataclass(frozen=True, slots=True)
class MarketParams:
    """Morpho Blue market parameters."""

    loan_token: str
    collateral_token: str
    oracle: str
    irm: str
    lltv: int


@dataclass(frozen=True, slots=True)
class MarketState:
    """Morpho Blue market state."""

    total_supply_assets: int
    total_supply_shares: int
    total_borrow_assets: int
    total_borrow_shares: int
    last_update: int
    fee: int


@dataclass(frozen=True, slots=True)
class Position:
    """User position in a Morpho Blue market."""

    supply_shares: int
    borrow_shares: int
    collateral: int


class MorphoBlueAdapter(BaseAssetAdapter):
    """
    Adapter for Morpho Blue lending positions.

    This adapter:
    - Queries positions for configured market IDs
    - Accrues interest to get real-time supply/borrow amounts
    - Returns collateral and supply as positive, borrows as negative
    - Supports passing through to subsequent adapters (e.g., Pendle for PT tokens)

    Configuration via TOML:
    [adapters.morpho_blue]
    morpho_address = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"  # Optional

    [adapters.morpho_blue.markets.market_name]
    market_id = "0x..."  # bytes32 market ID
    """

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize Morpho Blue adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - morpho_address: Morpho Blue contract address
                - markets: Dict of market configurations
        """
        super().__init__(config)

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for Morpho Blue adapter")

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout

        # Load configuration
        adapter_config = config.adapters.morpho_blue

        self.morpho_address = Web3.to_checksum_address(
            overrides.get("morpho_address")
            or adapter_config.morpho_address
            or DEFAULT_MORPHO_BLUE
        )

        # Markets configuration
        if "markets" in overrides:
            self.markets = overrides["markets"]
        elif adapter_config.markets:
            self.markets = adapter_config.markets
        else:
            self.markets = {}

        # Validate markets configuration
        self._validate_markets_config()

        # Initialize contract
        self.morpho_contract = self.w3.eth.contract(
            address=self.morpho_address,
            abi=load_morpho_blue_abi(),
        )

        # Cache for block timestamp
        self._block_timestamp: int | None = None

        logger.info(
            "Morpho Blue: initialized with morpho=%s, markets=%d",
            self.morpho_address,
            len(self.markets),
        )

    def _validate_markets_config(self) -> None:
        """Validate markets configuration for duplicates and required fields."""
        seen_market_ids: dict[str, str] = {}  # market_id -> market_name

        for market_name, market_config in self.markets.items():
            if "market_id" not in market_config:
                raise ValueError(
                    f"Morpho Blue market '{market_name}' missing required 'market_id' config"
                )

            market_id = market_config["market_id"].lower()
            if market_id in seen_market_ids:
                raise ValueError(
                    f"Duplicate market_id {market_config['market_id']} "
                    f"in Morpho Blue markets '{seen_market_ids[market_id]}' and '{market_name}'"
                )
            seen_market_ids[market_id] = market_name

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "morpho_blue"

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
        # Sleep outside semaphore to not block other tasks
        delay = self._rpc_delay + random.random() * self._rpc_jitter
        if delay > 0:
            await asyncio.sleep(delay)
        return result

    async def _get_block_timestamp(self) -> int:
        """Get cached block timestamp."""
        if self._block_timestamp is None:
            block = await self._rpc(
                self.w3.eth.get_block,
                self.block_number,
            )
            self._block_timestamp = block["timestamp"]
        return self._block_timestamp

    async def _get_market_params(self, market_id: bytes) -> MarketParams:
        """Get market parameters for a market ID.

        Args:
            market_id: The bytes32 market ID

        Returns:
            MarketParams with loan token, collateral token, oracle, irm, lltv
        """
        result = await self._rpc(
            self.morpho_contract.functions.idToMarketParams(market_id).call,
            block_identifier=self.block_number,
        )

        return MarketParams(
            loan_token=result[0],
            collateral_token=result[1],
            oracle=result[2],
            irm=result[3],
            lltv=result[4],
        )

    async def _get_market_state(self, market_id: bytes) -> MarketState:
        """Get market state for a market ID.

        Args:
            market_id: The bytes32 market ID

        Returns:
            MarketState with totals and lastUpdate
        """
        result = await self._rpc(
            self.morpho_contract.functions.market(market_id).call,
            block_identifier=self.block_number,
        )

        return MarketState(
            total_supply_assets=result[0],
            total_supply_shares=result[1],
            total_borrow_assets=result[2],
            total_borrow_shares=result[3],
            last_update=result[4],
            fee=result[5],
        )

    async def _get_position(self, market_id: bytes, user: str) -> Position:
        """Get user position in a market.

        Args:
            market_id: The bytes32 market ID
            user: User address

        Returns:
            Position with supplyShares, borrowShares, collateral
        """
        result = await self._rpc(
            self.morpho_contract.functions.position(
                market_id,
                Web3.to_checksum_address(user),
            ).call,
            block_identifier=self.block_number,
        )

        return Position(
            supply_shares=result[0],
            borrow_shares=result[1],
            collateral=result[2],
        )

    async def _get_borrow_rate(
        self,
        irm_address: str,
        market_params: MarketParams,
        market_state: MarketState,
    ) -> int:
        """Get borrow rate from IRM contract.

        Args:
            irm_address: IRM contract address
            market_params: Market parameters
            market_state: Market state

        Returns:
            Borrow rate per second (18 decimals)
        """
        irm_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(irm_address),
            abi=load_morpho_irm_abi(),
        )

        # Encode market params tuple
        params_tuple = (
            Web3.to_checksum_address(market_params.loan_token),
            Web3.to_checksum_address(market_params.collateral_token),
            Web3.to_checksum_address(market_params.oracle),
            Web3.to_checksum_address(market_params.irm),
            market_params.lltv,
        )

        # Encode market state tuple
        state_tuple = (
            market_state.total_supply_assets,
            market_state.total_supply_shares,
            market_state.total_borrow_assets,
            market_state.total_borrow_shares,
            market_state.last_update,
            market_state.fee,
        )

        rate = await self._rpc(
            irm_contract.functions.borrowRateView(params_tuple, state_tuple).call,
            block_identifier=self.block_number,
        )

        return rate

    def _accrue_interest(
        self,
        market_state: MarketState,
        borrow_rate: int,
        current_timestamp: int,
    ) -> tuple[int, int, int, int]:
        """Accrue interest to get real-time totals.

        Args:
            market_state: Current market state at lastUpdate
            borrow_rate: Borrow rate per second (18 decimals)
            current_timestamp: Current block timestamp

        Returns:
            Tuple of (newTotalSupplyAssets, newTotalSupplyShares,
                      newTotalBorrowAssets, newTotalBorrowShares)
        """
        elapsed = current_timestamp - market_state.last_update

        if elapsed == 0 or market_state.total_borrow_assets == 0:
            return (
                market_state.total_supply_assets,
                market_state.total_supply_shares,
                market_state.total_borrow_assets,
                market_state.total_borrow_shares,
            )

        # Calculate accrued interest using 3-term Taylor expansion
        # Matches Morpho Blue's MathLib.wTaylorCompounded(rate, elapsed):
        #   firstTerm  = rate * elapsed
        #   secondTerm = firstTerm^2 / (2 * WAD)
        #   thirdTerm  = secondTerm * firstTerm / (3 * WAD)
        #   interest   = totalBorrowAssets * (firstTerm + secondTerm + thirdTerm) / WAD
        first_term = borrow_rate * elapsed
        second_term = (first_term * first_term) // (2 * WAD)
        third_term = (second_term * first_term) // (3 * WAD)
        compound_factor = first_term + second_term + third_term

        interest = (
            market_state.total_borrow_assets * compound_factor
        ) // WAD

        # New borrow total
        new_total_borrow_assets = market_state.total_borrow_assets + interest

        # New supply total (interest flows to suppliers)
        new_total_supply_assets = market_state.total_supply_assets + interest

        # Calculate fee shares (fee is in WAD, e.g., 0.1e18 = 10%)
        # feeAmount = interest * fee / WAD
        fee_amount = (interest * market_state.fee) // WAD

        # feeShares = toSharesDown(feeAmount, newTotalSupplyAssets - feeAmount, totalSupplyShares)
        # toSharesDown(assets, totalAssets, totalShares) = assets * totalShares / totalAssets
        if new_total_supply_assets - fee_amount > 0 and market_state.total_supply_shares > 0:
            fee_shares = (
                fee_amount
                * market_state.total_supply_shares
                // (new_total_supply_assets - fee_amount)
            )
        else:
            fee_shares = 0

        new_total_supply_shares = market_state.total_supply_shares + fee_shares

        logger.debug(
            "Morpho Blue: accrued interest=%d, elapsed=%ds, fee_shares=%d",
            interest,
            elapsed,
            fee_shares,
        )

        return (
            new_total_supply_assets,
            new_total_supply_shares,
            new_total_borrow_assets,
            market_state.total_borrow_shares,  # Borrow shares don't change
        )

    def _shares_to_assets(
        self,
        shares: int,
        total_assets: int,
        total_shares: int,
    ) -> int:
        """Convert shares to assets (round down).

        Args:
            shares: Number of shares to convert
            total_assets: Total assets in pool
            total_shares: Total shares in pool

        Returns:
            Asset amount
        """
        if total_shares == 0:
            return 0
        return (shares * total_assets) // total_shares

    async def _process_market_position(
        self,
        market_name: str,
        market_config: dict[str, str],
        subvault_address: str,
    ) -> list[AssetData]:
        """Process a single market position.

        Args:
            market_name: Market identifier
            market_config: Market configuration with 'market_id'
            subvault_address: Subvault address to query

        Returns:
            List of AssetData with collateral, supply, and borrow positions
        """
        market_id_hex = market_config["market_id"]
        market_id = bytes.fromhex(market_id_hex.replace("0x", ""))

        # Fetch market params, state, and position in parallel
        market_params, market_state, position = await asyncio.gather(
            self._get_market_params(market_id),
            self._get_market_state(market_id),
            self._get_position(market_id, subvault_address),
        )

        # Check if position is empty
        if (
            position.supply_shares == 0
            and position.borrow_shares == 0
            and position.collateral == 0
        ):
            logger.debug(
                "Morpho Blue %s: no position for %s", market_name, subvault_address
            )
            return []

        results: list[AssetData] = []
        current_timestamp = await self._get_block_timestamp()

        # Get borrow rate and accrue interest if needed
        if position.supply_shares > 0 or position.borrow_shares > 0:
            borrow_rate = await self._get_borrow_rate(
                market_params.irm, market_params, market_state
            )

            (
                new_total_supply_assets,
                new_total_supply_shares,
                new_total_borrow_assets,
                new_total_borrow_shares,
            ) = self._accrue_interest(market_state, borrow_rate, current_timestamp)

            # Convert supply shares to assets
            if position.supply_shares > 0:
                supply_assets = self._shares_to_assets(
                    position.supply_shares,
                    new_total_supply_assets,
                    new_total_supply_shares,
                )
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(market_params.loan_token),
                        amount=supply_assets,
                    )
                )
                logger.info(
                    "Morpho Blue %s: supply shares=%d, assets=%d (loan_token=%s)",
                    market_name,
                    position.supply_shares,
                    supply_assets,
                    market_params.loan_token,
                )

            # Convert borrow shares to assets (negative)
            if position.borrow_shares > 0:
                borrow_assets = self._shares_to_assets(
                    position.borrow_shares,
                    new_total_borrow_assets,
                    new_total_borrow_shares,
                )
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(market_params.loan_token),
                        amount=-borrow_assets,
                    )
                )
                logger.info(
                    "Morpho Blue %s: borrow shares=%d, assets=%d (loan_token=%s, stored as negative)",
                    market_name,
                    position.borrow_shares,
                    borrow_assets,
                    market_params.loan_token,
                )

        # Collateral is stored as raw amount (no accrual needed)
        if position.collateral > 0:
            results.append(
                AssetData(
                    asset_address=Web3.to_checksum_address(
                        market_params.collateral_token
                    ),
                    amount=position.collateral,
                )
            )
            logger.info(
                "Morpho Blue %s: collateral=%d (collateral_token=%s)",
                market_name,
                position.collateral,
                market_params.collateral_token,
            )

        return results

    async def fetch_assets(
        self,
        subvault_address: str,
        previous_assets: list[AssetData] | None = None,
    ) -> list[AssetData]:
        """Fetch Morpho Blue positions for a subvault.

        This method:
        1. Queries all configured market positions
        2. Accrues interest to get real-time values
        3. Returns supply/collateral as positive, borrow as negative

        Args:
            subvault_address: Subvault address to query
            previous_assets: Optional assets from previous adapters

        Returns:
            List of AssetData with positions
        """
        results: list[AssetData] = []

        # Process each configured market
        for market_name, market_config in self.markets.items():
            if "market_id" not in market_config:
                logger.warning(
                    "Skipping Morpho Blue market %s: missing 'market_id' config",
                    market_name,
                )
                continue

            market_results = await self._process_market_position(
                market_name, market_config, subvault_address
            )
            results.extend(market_results)

        logger.info(
            "Morpho Blue: fetched %d positions for subvault %s",
            len(results),
            subvault_address,
        )

        # Merge with previous adapter results
        if previous_assets:
            results = list(previous_assets) + results

        return results

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for Morpho Blue.

        Returns:
            Empty list
        """
        logger.debug("Morpho Blue adapter does not support global asset fetching")
        return []
