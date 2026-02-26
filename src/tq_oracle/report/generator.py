from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..processors.asset_aggregator import AggregatedAssets
from ..processors.oracle_helper import FinalPrices


@dataclass
class OracleReport:
    """Oracle report containing asset data and prices."""

    vault_address: str
    base_asset: str
    tvl_in_base_asset: int
    total_assets: dict[str, int]
    final_prices: dict[str, int]
    asset_decimals: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        """Convert report to dictionary format."""
        return asdict(self)


async def generate_report(
    vault_address: str,
    base_asset: str,
    tvl_in_base_asset: int,
    aggregated_assets: AggregatedAssets,
    final_prices: FinalPrices,
    asset_decimals: dict[str, int] | None = None,
) -> OracleReport:
    """Generate an oracle report from processed data.

    Args:
        vault_address: The vault contract address
        base_asset: The address of the base asset used for reporting
        tvl_in_base_asset: Total value locked expressed in the base asset (18 decimals)
        aggregated_assets: Aggregated asset balances per asset address
        final_prices: Final oracle prices
        asset_decimals: Token decimals per asset address (from price adapters)

    Returns:
        Complete oracle report ready for publishing

    This corresponds to the "Generate Report" step in the flowchart.
    """
    return OracleReport(
        vault_address=vault_address,
        base_asset=base_asset,
        tvl_in_base_asset=tvl_in_base_asset,
        total_assets=aggregated_assets.assets,
        final_prices=final_prices.prices,
        asset_decimals=asset_decimals or {},
    )
