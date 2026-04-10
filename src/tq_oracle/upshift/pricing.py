"""Price fetching for Upshift pipeline — reuses existing price adapters."""

from __future__ import annotations

from ..adapters import PRICE_ADAPTERS
from ..adapters.price_adapters.base import PriceData
from ..checks.price_validators import run_price_validations
from ..processors.asset_aggregator import AggregatedAssets
from ..state import AppState


async def price_all_assets(
    state: AppState,
    aggregated: AggregatedAssets,
    base_asset: str,
) -> PriceData:
    """Fetch prices for all aggregated assets using the standard price adapter chain.

    Same adapter chain as the mellow pipeline (Manual → Chainlink → CoinGecko →
    CowSwap → ETH) but without OracleHelper derivation or supported-asset filtering.

    Args:
        state: Application state with settings
        aggregated: Aggregated asset balances
        base_asset: Base asset address (price set to 0)

    Returns:
        PriceData with prices and decimals for all assets

    Raises:
        PriceValidationError: If price validation fails
    """
    s = state.settings
    log = state.logger

    asset_addresses = list(aggregated.assets)
    log.info("Fetching prices for %d assets...", len(asset_addresses))

    price_data = PriceData(base_asset=base_asset, prices={})

    price_adapters = [AdapterClass(s) for AdapterClass in PRICE_ADAPTERS]
    for price_adapter in price_adapters:
        price_data = await price_adapter.fetch_prices(asset_addresses, price_data)
        log.debug("Price adapter returned %d prices", len(price_data.prices))

    log.info("Running price validations...")
    await run_price_validations(s, price_data)
    log.info("Price validations passed")

    return price_data
