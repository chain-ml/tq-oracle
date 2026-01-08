from __future__ import annotations

import asyncio
import logging
from decimal import Decimal

import backoff
import requests
from web3 import Web3

from ...abi import load_erc20_abi
from ...settings import Network, OracleSettings
from .base import BasePriceAdapter, PriceData

logger = logging.getLogger(__name__)


class CowSwapAdapter(BasePriceAdapter):
    """Adapter for querying CoW Protocol prices via the quote API.

    This adapter fetches prices for all assets EXCEPT those handled by specialized adapters.
    Assets on the list (ETH, WETH) are skipped as they're handled by ETHAdapter.
    """

    eth_address: str

    NETWORK_API_URLS: dict[Network, str] = {
        Network.MAINNET: "https://api.cow.fi/mainnet/api/v1",
        Network.SEPOLIA: "https://api.cow.fi/sepolia/api/v1",
        Network.BASE: "https://api.cow.fi/base/api/v1",
    }

    def __init__(self, config: OracleSettings):
        super().__init__(config)
        self.api_base_url = self.NETWORK_API_URLS[config.network]
        self.vault_rpc = config.vault_rpc
        self.block_number = config.block_number_required
        assets = config.assets
        eth_address = assets["ETH"]
        if eth_address is None:
            raise ValueError("ETH address is required for CowSwap adapter")
        self.eth_address = eth_address
        self._oseth_address = assets.get("OSETH")

        weth_address = assets.get("WETH")
        if weth_address is None:
            raise ValueError("WETH address is required for CowSwap adapter")
        self.weth_address = weth_address

        self._decimals_cache: dict[str, int] = {}

        self.skipped_assets = {
            addr.lower()
            for addr in [
                assets["ETH"],
                assets["WETH"],
                self._oseth_address,
            ]
            if addr is not None
        }

    @property
    def adapter_name(self) -> str:
        return "cow_swap"

    async def get_token_decimals(self, token_address: str) -> int:
        """Fetch token decimals from on-chain contract, with caching.

        Args:
            token_address: The token contract address

        Returns:
            Number of decimals for the token
        """
        if token_address in self._decimals_cache:
            return self._decimals_cache[token_address]

        w3 = Web3(Web3.HTTPProvider(self.vault_rpc))
        erc20_abi = load_erc20_abi()
        token_contract = w3.eth.contract(
            address=w3.to_checksum_address(token_address),
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
        logger.debug(f" Fetched decimals for {token_address}: {decimals}")

        return decimals

    @backoff.on_exception(
        backoff.expo,
        (requests.exceptions.RequestException, requests.exceptions.HTTPError),
        max_time=5,
        giveup=lambda e: isinstance(e, requests.exceptions.HTTPError)
        and e.response is not None
        and e.response.status_code != 429,
        jitter=backoff.full_jitter,
    )
    async def fetch_native_price(self, token_address: str) -> str:
        """Fetch price (in ETH) for 1e18 units of a token using CoW Protocol quote API.

        Args:
            token_address: The token contract address

        Returns:
            Price in ETH for 1e18 units of the token as a string
        """
        url = f"{self.api_base_url}/quote"
        sell_amount = 10**18

        data = {
            "sellToken": Web3.to_checksum_address(token_address),
            "buyToken": Web3.to_checksum_address(self.weth_address),
            "sellAmountBeforeFee": str(sell_amount),
            "from": Web3.to_checksum_address(self.weth_address),  # dummy address for quote
            "kind": "sell",
            "priceQuality": "optimal",
        }

        logger.debug(f"Calling {url} for {token_address}")
        response = await asyncio.to_thread(
            lambda: requests.post(url, json=data, timeout=10.0)
        )
        response.raise_for_status()
        result = response.json()

        quote = result.get("quote", {})
        buy_amount_wei = int(quote.get("buyAmount", "0"))
        # Scale down to ETH (same format as old native_price endpoint)
        price_eth = Decimal(buy_amount_wei) / Decimal(10**18)
        logger.debug(f"Quote for {token_address}: {price_eth} ETH for 1e18 units")
        return str(price_eth)

    async def fetch_prices(
        self, asset_addresses: list[str], prices_accumulator: PriceData
    ) -> PriceData:
        """Fetch and accumulate asset prices from CoW Swap API.

        Args:
            asset_addresses: List of asset contract addresses to get prices for.
            prices_accumulator: Existing price accumulator to update. Must
                have base_asset set to ETH (wei). All prices are 18-decimal values
                representing wei per 1 unit of the asset.

        Returns:
            The same accumulator with CoW Swap-derived prices merged in.

        Notes:
            - Only ETH as base asset is supported.
            - Uses CoW Protocol quote API to get prices (sells 1e18 units for WETH).
            - Processes all assets EXCEPT those on the skipped_assets (ETH, WETH).
            - Token decimals are fetched dynamically from on-chain and cached.
        """
        if prices_accumulator.base_asset != self.eth_address:
            raise ValueError("CowSwap adapter only supports ETH as base asset")

        for asset_address in asset_addresses:
            if asset_address.lower() in self.skipped_assets:
                logger.debug(f" Skipping asset: {asset_address}")
                continue

            try:
                token_decimals = await self.get_token_decimals(asset_address)
                native_price = await self.fetch_native_price(asset_address)
                price_wei = int(Decimal(native_price) * 10**18)
                price_wei_normalized = price_wei // (10 ** (18 - token_decimals))
                logger.debug(
                    f" Fetched price for {asset_address}: {price_wei_normalized} wei (decimals: {token_decimals})"
                )
                prices_accumulator.prices[asset_address] = price_wei_normalized

            except Exception as e:
                logger.warning(f" Failed to fetch price for {asset_address}: {e}")
                continue

        self.validate_prices(prices_accumulator)

        return prices_accumulator
