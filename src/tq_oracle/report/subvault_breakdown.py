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
    base_asset_decimals: int = 18,
) -> None:
    """Log detailed asset breakdown for a single subvault.

    Args:
        subvault_address: Address of the subvault
        assets: List of assets held by this subvault
        prices: Price data for calculating base asset values
        config: Oracle settings for token decimals lookup
        base_asset_decimals: Decimals of the vault's base asset
    """
    if not assets:
        logger.info("  [%s] No assets", subvault_address[:10])
        return

    # Group assets by address
    asset_groups: dict[str, list[int]] = defaultdict(list)
    for asset in assets:
        asset_groups[asset.asset_address.lower()].append(asset.amount)

    logger.info("  ┌─ Subvault: %s", subvault_address)

    base_asset_addr = prices.base_asset.lower()
    total_base_value = 0
    for asset_addr, amounts in sorted(asset_groups.items()):
        total_amount = sum(amounts)

        # Get asset symbol if available
        symbol = None
        for key, addr in config.assets.items():
            if isinstance(addr, str) and addr.lower() == asset_addr:
                symbol = key
                break

        # Get token decimals for proper display formatting
        decimals = await get_token_decimals(asset_addr, config)

        # Calculate value in base asset
        if asset_addr == base_asset_addr:
            # Base asset has 1:1 price with itself
            base_value = total_amount
            total_base_value += base_value
        else:
            # Try both lowercase and original case for price lookup
            base_value = 0
            price_key = None
            for key in prices.prices.keys():
                if key.lower() == asset_addr:
                    price_key = key
                    break

            if price_key:
                price = prices.prices[price_key]
                # Price is in D18 (base asset per token with 18-decimal normalization)
                # Compute value in D18, then convert to native base asset units
                amount_normalized = total_amount * (10 ** (18 - decimals))
                base_value_d18 = (amount_normalized * price) // (10**18)
                # Convert D18 to native base asset units (e.g., divide by 10^12 for 6-decimal base)
                base_value = base_value_d18 // (10 ** (18 - base_asset_decimals))
                total_base_value += base_value
            else:
                # Asset has no price data
                logger.debug(f"No price found for asset {asset_addr}")

        # Format display - show symbol and truncated address
        if symbol:
            display_name = f"{symbol}"
        else:
            display_name = f"{asset_addr[:10]}..."

        amount_decimal = total_amount / (
            10**decimals
        )  # Use correct decimals for display
        base_value_decimal = base_value / 10**base_asset_decimals

        # Show negative amounts differently
        sign = "-" if total_amount < 0 else " "

        logger.info(
            "  │  %s %s %s (%.6f base) [%s]",
            sign,
            display_name.ljust(20),
            f"{abs(amount_decimal):,.6f}".rjust(20),
            base_value_decimal,
            asset_addr,
        )

    total_base_decimal = total_base_value / 10**base_asset_decimals
    logger.info("  └─ Total Value: %.6f (base asset)", total_base_decimal)
