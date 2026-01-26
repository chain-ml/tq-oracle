"""Chainlink price adapter for stable USD to ETH conversions.

This adapter provides more stable USD/stablecoin pricing by using Chainlink's
ETH/USD oracle instead of relying on potentially noisy DEX bid-ask spreads.
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
    """Adapter for pricing USD stablecoins using Chainlink ETH/USD oracle.

    This adapter:
    1. Reads Chainlink ETH/USD price feed (e.g., 1 ETH = $3000)
    2. Inverts it to get USD in ETH (e.g., 1 USD = 0.000333 ETH)
    3. Applies this price to configured stablecoins (USDC, USDT, DAI, etc.)

    Benefits over CoW Swap for stablecoins:
    - More stable pricing (oracle vs. bid-ask spread)
    - Less susceptible to manipulation
    - Consistent across all USD stablecoins

    Configuration:
        chainlink_enabled: bool = True
        chainlink_eth_usd_feed: str = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
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

        # Get ETH address for base asset validation
        eth_address = config.assets["ETH"]
        if eth_address is None:
            raise ValueError("ETH address is required for Chainlink adapter")
        self.eth_address = eth_address

        logger.info(
            "Chainlink adapter initialized: feed=%s, stablecoins=%d",
            self.eth_usd_feed,
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
        if token_address in self._decimals_cache:
            return self._decimals_cache[token_address]

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

        self._decimals_cache[token_address] = decimals
        logger.debug(f"Fetched decimals for {token_address}: {decimals}")

        return decimals

    async def _get_eth_usd_price(self) -> tuple[int, int]:
        """Get ETH/USD price from Chainlink oracle.

        Returns:
            Tuple of (price, decimals)
            - price: ETH price in USD (e.g., 3000 * 10^8 for $3000)
            - decimals: Feed decimals (typically 8)

        Raises:
            ValueError: If price data is invalid, stale, or round is incomplete
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.eth_usd_feed),
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
            raise ValueError(f"Invalid Chainlink price: {answer}")

        # Validation 2: Check updatedAt is not zero (data exists)
        if updated_at == 0:
            raise ValueError(
                f"Chainlink price feed not updated: updatedAt=0 for round {round_id}"
            )

        # Validation 3: Check round is complete (answeredInRound >= roundId)
        if answered_in_round < round_id:
            raise ValueError(
                f"Chainlink round incomplete: answeredInRound={answered_in_round} < "
                f"roundId={round_id}"
            )

        # Validation 4: Check staleness threshold
        price_age = block_timestamp - updated_at
        if price_age > self.staleness_threshold:
            raise ValueError(
                f"Chainlink price stale: age={price_age}s exceeds "
                f"threshold={self.staleness_threshold}s (updatedAt={updated_at}, "
                f"blockTimestamp={block_timestamp})"
            )

        logger.debug(
            "Chainlink ETH/USD: price=%d, decimals=%d, updated_at=%d, "
            "answered_in_round=%d, round_id=%d, price_age=%ds",
            answer,
            decimals,
            updated_at,
            answered_in_round,
            round_id,
            price_age,
        )

        return answer, decimals

    def _convert_eth_usd_to_usd_eth(
        self,
        eth_usd_price: int,
        feed_decimals: int,
    ) -> int:
        """Convert ETH/USD price to USD/ETH price (inverted).

        Args:
            eth_usd_price: Price of 1 ETH in USD (e.g., 3000 * 10^8)
            feed_decimals: Decimals of the feed (typically 8)

        Returns:
            Price of 1 USD in ETH (18 decimals)

        Example:
            ETH/USD = 3000 (with 8 decimals: 300000000000)
            USD/ETH = 10^18 / (3000 * 10^8) = 10^18 / 300000000000
                    = 3333333333 (in 18 decimals, represents 0.000333... ETH)
        """
        # Formula: usd_in_eth = 10^18 / (eth_usd_price / 10^feed_decimals)
        #                     = (10^18 * 10^feed_decimals) / eth_usd_price
        numerator = 10**18 * (10**feed_decimals)
        usd_in_eth = numerator // eth_usd_price

        logger.debug(
            "Converted ETH/USD %d (decimals=%d) to USD/ETH %d (18 decimals)",
            eth_usd_price,
            feed_decimals,
            usd_in_eth,
        )

        return usd_in_eth

    async def fetch_prices(
        self,
        asset_addresses: list[str],
        prices_accumulator: PriceData,
    ) -> PriceData:
        """Fetch and accumulate stablecoin prices using Chainlink.

        This adapter only prices assets in the configured stablecoins list.
        All other assets are skipped and will be priced by other adapters.

        Args:
            asset_addresses: List of asset addresses to potentially price
            prices_accumulator: Existing price accumulator to update

        Returns:
            Updated price accumulator with Chainlink-derived stablecoin prices

        Notes:
            - Only processes assets in chainlink_stablecoins configuration
            - Assumes all configured stablecoins are worth ~$1
            - Uses Chainlink ETH/USD oracle for conversion
            - Prices are in 18-decimal wei per 1 unit of asset
        """
        if self._skip:
            return prices_accumulator

        if prices_accumulator.base_asset != self.eth_address:
            raise ValueError("Chainlink adapter only supports ETH as base asset")

        # Get ETH/USD price from Chainlink
        try:
            eth_usd_price, feed_decimals = await self._get_eth_usd_price()
        except Exception as e:
            logger.error("Failed to fetch Chainlink ETH/USD price: %s", e)
            return prices_accumulator

        # Convert to USD/ETH (inverted)
        usd_in_eth = self._convert_eth_usd_to_usd_eth(eth_usd_price, feed_decimals)

        # Apply to all configured stablecoins that are in the asset list
        priced_count = 0
        for asset_address in asset_addresses:
            if asset_address.lower() in self.stablecoins:
                # For stablecoins, we assume 1 token ≈ 1 USD
                # usd_in_eth represents: "ETH per 1 whole USD" in 18 decimals
                # Store this directly without normalization to match Pyth's format
                prices_accumulator.prices[asset_address] = usd_in_eth

                # Fetch and store token decimals
                token_decimals = await self.get_token_decimals(asset_address)
                prices_accumulator.decimals[asset_address] = token_decimals

                priced_count += 1

                logger.info(
                    "Chainlink priced %s: %d wei (decimals=%d)",
                    asset_address,
                    usd_in_eth,
                    token_decimals,
                )

        logger.info(
            "Chainlink adapter priced %d stablecoins (ETH/USD: %d)",
            priced_count,
            eth_usd_price,
        )

        self.validate_prices(prices_accumulator)
        return prices_accumulator
