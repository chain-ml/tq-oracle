"""Manual price adapter for fixed price overrides.

This adapter allows you to set fixed prices for any asset,
bypassing all other price sources. Useful for:
- Testing with known prices
- Overriding noisy CoW Swap prices
- Setting prices for assets without other sources
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from web3 import Web3

from ...abi import load_erc20_abi
from .base import BasePriceAdapter, PriceData

if TYPE_CHECKING:
    from ...settings import OracleSettings

logger = logging.getLogger(__name__)


class ManualPriceAdapter(BasePriceAdapter):
    """Adapter for manually configured fixed prices.

    This adapter has HIGHEST priority and will override any prices
    set by other adapters. Configured prices are applied as-is without
    any normalization.

    Configuration:
        manual_prices: dict[str, int] = {
            "0xTokenAddress": 333333,  # Price in wei (18 decimals, normalized)
        }

    Example:
        To set USDC price to 0.000333 ETH (with 6 decimals):
        manual_prices = {
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": 333333
        }

        Calculation:
        - USDC has 6 decimals
        - 0.000333 ETH = 333333333333333 in 18 decimals
        - Normalized: 333333333333333 // 10^(18-6) = 333333
    """

    def __init__(self, config: OracleSettings):
        """Initialize manual price adapter.

        Args:
            config: Oracle settings with manual_prices configuration
        """
        super().__init__(config)

        self.manual_prices = {
            addr.lower(): price for addr, price in config.manual_prices.items()
        }

        if not self.manual_prices:
            self._skip = True
            logger.debug("No manual prices configured, adapter will be skipped")
        else:
            self._skip = False
            logger.info(
                "Manual price adapter initialized with %d price(s)",
                len(self.manual_prices),
            )

        # Get ETH address for base asset validation
        eth_address = config.assets["ETH"]
        if eth_address is None:
            raise ValueError("ETH address is required for Manual price adapter")
        self.eth_address = eth_address

        # Web3 setup for fetching token decimals
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        self.block_number = config.block_number_required
        self._decimals_cache: dict[str, int] = {}

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "manual"

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

    async def fetch_prices(
        self,
        asset_addresses: list[str],
        prices_accumulator: PriceData,
    ) -> PriceData:
        """Apply manually configured prices.

        This adapter simply sets prices for any assets that have
        manual price overrides configured.

        Args:
            asset_addresses: List of asset addresses to potentially price
            prices_accumulator: Existing price accumulator to update

        Returns:
            Updated price accumulator with manual prices applied

        Notes:
            - Prices should already be normalized for token decimals
            - Manual prices override any existing prices in accumulator
            - No validation is performed on manual prices (use with care!)
        """
        if self._skip:
            return prices_accumulator

        if prices_accumulator.base_asset != self.eth_address:
            raise ValueError("Manual price adapter only supports ETH as base asset")

        # Apply manual prices
        priced_count = 0
        for asset_address in asset_addresses:
            addr_lower = asset_address.lower()
            if addr_lower in self.manual_prices:
                price = self.manual_prices[addr_lower]
                prices_accumulator.prices[asset_address] = price

                # Fetch and store token decimals
                token_decimals = await self.get_token_decimals(asset_address)
                prices_accumulator.decimals[asset_address] = token_decimals

                priced_count += 1

                logger.info(
                    "Manual price set for %s: %d wei (decimals=%d)",
                    asset_address,
                    price,
                    token_decimals,
                )

        logger.info("Manual price adapter applied %d price(s)", priced_count)

        # Note: We skip validate_prices() to allow manual 0 prices if needed
        # self.validate_prices(prices_accumulator)

        return prices_accumulator
