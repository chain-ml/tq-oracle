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
    3. Fetches prices in ETH directly
    4. Handles both free and Pro API tiers

    Benefits:
    - Stable pricing from CoinGecko's aggregated data
    - Efficient batching (one API call for all tokens)
    - No on-chain calls required
    - Good fallback pricing source

    Configuration:
        coingecko_enabled: bool = True
        coingecko_api_key: str = "your_key"  # Optional - uses free tier if not provided
        coingecko_token_ids: dict[str, str] = {
            "0xUSDC": "usd-coin",
            "0xUSDT": "tether",
        }
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

        # Web3 setup for fetching token decimals
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        self.block_number = config.block_number_required
        self._decimals_cache: dict[str, int] = {}

        logger.info(
            "CoinGecko adapter initialized: tokens=%d, api=%s",
            len(self.token_ids),
            "Pro" if self.api_key else "Free",
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
    ) -> dict[str, float]:
        """Fetch prices for multiple tokens in one API call.

        Args:
            coingecko_ids: List of CoinGecko IDs to fetch prices for

        Returns:
            Dict mapping coingecko_id -> price_in_eth

        Example Response:
            {
                "usd-coin": { "eth": 0.000333 },
                "tether": { "eth": 0.000332 }
            }
        """
        # Build comma-separated ID list
        ids_param = ",".join(coingecko_ids)

        # API endpoint
        url = f"{self.api_base_url}/simple/price"

        params = {
            "ids": ids_param,
            "vs_currencies": "eth",
            "precision": "18",  # Maximum precision
        }

        headers = {}
        if self.api_key:
            headers["x-cg-pro-api-key"] = self.api_key

        logger.debug("Fetching CoinGecko prices: ids=%s", ids_param)

        response = await asyncio.to_thread(
            requests.get,
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
            if cg_id in data and "eth" in data[cg_id]:
                prices[cg_id] = float(data[cg_id]["eth"])
            else:
                logger.warning("No price returned for CoinGecko ID: %s", cg_id)

        logger.debug("CoinGecko returned %d prices", len(prices))
        return prices

    async def fetch_prices(
        self,
        asset_addresses: list[str],
        prices_accumulator: PriceData,
    ) -> PriceData:
        """Fetch and accumulate token prices from CoinGecko.

        This adapter batches all configured tokens into a single API call.
        Only processes assets that have CoinGecko ID mappings.

        Args:
            asset_addresses: List of asset addresses to potentially price
            prices_accumulator: Existing price accumulator to update

        Returns:
            Updated price accumulator with CoinGecko prices

        Notes:
            - Batches all tokens into one API call
            - Only processes tokens in coingecko_token_ids mapping
            - Prices are in 18-decimal wei per 1 unit of asset
            - Automatically normalizes for token decimals
        """
        if self._skip:
            return prices_accumulator

        if prices_accumulator.base_asset != self.eth_address:
            raise ValueError("CoinGecko adapter only supports ETH as base asset")

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

        # Batch fetch all prices in one API call
        try:
            coingecko_ids = list(set(tokens_to_price.values()))  # Unique IDs
            cg_prices = await self._fetch_prices_batch(coingecko_ids)
        except Exception as e:
            logger.error("Failed to fetch CoinGecko prices: %s", e)
            return prices_accumulator

        # Process each token
        priced_count = 0
        for asset_address, cg_id in tokens_to_price.items():
            if cg_id not in cg_prices:
                logger.warning(
                    "No CoinGecko price for %s (id: %s)",
                    asset_address,
                    cg_id,
                )
                continue

            try:
                # Get price in ETH (float, already at maximum precision from API)
                price_in_eth = cg_prices[cg_id]

                # Convert to 18-decimal integer
                # CoinGecko returns price per 1 whole token in ETH
                # e.g., for USDC: 0.000333 ETH per 1 USDC -> 333000000000000 wei
<<<<<<< HEAD
                # This format works directly with calculate_total_assets: amount * price // 10^token_decimals
                price_wei = int(price_in_eth * (10**18))

                prices_accumulator.prices[asset_address] = price_wei

                # Fetch and store token decimals
                token_decimals = await self.get_token_decimals(asset_address)
                prices_accumulator.decimals[asset_address] = token_decimals

                priced_count += 1

                logger.info(
                    "CoinGecko priced %s: %d wei (id=%s, decimals=%d)",
=======
                # This format works directly with calculate_total_assets: amount * price // 10^18
                price_wei = int(price_in_eth * (10**18))

                prices_accumulator.prices[asset_address] = price_wei
                priced_count += 1

                logger.info(
                    "CoinGecko priced %s: %d wei (id=%s)",
>>>>>>> cce8f43 (feat: add coingecko, chainlink price adapters, add new logging and reporitng)
                    asset_address,
                    price_wei,
                    cg_id,
                    token_decimals,
                )

            except Exception as e:
                logger.warning(
                    "Failed to process CoinGecko price for %s: %s",
                    asset_address,
                    e,
                )
                continue

        logger.info(
            "CoinGecko adapter priced %d/%d tokens",
            priced_count,
            len(tokens_to_price),
        )

        self.validate_prices(prices_accumulator)
        return prices_accumulator
