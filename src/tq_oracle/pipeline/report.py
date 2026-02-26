"""Report generation."""

from __future__ import annotations

from ..report import generate_report
from ..report import publish_report as publish_report_impl
from .context import PipelineContext


async def build_report(ctx: PipelineContext) -> None:
    """Generate the oracle report.

    Args:
        ctx: Pipeline context containing state, aggregated assets, and final prices

    Sets the report in the context.
    """
    log = ctx.state.logger
    state = ctx.state
    aggregated = ctx.aggregated_required
    final_prices = ctx.final_prices_required

    log.info("Generating report...")
    report = await generate_report(
        state.settings.vault_address_required,
        ctx.price_data_required.base_asset,
        ctx.total_assets_required,
        aggregated,
        final_prices,
        asset_decimals=ctx.price_data_required.decimals,
    )

    ctx.report = report


async def publish_report(ctx: PipelineContext) -> None:
    """Publish the oracle report.

    Args:
        ctx: Pipeline context containing state and report

    Publishes the report to the appropriate destination based on the configuration.
    """
    s = ctx.state.settings
    report = ctx.report_required
    log = ctx.state.logger

    # Log final report summary
    tvl_eth = report.tvl_in_base_asset / 10**18

    log.info("=" * 60)
    log.info("Final Report Summary:")
    log.info(f"  TVL (Total Value Locked): {tvl_eth:,.6f} ETH")
    log.info(f"  Assets Reported: {len(report.final_prices)}")
    log.info("=" * 60)

    log.info("Publishing report (dry_run=%s)...", s.dry_run)

    await publish_report_impl(s, report, ctx.supported_assets)
