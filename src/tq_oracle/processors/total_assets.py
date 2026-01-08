from __future__ import annotations

from ..adapters.price_adapters.base import PriceData
from ..processors.asset_aggregator import AggregatedAssets


def calculate_total_assets(
    aggregated_assets: AggregatedAssets,
    prices: PriceData,
) -> int:
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

    return sum(
        aggregated_assets.assets[i] * prices.prices[i] // (10 ** prices.decimals[i])
        for i in aggregated_assets.assets
    )
