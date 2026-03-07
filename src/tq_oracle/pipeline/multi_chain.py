"""Multi-chain asset collection and bridge reconciliation.

This module provides the orchestration layer for collecting assets across
multiple chains and reconciling in-flight bridge transfers to produce
an accurate cross-chain TVL.

Architecture:
    1. For each configured chain, run adapters in parallel
    2. For each configured bridge, check for in-flight transfers
    3. Aggregate TVL from all chains
    4. Deduct in-flight amounts to prevent double-counting
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..adapters.asset_adapters.base import AssetData
from ..adapters.bridge_adapters import (
    BaseBridgeAdapter,
    BridgeReconciliationResult,
    CCTPBridgeAdapter,
    EVMCoreBridgeAdapter,
)

if TYPE_CHECKING:
    from ..settings import BridgeConfig, ChainConfig, OracleSettings

logger = logging.getLogger(__name__)


# Registry mapping bridge type to adapter class
BRIDGE_ADAPTER_REGISTRY: dict[str, type[BaseBridgeAdapter]] = {
    "cctp": CCTPBridgeAdapter,
    "evm_core": EVMCoreBridgeAdapter,
}


@dataclass
class ChainAssetResult:
    """Result of asset collection for a single chain."""

    chain_name: str
    chain_config: ChainConfig
    assets: list[AssetData] = field(default_factory=list)
    total_amount: int = 0  # In base asset units (wei)
    error: str | None = None
    success: bool = True


@dataclass
class MultiChainAssetResult:
    """Aggregated result from all chains and bridge reconciliation."""

    chain_results: list[ChainAssetResult] = field(default_factory=list)
    bridge_results: list[BridgeReconciliationResult] = field(default_factory=list)

    # Aggregated totals
    gross_tvl: int = 0  # Sum of all chain assets
    inflight_total: int = 0  # Total in-flight across all bridges
    net_tvl: int = 0  # gross_tvl - inflight_total

    # All assets flattened
    all_assets: list[AssetData] = field(default_factory=list)

    # Errors from required chains
    errors: list[str] = field(default_factory=list)


async def collect_chain_assets(
    config: OracleSettings,
    chain_config: ChainConfig,
) -> ChainAssetResult:
    """Collect assets from a single chain.

    Args:
        config: Oracle settings
        chain_config: Configuration for this specific chain

    Returns:
        ChainAssetResult with assets or error
    """
    from ..adapters.asset_adapters import HyperCoreAdapter

    chain_name = chain_config.name
    logger.info(f"Collecting assets from chain: {chain_name}")

    try:
        assets: list[AssetData] = []

        # For HyperCore, use the specialized adapter
        if chain_config.network.lower() in ("hypercore", "hyper_core"):
            adapter = HyperCoreAdapter(config)
            assets = await adapter.fetch_all_assets()
            total = sum(a.amount for a in assets)

            logger.info(
                f"Chain {chain_name}: collected {len(assets)} assets, "
                f"total value: {total}"
            )

            return ChainAssetResult(
                chain_name=chain_name,
                chain_config=chain_config,
                assets=assets,
                total_amount=total,
            )

        # For other chains (mainnet, hyperevm), the existing pipeline handles them
        # This function would be extended to support running standard adapters
        # against different chain configurations
        logger.debug(
            f"Chain {chain_name} uses standard pipeline adapters"
        )

        return ChainAssetResult(
            chain_name=chain_name,
            chain_config=chain_config,
            assets=[],
            total_amount=0,
        )

    except Exception as e:
        logger.error(f"Failed to collect assets from chain {chain_name}: {e}")
        return ChainAssetResult(
            chain_name=chain_name,
            chain_config=chain_config,
            error=str(e),
            success=False,
        )


async def check_bridge_inflight(
    config: OracleSettings,
    bridge_config: BridgeConfig,
) -> BridgeReconciliationResult:
    """Check for in-flight transfers on a bridge.

    Args:
        config: Oracle settings
        bridge_config: Configuration for this bridge

    Returns:
        BridgeReconciliationResult with in-flight details
    """
    bridge_type = bridge_config.type
    logger.info(
        f"Checking {bridge_type} bridge: "
        f"{bridge_config.source_chain} -> {bridge_config.dest_chain}"
    )

    adapter_class = BRIDGE_ADAPTER_REGISTRY.get(bridge_type)
    if not adapter_class:
        logger.warning(f"Unknown bridge type: {bridge_type}")
        return BridgeReconciliationResult(
            bridge_type=bridge_type,
            source_chain=bridge_config.source_chain,
            dest_chain=bridge_config.dest_chain,
            total_inflight_amount=0,
            inflight_count=0,
            inflight_transfers=[],
            error=f"Unknown bridge type: {bridge_type}",
        )

    try:
        adapter = adapter_class(config, bridge_config)
        result = await adapter.get_inflight_transfers()

        if result.inflight_count > 0:
            logger.warning(
                f"Bridge {bridge_type} ({result.source_chain}->{result.dest_chain}): "
                f"{result.inflight_count} in-flight transfer(s), "
                f"total: {result.total_inflight_amount} units"
            )
        else:
            logger.info(
                f"Bridge {bridge_type} ({result.source_chain}->{result.dest_chain}): "
                f"no in-flight transfers"
            )

        return result

    except Exception as e:
        logger.error(f"Bridge check failed for {bridge_type}: {e}")
        return BridgeReconciliationResult(
            bridge_type=bridge_type,
            source_chain=bridge_config.source_chain,
            dest_chain=bridge_config.dest_chain,
            total_inflight_amount=0,
            inflight_count=0,
            inflight_transfers=[],
            error=str(e),
        )


async def collect_multi_chain_assets(
    config: OracleSettings,
) -> MultiChainAssetResult:
    """Collect assets from all configured chains and reconcile bridges.

    This is the main entry point for multi-chain asset collection.

    Args:
        config: Oracle settings with chains and bridges configured

    Returns:
        MultiChainAssetResult with aggregated TVL and breakdown
    """
    result = MultiChainAssetResult()

    # Skip if no chains configured
    if not config.chains:
        logger.debug("No multi-chain configuration found, skipping")
        return result

    logger.info(f"Starting multi-chain asset collection across {len(config.chains)} chain(s)")

    # 1. Collect assets from all chains in parallel
    chain_tasks = [
        collect_chain_assets(config, chain_config)
        for chain_config in config.chains
    ]

    chain_results = await asyncio.gather(*chain_tasks, return_exceptions=True)

    for i, chain_result in enumerate(chain_results):
        chain_config = config.chains[i]

        if isinstance(chain_result, BaseException):
            error_msg = f"Chain {chain_config.name} failed: {chain_result}"
            logger.error(error_msg)
            result.chain_results.append(
                ChainAssetResult(
                    chain_name=chain_config.name,
                    chain_config=chain_config,
                    error=str(chain_result),
                    success=False,
                )
            )
            if chain_config.required:
                result.errors.append(error_msg)
        else:
            result.chain_results.append(chain_result)
            result.all_assets.extend(chain_result.assets)
            result.gross_tvl += chain_result.total_amount

            if not chain_result.success and chain_config.required:
                result.errors.append(
                    f"Chain {chain_config.name}: {chain_result.error}"
                )

    # 2. Check bridges for in-flight transfers
    if config.bridges:
        logger.info(f"Checking {len(config.bridges)} bridge(s) for in-flight transfers")

        bridge_tasks = [
            check_bridge_inflight(config, bridge_config)
            for bridge_config in config.bridges
        ]

        bridge_results = await asyncio.gather(*bridge_tasks, return_exceptions=True)

        for i, bridge_result in enumerate(bridge_results):
            bridge_config = config.bridges[i]

            if isinstance(bridge_result, BaseException):
                logger.error(
                    f"Bridge check failed for {bridge_config.type}: {bridge_result}"
                )
                result.bridge_results.append(
                    BridgeReconciliationResult(
                        bridge_type=bridge_config.type,
                        source_chain=bridge_config.source_chain,
                        dest_chain=bridge_config.dest_chain,
                        total_inflight_amount=0,
                        inflight_count=0,
                        inflight_transfers=[],
                        error=str(bridge_result),
                    )
                )
            else:
                result.bridge_results.append(bridge_result)
                result.inflight_total += bridge_result.total_inflight_amount

    # 3. Calculate net TVL
    result.net_tvl = result.gross_tvl - result.inflight_total
    if result.net_tvl < 0:
        raise ValueError(
            f"Negative net TVL: gross={result.gross_tvl}, "
            f"inflight={result.inflight_total}. "
            f"In-flight amounts exceed gross TVL — possible bridge reconciliation error."
        )

    # Log summary
    logger.info(
        f"Multi-chain collection complete: "
        f"gross_tvl={result.gross_tvl}, "
        f"inflight={result.inflight_total}, "
        f"net_tvl={result.net_tvl}"
    )

    if result.errors:
        logger.error(f"Multi-chain collection had {len(result.errors)} error(s)")
        for error in result.errors:
            logger.error(f"  - {error}")

    return result


def format_multi_chain_report(result: MultiChainAssetResult) -> str:
    """Format multi-chain results as a human-readable report.

    Args:
        result: Multi-chain asset collection result

    Returns:
        Formatted string report
    """
    lines = ["", "=== Cross-Chain TVL Report ===", ""]

    # Per-chain breakdown
    for chain_result in result.chain_results:
        role = chain_result.chain_config.role.upper()
        status = "OK" if chain_result.success else "ERROR"

        lines.append(f"{chain_result.chain_name} ({role}) [{status}]:")

        if chain_result.error:
            lines.append(f"  Error: {chain_result.error}")
        else:
            lines.append(f"  Total: {chain_result.total_amount} wei")
            lines.append(f"  Assets: {len(chain_result.assets)}")

        lines.append("")

    # Bridge reconciliation
    if result.bridge_results:
        lines.append("In-Flight Transfers:")
        for bridge_result in result.bridge_results:
            direction = f"{bridge_result.source_chain}->{bridge_result.dest_chain}"
            if bridge_result.error:
                lines.append(f"  {bridge_result.bridge_type} {direction}: ERROR - {bridge_result.error}")
            elif bridge_result.inflight_count > 0:
                lines.append(
                    f"  {bridge_result.bridge_type} {direction}: "
                    f"{bridge_result.inflight_count} pending, "
                    f"total: {bridge_result.total_inflight_amount} units"
                )
            else:
                lines.append(f"  {bridge_result.bridge_type} {direction}: none")
        lines.append("")

    # Totals
    lines.append("-" * 40)
    lines.append(f"Gross TVL: {result.gross_tvl} wei")
    lines.append(f"In-Flight: -{result.inflight_total} wei")
    lines.append(f"Net TVL:   {result.net_tvl} wei")
    lines.append("")

    return "\n".join(lines)
