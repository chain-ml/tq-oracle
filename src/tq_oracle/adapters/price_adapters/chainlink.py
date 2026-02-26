"""Chainlink price adapter for stable USD pricing.

This adapter provides more stable USD/stablecoin pricing by using Chainlink
oracle feeds instead of relying on potentially noisy DEX bid-ask spreads.

Supports two modes:
- ETH base asset: Uses ETH/USD feed, inverts to USD/ETH for stablecoins
- Non-ETH base asset: Uses both ETH/USD and BASE/USD feeds for two-hop pricing
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from web3 import Web3

from ...abi import load_erc20_abi, load_chainlink_feed_abi
from ...constants import CHAINLINK_FEEDS
from .base import BasePriceAdapter, PriceData

if TYPE_CHECKING:
    from ...settings import OracleSettings

logger = logging.getLogger(__name__)


class ChainlinkAdapter(BasePriceAdapter):
    """Adapter for pricing USD stablecoins using Chainlink oracle feeds.

    This adapter:
    1. Reads Chainlink ETH/USD price feed (e.g., 1 ETH = $3000)
    2. When base asset is ETH: inverts to USD/ETH for stablecoins
    3. When base asset is non-ETH: uses a separate BASE/USD feed for two-hop
       pricing (USD → BASE) for stablecoins

    Configuration:
        chainlink_enabled: bool = True
        chainlink_eth_usd_feed: str = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
        chainlink_base_usd_feed: str | None = None  # For non-ETH base assets
        chainlink_stablecoins: list[str] = ["0xUSDC", "0xUSDT", "0xDAI"]
    """

    def __init__(self, config: OracleSettings):
        """Initialize Chainlink adapter.

        Args:
            config: Oracle settings with Chainlink configuration
        """
        super().__init__(config)

        if not config.chainlink_enabled:
            self._skip = True
            logger.info("Chainlink adapter disabled in configuration")
            return

        self._skip = False

        # Get ETH/USD feed address
        if config.chainlink_eth_usd_feed:
            self.eth_usd_feed = config.chainlink_eth_usd_feed
        else:
            # Use default for network
            network_key = config.network.value
            if network_key not in CHAINLINK_FEEDS:
                logger.warning(
                    "No default Chainlink feed for network %s, adapter disabled",
                    network_key,
                )
                self._skip = True
                return
            self.eth_usd_feed = CHAINLINK_FEEDS[network_key]

        # Optional BASE/USD feed for non-ETH base assets
        self.base_usd_feed: str | None = config.chainlink_base_usd_feed

        # Get stablecoins to price
        self.stablecoins = set(addr.lower() for addr in config.chainlink_stablecoins)

        if not self.stablecoins:
            logger.info(
                "No stablecoins configured for Chainlink adapter, will be skipped"
            )
            self._skip = True
            return

        # Web3 setup
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        self.block_number = config.block_number_required

        # Decimals cache
        self._decimals_cache: dict[str, int] = {}

        # Staleness threshold
        self.staleness_threshold = config.chainlink_staleness_threshold

        # Get ETH address (may be None on non-ETH networks)
        self.eth_address = config.assets.get("ETH")

        logger.info(
            "Chainlink adapter initialized: eth_usd_feed=%s, base_usd_feed=%s, stablecoins=%d",
            self.eth_usd_feed,
            self.base_usd_feed or "none",
            len(self.stablecoins),
        )

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "chainlink"

    async def get_token_decimals(self, token_address: str) -> int:
        """Fetch token decimals from on-chain contract, with caching.

        Args:
            token_address: The token contract address

        Returns:
            Number of decimals for the token
        """
        cache_key = token_address.lower()  # Normalize for consistent caching (FYEO-TQO-04)
        if cache_key in self._decimals_cache:
            return self._decimals_cache[cache_key]

        erc20_abi = load_erc20_abi()
        token_contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(token_address),
            abi=erc20_abi,
        )

        decimals = await asyncio.to_thread(
            lambda: int(
                token_contract.functions.decimals().call(
                    block_identifier=self.block_number
                )
            )
        )

        self._decimals_cache[cache_key] = decimals
        logger.debug(f"Fetched decimals for {token_address}: {decimals}")

        return decimals

    async def _get_feed_price(self, feed_address: str, label: str) -> tuple[int, int]:
        """Get price from a Chainlink oracle feed.

        Args:
            feed_address: Chainlink feed contract address
            label: Human-readable label for logging (e.g., "ETH/USD", "XAUT/USD")

        Returns:
            Tuple of (price, decimals)
            - price: Asset price in USD (e.g., 3000 * 10^8 for $3000)
            - decimals: Feed decimals (typically 8)

        Raises:
            ValueError: If price data is invalid, stale, or round is incomplete
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(feed_address),
            abi=load_chainlink_feed_abi(),
        )

        # Get latest round data, decimals, and block timestamp in parallel
        round_data, decimals, block = await asyncio.gather(
            asyncio.to_thread(
                contract.functions.latestRoundData().call,
                block_identifier=self.block_number,
            ),
            asyncio.to_thread(
                contract.functions.decimals().call,
                block_identifier=self.block_number,
            ),
            asyncio.to_thread(
                self.w3.eth.get_block,
                self.block_number,
            ),
        )

        round_id, answer, started_at, updated_at, answered_in_round = round_data
        block_timestamp = block["timestamp"]

        # Validation 1: Check price is positive
        if answer <= 0:
            raise ValueError(f"Invalid Chainlink price for {label}: {answer}")

        # Validation 2: Check updatedAt is not zero (data exists)
        if updated_at == 0:
            raise ValueError(
                f"Chainlink {label} feed not updated: updatedAt=0 for round {round_id}"
            )

        # Validation 3: Check round is complete (answeredInRound >= roundId)
        if answered_in_round < round_id:
            raise ValueError(
                f"Chainlink {label} round incomplete: answeredInRound={answered_in_round} < "
                f"roundId={round_id}"
            )

        # Validation 4: Check staleness threshold
        price_age = block_timestamp - updated_at
        if price_age > self.staleness_threshold:
            raise ValueError(
                f"Chainlink {label} price stale: age={price_age}s exceeds "
                f"threshold={self.staleness_threshold}s (updatedAt={updated_at}, "
                f"blockTimestamp={block_timestamp})"
            )

        logger.debug(
            "Chainlink %s: price=%d, decimals=%d, updated_at=%d, "
            "answered_in_round=%d, round_id=%d, price_age=%ds",
            label,
            answer,
            decimals,
            updated_at,
            answered_in_round,
            round_id,
            price_age,
        )

        return answer, decimals

    async def _get_eth_usd_price(self) -> tuple[int, int]:
        """Get ETH/USD price from Chainlink oracle."""
        return await self._get_feed_price(self.eth_usd_feed, "ETH/USD")

    async def _get_base_usd_price(self) -> tuple[int, int]:
        """Get BASE/USD price from Chainlink oracle.

        Raises:
            ValueError: If no base_usd_feed is configured
        """
        if not self.base_usd_feed:
            raise ValueError("No chainlink_base_usd_feed configured for non-ETH base asset")
        return await self._get_feed_price(self.base_usd_feed, "BASE/USD")

    def _invert_usd_feed(
        self,
        asset_usd_price: int,
        feed_decimals: int,
    ) -> int:
        """Invert an X/USD price to get USD/X price.

        Works for any asset: ETH/USD → USD/ETH, XAUT/USD → USD/XAUT, etc.

        Args:
            asset_usd_price: Price of 1 asset in USD (e.g., 3000 * 10^8)
            feed_decimals: Decimals of the feed (typically 8)

        Returns:
            Price of 1 USD in the asset (18 decimals)

        Example:
            ETH/USD = 3000 (with 8 decimals: 300000000000)
            USD/ETH = 10^18 * 10^8 / 300000000000
                    = 3333333333 (in D18, represents 0.000333... ETH)
        """
        # Formula: usd_in_asset = 10^18 / (asset_usd_price / 10^feed_decimals)
        #                       = (10^18 * 10^feed_decimals) / asset_usd_price
        numerator = 10**18 * (10**feed_decimals)
        usd_in_asset = numerator // asset_usd_price

        logger.debug(
            "Inverted feed: %d (decimals=%d) -> USD/asset %d (D18)",
            asset_usd_price,
            feed_decimals,
            usd_in_asset,
        )

        return usd_in_asset

    async def fetch_prices(
        self,
        asset_addresses: list[str],
        prices_accumulator: PriceData,
    ) -> PriceData:
        """Fetch and accumulate stablecoin prices using Chainlink.

        This adapter only prices assets in the configured stablecoins list.
        All other assets are skipped and will be priced by other adapters.

        Supports two modes:
        - ETH base: Uses ETH/USD feed, inverts to USD/ETH for stablecoins
        - Non-ETH base: Uses BASE/USD feed, inverts to USD/BASE for stablecoins

        Args:
            asset_addresses: List of asset addresses to potentially price
            prices_accumulator: Existing price accumulator to update

        Returns:
            Updated price accumulator with Chainlink-derived stablecoin prices

        Notes:
            - Only processes assets in chainlink_stablecoins configuration
            - Assumes all configured stablecoins are worth ~$1
            - Prices are in D18 per 1 unit of asset
        """
        if self._skip:
            return prices_accumulator

        is_eth_base = self.eth_address and (
            prices_accumulator.base_asset == self.eth_address
        )

        # Determine USD/BASE price
        if is_eth_base:
            # ETH is base: use ETH/USD feed directly
            try:
                eth_usd_price, feed_decimals = await self._get_eth_usd_price()
            except Exception as e:
                logger.error("Failed to fetch Chainlink ETH/USD price: %s", e)
                return prices_accumulator
            usd_in_base = self._invert_usd_feed(eth_usd_price, feed_decimals)
            feed_label = f"ETH/USD: {eth_usd_price}"
        elif self.base_usd_feed:
            # Non-ETH base: use BASE/USD feed for two-hop
            try:
                base_usd_price, feed_decimals = await self._get_base_usd_price()
            except Exception as e:
                logger.error("Failed to fetch Chainlink BASE/USD price: %s", e)
                return prices_accumulator
            usd_in_base = self._invert_usd_feed(base_usd_price, feed_decimals)
            feed_label = f"BASE/USD: {base_usd_price}"
        else:
            logger.warning(
                "Non-ETH base asset detected but no chainlink_base_usd_feed configured. "
                "Skipping Chainlink stablecoin pricing."
            )
            return prices_accumulator

        # Apply to all configured stablecoins that are in the asset list
        priced_count = 0
        for asset_address in asset_addresses:
            if asset_address.lower() in self.stablecoins:
                # For stablecoins, we assume 1 token ≈ 1 USD
                # usd_in_base represents: "base asset per 1 whole USD" in D18
                prices_accumulator.prices[asset_address] = usd_in_base

                # Fetch and store token decimals
                token_decimals = await self.get_token_decimals(asset_address)
                prices_accumulator.decimals[asset_address] = token_decimals

                priced_count += 1

                logger.info(
                    "Chainlink priced %s: %d D18 (decimals=%d)",
                    asset_address,
                    usd_in_base,
                    token_decimals,
                )

        logger.info(
            "Chainlink adapter priced %d stablecoins (%s)",
            priced_count,
            feed_label,
        )

        self.validate_prices(prices_accumulator)
        return prices_accumulator
