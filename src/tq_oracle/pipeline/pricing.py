"""Price fetching and validation."""

from __future__ import annotations


from ..abi import fetch_subvault_addresses
from ..adapters import PRICE_ADAPTERS
from ..adapters.price_adapters.aliases import apply_aliases, resolve_aliases
from ..adapters.price_adapters.base import PriceData
from ..checks.price_validators import PriceValidationError, run_price_validations
from ..processors import (
    calculate_total_assets,
    derive_final_prices,
)
from ..report.subvault_breakdown import log_subvault_breakdown
from .context import PipelineContext


async def price_assets(ctx: PipelineContext) -> None:
    """Fetch prices for assets and validate them.

    Args:
        ctx: Pipeline context containing state and aggregated assets

    Sets the price data, total assets, and final prices in the context.

    Raises:
        PriceValidationError: If price validation fails
    """
    s = ctx.state.settings
    log = ctx.state.logger
    aggregated = ctx.aggregated_required

    asset_addresses = list(aggregated.assets)
    log.info("Fetching prices for %d assets...", len(asset_addresses))
    price_data: PriceData = PriceData(base_asset=ctx.base_asset_required, prices={})

    # Resolve cross-chain price aliases (substitute before pricing, copy back after)
    alias_resolution = resolve_aliases(asset_addresses, s.price_aliases)
    pricing_addresses = alias_resolution.pricing_addresses

    price_adapters = [AdapterClass(s) for AdapterClass in PRICE_ADAPTERS]
    for price_adapter in price_adapters:
        price_data = await price_adapter.fetch_prices(pricing_addresses, price_data)
        log.debug("Price adapter returned %d prices", len(price_data.prices))

    apply_aliases(price_data, alias_resolution)

    log.info("Running price validations...")
    try:
        await run_price_validations(s, price_data)
        log.info("Price validations passed successfully")
    except PriceValidationError as e:
        log.error("Price validations failed: %s", e)
        raise

    log.info("Calculating total assets in base asset...")
    log.debug(f"Assets found: {aggregated}")
    log.debug(f"Price data: {price_data}")
    total_assets = calculate_total_assets(aggregated, price_data, s.base_asset_decimals)
    log.debug("Total assets in base asset: %d", total_assets)

    log.info("Deriving final prices via OracleHelper...")
    excluded_for_oracle = getattr(aggregated, "tvl_only_assets", set())
    final_prices = await derive_final_prices(
        s,
        total_assets,
        price_data,
        excluded_assets=excluded_for_oracle,
    )

    ctx.price_data = price_data
    ctx.total_assets = total_assets
    ctx.final_prices = final_prices

    # Log per-subvault breakdown
    if ctx.subvault_asset_map:
        log.info("Per-subvault asset breakdown (mainnet):")
        subvault_addresses = await fetch_subvault_addresses(s)

        # Log each subvault's breakdown
        for subvault_addr in subvault_addresses:
            assets_for_subvault = ctx.subvault_asset_map.get(subvault_addr.lower(), [])
            await log_subvault_breakdown(
                subvault_addr, assets_for_subvault, price_data, s,
                base_asset_decimals=s.base_asset_decimals,
            )

        # Log extra addresses breakdown
        if hasattr(ctx, "extra_addresses_assets") and ctx.extra_addresses_assets:
            log.info("Extra addresses asset breakdown:")
            for extra_addr, assets_for_extra in ctx.extra_addresses_assets.items():
                await log_subvault_breakdown(
                    f"Extra Address: {extra_addr}", assets_for_extra, price_data, s,
                    base_asset_decimals=s.base_asset_decimals,
                )

    # Log satellite chain subvault breakdowns
    if ctx.chain_results:
        from .multi_chain import _build_chain_settings

        for chain_result in ctx.chain_results:
            if not chain_result.subvault_asset_map:
                continue

            log.info(
                "Per-subvault asset breakdown (%s):", chain_result.chain_name
            )

            # Build chain-specific settings so get_token_decimals uses the right RPC
            chain_cfg = chain_result.chain_config
            block_number = chain_result.block_number or chain_cfg.block_number or 0
            chain_settings = _build_chain_settings(s, chain_cfg, block_number)

            for subvault_addr, assets_list in chain_result.subvault_asset_map.items():
                await log_subvault_breakdown(
                    subvault_addr, assets_list, price_data, chain_settings,
                    base_asset_decimals=s.base_asset_decimals,
                )
