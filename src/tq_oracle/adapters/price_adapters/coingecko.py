"""CoinGecko price adapter for batched token pricing.

This adapter provides stable pricing by querying CoinGecko's API,
batching multiple tokens into a single API call for efficiency.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import backoff
import requests
from web3 import Web3

from ...abi import load_erc20_abi
from ...constants import (
    COINGECKO_API_BASE_URL,
    COINGECKO_DEFAULT_IDS,
    COINGECKO_PRO_API_BASE_URL,
)
from .base import BasePriceAdapter, PriceData

if TYPE_CHECKING:
    from ...settings import OracleSettings

logger = logging.getLogger(__name__)


class CoinGeckoAdapter(BasePriceAdapter):
    """Adapter for pricing tokens using CoinGecko API.

    This adapter:
    1. Maps token addresses to CoinGecko IDs
    2. Batches all configured tokens into a single API call
    3. Fetches prices in ETH (or USD for non-ETH base assets)
    4. Handles both free and Pro API tiers

    For non-ETH base assets (e.g., XAUT), uses two-hop pricing:
    1. Fetches all token prices in USD from CoinGecko
    2. Fetches base asset price in USD (via coingecko_base_asset_id)
    3. Converts: price_in_base = price_usd / base_usd

    Configuration:
        coingecko_enabled: bool = True
        coingecko_api_key: str = "your_key"  # Optional - uses free tier if not provided
        coingecko_token_ids: dict[str, str] = {
            "0xUSDC": "usd-coin",
            "0xUSDT": "tether",
        }
        coingecko_base_asset_id: str | None = None  # e.g., "tether-gold" for XAUT
    """

    def __init__(self, config: OracleSettings):
        """Initialize CoinGecko adapter.

        Args:
            config: Oracle settings with CoinGecko configuration
        """
        super().__init__(config)

        if not config.coingecko_enabled:
            self._skip = True
            logger.info("CoinGecko adapter disabled in configuration")
            return

        self._skip = False

        # API configuration
        self.api_key = (
            config.coingecko_api_key.get_secret_value()
            if config.coingecko_api_key
            else None
        )

        # Use Pro API if key provided, otherwise free tier
        if self.api_key:
            self.api_base_url = COINGECKO_PRO_API_BASE_URL
            logger.info("Using CoinGecko Pro API")
        else:
            self.api_base_url = COINGECKO_API_BASE_URL
            logger.info("Using CoinGecko Free API")

        # Build token ID mapping from config + defaults
        self.token_ids: dict[str, str] = {}

        # Start with defaults
        for addr, cg_id in COINGECKO_DEFAULT_IDS.items():
            self.token_ids[addr.lower()] = cg_id

        # Override with user-configured mappings
        for addr, cg_id in config.coingecko_token_ids.items():
            self.token_ids[addr.lower()] = cg_id

        if not self.token_ids:
            logger.info("No tokens configured for CoinGecko adapter, will be skipped")
            self._skip = True
            return

        # Get ETH address for base asset validation
        eth_address = config.assets["ETH"]
        if eth_address is None:
            raise ValueError("ETH address is required for CoinGecko adapter")
        self.eth_address = eth_address

        # Non-ETH base asset CoinGecko ID for two-hop USD pricing
        self.base_asset_id: str | None = config.coingecko_base_asset_id

        # Web3 setup for fetching token decimals
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        self.block_number = config.block_number_required
        self._decimals_cache: dict[str, int] = {}

        # HTTP session for connection reuse (FYEO-TQO-07)
        self._session = requests.Session()

        logger.info(
            "CoinGecko adapter initialized: tokens=%d, api=%s, base_asset_id=%s",
            len(self.token_ids),
            "Pro" if self.api_key else "Free",
            self.base_asset_id or "eth (default)",
        )

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "coingecko"

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

        # Native ETH sentinel has no contract; decimals are always 18
        if cache_key == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
            self._decimals_cache[cache_key] = 18
            return 18

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

    @backoff.on_exception(
        backoff.expo,
        (requests.exceptions.RequestException, requests.exceptions.HTTPError),
        max_time=10,
        giveup=lambda e: isinstance(e, requests.exceptions.HTTPError)
        and e.response is not None
        and e.response.status_code not in [429, 500, 502, 503, 504],
        jitter=backoff.full_jitter,
    )
    async def _fetch_prices_batch(
        self,
        coingecko_ids: list[str],
        vs_currency: str = "eth",
    ) -> dict[str, float]:
        """Fetch prices for multiple tokens in one API call.

        Args:
            coingecko_ids: List of CoinGecko IDs to fetch prices for
            vs_currency: Currency to price against ("eth" or "usd")

        Returns:
            Dict mapping coingecko_id -> price_in_vs_currency
        """
        # Build comma-separated ID list
        ids_param = ",".join(coingecko_ids)

        # API endpoint
        url = f"{self.api_base_url}/simple/price"

        params = {
            "ids": ids_param,
            "vs_currencies": vs_currency,
            "precision": "18",  # Maximum precision
        }

        headers = {}
        if self.api_key:
            headers["x-cg-pro-api-key"] = self.api_key

        logger.debug("Fetching CoinGecko prices: ids=%s, vs=%s", ids_param, vs_currency)

        response = await asyncio.to_thread(
            self._session.get,
            url,
            params=params,
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()

        data = response.json()

        # Extract prices
        prices = {}
        for cg_id in coingecko_ids:
            if cg_id in data and vs_currency in data[cg_id]:
                prices[cg_id] = float(data[cg_id][vs_currency])
            else:
                logger.warning("No %s price returned for CoinGecko ID: %s", vs_currency, cg_id)

        logger.debug("CoinGecko returned %d prices (vs %s)", len(prices), vs_currency)
        return prices

    async def fetch_prices(
        self,
        asset_addresses: list[str],
        prices_accumulator: PriceData,
    ) -> PriceData:
        """Fetch and accumulate token prices from CoinGecko.

        This adapter batches all configured tokens into a single API call.
        Only processes assets that have CoinGecko ID mappings.

        For ETH base: fetches prices in ETH directly.
        For non-ETH base (e.g., XAUT): fetches prices in USD, then converts
        to base asset using the base asset's USD price (two-hop).

        Args:
            asset_addresses: List of asset addresses to potentially price
            prices_accumulator: Existing price accumulator to update

        Returns:
            Updated price accumulator with CoinGecko prices
        """
        if self._skip:
            return prices_accumulator

        is_eth_base = prices_accumulator.base_asset == self.eth_address

        if not is_eth_base and not self.base_asset_id:
            logger.info(
                "CoinGecko adapter skipped: non-ETH base asset and no "
                "coingecko_base_asset_id configured (base=%s)",
                prices_accumulator.base_asset,
            )
            return prices_accumulator

        # Find tokens to price (that have CoinGecko IDs)
        tokens_to_price: dict[str, str] = {}  # address -> coingecko_id

        for asset_address in asset_addresses:
            addr_lower = asset_address.lower()
            if addr_lower in self.token_ids:
                cg_id = self.token_ids[addr_lower]
                tokens_to_price[asset_address] = cg_id

        if not tokens_to_price:
            logger.debug("No CoinGecko-mapped tokens to price")
            return prices_accumulator

        if is_eth_base:
            return await self._fetch_eth_base_prices(
                tokens_to_price, prices_accumulator
            )
        else:
            return await self._fetch_two_hop_prices(
                tokens_to_price, prices_accumulator
            )

    async def _fetch_eth_base_prices(
        self,
        tokens_to_price: dict[str, str],
        prices_accumulator: PriceData,
    ) -> PriceData:
        """Fetch prices denominated directly in ETH."""
        try:
            coingecko_ids = list(set(tokens_to_price.values()))
            cg_prices = await self._fetch_prices_batch(coingecko_ids, vs_currency="eth")
        except Exception as e:
            logger.error("Failed to fetch CoinGecko prices: %s", e)
            return prices_accumulator

        priced_count = 0
        for asset_address, cg_id in tokens_to_price.items():
            if asset_address in prices_accumulator.prices:
                logger.debug(
                    "Skipping %s - already priced by higher-priority adapter",
                    asset_address,
                )
                continue

            if cg_id not in cg_prices:
                logger.warning("No CoinGecko price for %s (id: %s)", asset_address, cg_id)
                continue

            try:
                price_in_eth = cg_prices[cg_id]
                price_wei = int(price_in_eth * (10**18))

                prices_accumulator.prices[asset_address] = price_wei

                token_decimals = await self.get_token_decimals(asset_address)
                prices_accumulator.decimals[asset_address] = token_decimals

                priced_count += 1
                logger.info(
                    "CoinGecko priced %s: %d wei (id=%s, decimals=%d)",
                    asset_address, price_wei, cg_id, token_decimals,
                )
            except Exception as e:
                logger.warning("Failed to process CoinGecko price for %s: %s", asset_address, e)
                continue

        logger.info("CoinGecko adapter priced %d/%d tokens (ETH base)", priced_count, len(tokens_to_price))
        self.validate_prices(prices_accumulator)
        return prices_accumulator

    async def _fetch_two_hop_prices(
        self,
        tokens_to_price: dict[str, str],
        prices_accumulator: PriceData,
    ) -> PriceData:
        """Fetch prices via USD two-hop for non-ETH base assets.

        1. Fetch all token prices in USD
        2. Fetch base asset price in USD (via coingecko_base_asset_id)
        3. Convert: price_in_base = (token_usd / base_usd) * 10^18
        """
        # Gather all IDs including the base asset
        assert self.base_asset_id is not None  # Guarded by caller check
        base_asset_id: str = self.base_asset_id
        coingecko_ids = list(set(tokens_to_price.values()))
        if base_asset_id not in coingecko_ids:
            coingecko_ids.append(base_asset_id)

        try:
            cg_prices_usd = await self._fetch_prices_batch(coingecko_ids, vs_currency="usd")
        except Exception as e:
            logger.error("Failed to fetch CoinGecko USD prices: %s", e)
            return prices_accumulator

        # Get base asset USD price
        base_usd_price = cg_prices_usd.get(base_asset_id)
        if not base_usd_price or base_usd_price <= 0:
            logger.error(
                "No CoinGecko USD price for base asset (id: %s), cannot do two-hop",
                self.base_asset_id,
            )
            return prices_accumulator

        logger.info(
            "CoinGecko base asset USD price: %s = $%.4f",
            self.base_asset_id,
            base_usd_price,
        )

        priced_count = 0
        for asset_address, cg_id in tokens_to_price.items():
            if asset_address in prices_accumulator.prices:
                logger.debug(
                    "Skipping %s - already priced by higher-priority adapter",
                    asset_address,
                )
                continue

            if cg_id not in cg_prices_usd:
                logger.warning("No CoinGecko USD price for %s (id: %s)", asset_address, cg_id)
                continue

            try:
                # Convert: token_usd / base_usd = how many base asset units per 1 token
                token_usd_price = cg_prices_usd[cg_id]
                price_in_base = token_usd_price / base_usd_price
                price_wei = int(price_in_base * (10**18))

                prices_accumulator.prices[asset_address] = price_wei

                token_decimals = await self.get_token_decimals(asset_address)
                prices_accumulator.decimals[asset_address] = token_decimals

                priced_count += 1
                logger.info(
                    "CoinGecko two-hop priced %s: %d wei ($%.4f / $%.4f = %.6f base, id=%s, decimals=%d)",
                    asset_address, price_wei, token_usd_price, base_usd_price,
                    price_in_base, cg_id, token_decimals,
                )
            except Exception as e:
                logger.warning("Failed to process CoinGecko price for %s: %s", asset_address, e)
                continue

        logger.info(
            "CoinGecko adapter priced %d/%d tokens (USD two-hop via %s)",
            priced_count, len(tokens_to_price), self.base_asset_id,
        )

        self.validate_prices(prices_accumulator)
        return prices_accumulator
