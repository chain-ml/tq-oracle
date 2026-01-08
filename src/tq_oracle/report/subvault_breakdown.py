"""Per-subvault asset position breakdown logging."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from web3 import Web3

from ..abi import load_erc20_abi

if TYPE_CHECKING:
    from ..adapters.asset_adapters.base import AssetData
    from ..adapters.price_adapters.base import PriceData
    from ..settings import OracleSettings

logger = logging.getLogger(__name__)

# Cache for token decimals to avoid repeated on-chain calls
_DECIMALS_CACHE: dict[str, int] = {}


async def get_token_decimals(token_address: str, config: OracleSettings) -> int:
    """Fetch token decimals from on-chain contract, with caching.

    Args:
        token_address: The token contract address
        config: Oracle settings with RPC configuration

    Returns:
        Number of decimals for the token
    """
    if token_address in _DECIMALS_CACHE:
        return _DECIMALS_CACHE[token_address]

    # ETH has 18 decimals
    if token_address == "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee":
        _DECIMALS_CACHE[token_address] = 18
        return 18

    w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
    erc20_abi = load_erc20_abi()
    token_contract = w3.eth.contract(
        address=w3.to_checksum_address(token_address),
        abi=erc20_abi,
    )

    decimals = await asyncio.to_thread(
        lambda: int(
            token_contract.functions.decimals().call(
                block_identifier=config.block_number_required
            )
        )
    )

    _DECIMALS_CACHE[token_address] = decimals
    logger.debug(f"Fetched decimals for {token_address}: {decimals}")

    return decimals


async def log_subvault_breakdown(
    subvault_address: str,
    assets: list[AssetData],
    prices: PriceData,
    config: OracleSettings,
) -> None:
    """Log detailed asset breakdown for a single subvault.

    Args:
        subvault_address: Address of the subvault
        assets: List of assets held by this subvault
        prices: Price data for calculating ETH values
        config: Oracle settings for token decimals lookup
    """
    if not assets:
        logger.info("  [%s] No assets", subvault_address[:10])
        return

    # Group assets by address
    asset_groups: dict[str, list[int]] = defaultdict(list)
    for asset in assets:
        asset_groups[asset.asset_address.lower()].append(asset.amount)

    logger.info("  ┌─ Subvault: %s", subvault_address)

    total_eth_value = 0
    for asset_addr, amounts in sorted(asset_groups.items()):
        total_amount = sum(amounts)

        # Get asset symbol if available
        symbol = None
        for key, addr in config.assets.items():
            if addr and addr.lower() == asset_addr:
                symbol = key
                break

        # Get token decimals for proper display formatting
        decimals = await get_token_decimals(asset_addr, config)

        # Calculate ETH value
        # Special case: ETH is the base asset with 1:1 price
        eth_address = "0xeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
        if asset_addr == eth_address:
            # ETH has 1:1 price with itself
            eth_value = total_amount
            total_eth_value += eth_value
        else:
            # Try both lowercase and original case for price lookup
            eth_value = 0
            price_key = None
            for key in prices.prices.keys():
                if key.lower() == asset_addr:
                    price_key = key
                    break

            if price_key:
                price = prices.prices[price_key]
                # Price is in 18 decimals (ETH per token with 18 decimals)
                # We need to normalize: amount_in_18_decimals * price / 10^18
                # amount_in_18_decimals = total_amount * 10^(18-decimals)
                amount_normalized = total_amount * (10 ** (18 - decimals))
                eth_value = (amount_normalized * price) // (10**18)
                total_eth_value += eth_value
            else:
                # Asset has no price data
                logger.debug(f"No price found for asset {asset_addr}")

        # Format display - show symbol and truncated address
        if symbol:
            display_name = f"{symbol}"
        else:
            display_name = f"{asset_addr[:10]}..."

        amount_decimal = total_amount / (10**decimals)  # Use correct decimals for display
        eth_value_decimal = eth_value / 10**18

        # Show negative amounts differently
        sign = "-" if total_amount < 0 else " "

        logger.info(
            "  │  %s %s %s (%.6f ETH) [%s]",
            sign,
            display_name.ljust(20),
            f"{abs(amount_decimal):,.6f}".rjust(20),
            eth_value_decimal,
            asset_addr,
        )

    total_eth_decimal = total_eth_value / 10**18
    logger.info("  └─ Total Value: %.6f ETH", total_eth_decimal)
