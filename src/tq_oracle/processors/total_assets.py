from __future__ import annotations

from ..adapters.price_adapters.base import PriceData
from ..processors.asset_aggregator import AggregatedAssets


def calculate_total_assets(
    aggregated_assets: AggregatedAssets,
    prices: PriceData,
    base_asset_decimals: int = 18,
) -> int:
    """Calculate total assets in base asset native units.

    Args:
        aggregated_assets: Aggregated asset balances
        prices: Price data with D18 prices and per-asset decimals
        base_asset_decimals: Decimals of the vault's base asset

    Returns:
        Total assets in base asset native units (e.g., 1e18 for 1 ETH, 1e6 for 1 XAUT)
    """
    missing_assets = aggregated_assets.assets.keys() - prices.prices.keys()
    if missing_assets:
        raise ValueError(f"Prices missing for assets: {sorted(missing_assets)}")

    missing_decimals = aggregated_assets.assets.keys() - prices.decimals.keys()
    if missing_decimals:
        raise ValueError(f"Decimals missing for assets: {sorted(missing_decimals)}")

    invalid_prices = [
        (asset_address, price)
        for asset_address, price in prices.prices.items()
        if price <= 0
    ]

    if invalid_prices:
        invalid_details = ", ".join(
            f"{addr}: {price}" for addr, price in invalid_prices
        )
        raise ValueError(f"Invalid prices for assets: {invalid_details}")

    # Sum gives result in D18 representation of base asset
    d18_total = sum(
        aggregated_assets.assets[i] * prices.prices[i] // (10 ** prices.decimals[i])
        for i in aggregated_assets.assets
    )

    # Convert from D18 to base asset native decimals
    if base_asset_decimals < 18:
        return d18_total // (10 ** (18 - base_asset_decimals))
    return d18_total
