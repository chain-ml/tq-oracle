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
from typing import Any, TYPE_CHECKING

from web3 import Web3

from ..abi import load_erc20_abi
from ..adapters.asset_adapters import get_adapter_class, parse_adapter_name
from ..adapters.asset_adapters.base import AssetData
from ..adapters.bridge_adapters import (
    BaseBridgeAdapter,
    BridgeReconciliationResult,
    CCTPBridgeAdapter,
    EVMCoreBridgeAdapter,
)
from ..processors import compute_total_aggregated_assets

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
    subvault_asset_map: dict[str, list[AssetData]] = field(default_factory=dict)
    block_number: int | None = None


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

        # Standard EVM chain — scan idle balances + run adapter chains
        return await _collect_evm_chain_assets(config, chain_config)

    except Exception as e:
        logger.error(f"Failed to collect assets from chain {chain_name}: {e}")
        return ChainAssetResult(
            chain_name=chain_name,
            chain_config=chain_config,
            error=str(e),
            success=False,
        )


def _build_chain_settings(
    base: OracleSettings,
    chain_config: ChainConfig,
    block_number: int,
) -> OracleSettings:
    """Build a settings overlay for a specific chain.

    Creates a copy of the base settings with chain-specific values for
    RPC, block number, network, and adapters.
    """
    from ..settings import Network

    try:
        network = Network(chain_config.network)
    except ValueError:
        network = base.network

    return base.model_copy(update={
        "vault_rpc": chain_config.rpc,
        "block_number": block_number,
        "network": network,
        "subvault_adapters": chain_config.subvault_adapters,
        "adapters": chain_config.adapters,
    })


def _sanitize_adapter_kwargs(values: dict[str, Any]) -> dict[str, Any]:
    """Drop None or empty collection values from adapter kwargs."""
    return {
        key: value
        for key, value in values.items()
        if value is not None and (not isinstance(value, (list, dict)) or value)
    }


def _create_adapter(
    settings: OracleSettings,
    adapter_name: str,
    subvault_config: dict[str, Any],
    adapter_defaults: dict[str, Any],
):
    """Create an adapter instance with proper config resolution."""
    base_name, instance_name = parse_adapter_name(adapter_name)
    adapter_class = get_adapter_class(adapter_name)

    adapter_overrides: dict[str, Any] = {}
    overrides_config = subvault_config.get("adapter_overrides", {})
    if isinstance(overrides_config, dict):
        candidate = overrides_config.get(adapter_name)
        if candidate is None:
            candidate = overrides_config.get(adapter_name.lower())
        if candidate is None:
            candidate = overrides_config.get(base_name)
        if isinstance(candidate, dict):
            adapter_overrides = candidate

    if base_name == "aave_v3":
        instance_config = settings.adapters.get_aave_v3_config(instance_name)
        if instance_name and instance_config is None:
            raise ValueError(
                f"No configuration found for adapter instance '{adapter_name}'. "
                f'Define [[adapters.aave_v3]] with name = "{instance_name}" in your config.'
            )
        if instance_config:
            defaults = _sanitize_adapter_kwargs(
                instance_config.model_dump(exclude_none=True)
            )
        else:
            defaults = adapter_defaults.get(base_name, {})
    else:
        defaults = adapter_defaults.get(base_name, {})

    adapter_kwargs = _sanitize_adapter_kwargs({**defaults, **adapter_overrides})

    if adapter_kwargs:
        return adapter_class(settings, **adapter_kwargs)
    return adapter_class(settings)


async def _run_adapter_chain(
    settings: OracleSettings,
    subvault_addr: str,
    adapter_names: list[str],
    subvault_config: dict[str, Any],
) -> list[AssetData]:
    """Run adapters sequentially, passing results forward (adapter chaining)."""
    adapter_defaults = {
        name.lower(): value
        for name, value in settings.adapters.model_dump(
            exclude_none=True, exclude_defaults=True
        ).items()
        if isinstance(value, dict)
    }

    accumulated: list[AssetData] | None = None

    for adapter_name in adapter_names:
        adapter = _create_adapter(
            settings, adapter_name, subvault_config, adapter_defaults
        )
        new_assets = await adapter.fetch_assets(subvault_addr, accumulated)
        accumulated = new_assets
        logger.debug(
            "Chain subvault %s → %s returned %d assets",
            subvault_addr,
            adapter_name,
            len(new_assets) if new_assets else 0,
        )

    return accumulated or []


async def _scan_idle_balances(
    w3: Web3,
    address: str,
    token_addresses: list[str],
    block_number: int,
    settings: OracleSettings,
) -> list[AssetData]:
    """Scan ERC20 balances for tracked tokens at the given address."""
    checksum_address = Web3.to_checksum_address(address)
    erc20_abi = load_erc20_abi()
    sem = asyncio.Semaphore(settings.rpc_max_concurrent_calls)

    async def fetch_balance(token_addr: str) -> AssetData | None:
        async with sem:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_addr), abi=erc20_abi
            )
            balance = await asyncio.wait_for(
                asyncio.to_thread(
                    contract.functions.balanceOf(checksum_address).call,
                    block_identifier=block_number,
                ),
                timeout=settings.rpc_timeout,
            )
        if balance > 0:
            return AssetData(asset_address=token_addr, amount=balance)
        return None

    results = await asyncio.gather(
        *[fetch_balance(addr) for addr in token_addresses],
        return_exceptions=True,
    )

    assets: list[AssetData] = []
    for token_addr, result in zip(token_addresses, results):
        if isinstance(result, Exception):
            logger.error(
                "Failed to fetch balance for %s at %s: %s",
                token_addr, address, result,
            )
            raise result
        if result is not None:
            assets.append(result)

    return assets


async def _collect_evm_chain_assets(
    config: OracleSettings,
    chain_config: ChainConfig,
) -> ChainAssetResult:
    """Collect assets from a standard EVM chain.

    For each configured subvault:
    1. Scan idle balances for tracked tokens
    2. Run adapter chains (euler_v2, morpho_blue, etc.)

    Args:
        config: Base oracle settings
        chain_config: Chain-specific configuration

    Returns:
        ChainAssetResult with all assets from this chain
    """
    chain_name = chain_config.name

    if not chain_config.rpc:
        logger.warning("Chain %s has no RPC configured, skipping", chain_name)
        return ChainAssetResult(
            chain_name=chain_name,
            chain_config=chain_config,
        )

    w3 = Web3(Web3.HTTPProvider(chain_config.rpc))
    block_number = chain_config.block_number
    if block_number is None:
        block_number = w3.eth.block_number
        logger.debug("Chain %s: resolved block number %d", chain_name, block_number)

    chain_settings = _build_chain_settings(config, chain_config, block_number)

    tracked_token_addresses = [
        Web3.to_checksum_address(addr) for addr in chain_config.tracked_tokens.values()
    ]

    all_assets: list[list[AssetData]] = []
    subvault_asset_map: dict[str, list[AssetData]] = {}

    for sv_cfg in chain_config.subvault_adapters:
        subvault_addr = sv_cfg["subvault_address"]
        subvault_assets: list[AssetData] = []

        # 1. Scan idle balances for tracked tokens
        if tracked_token_addresses:
            idle_assets = await _scan_idle_balances(
                w3, subvault_addr, tracked_token_addresses, block_number, chain_settings
            )
            subvault_assets.extend(idle_assets)
            logger.debug(
                "Chain %s / Subvault %s: %d idle balances",
                chain_name, subvault_addr, len(idle_assets),
            )

        # 2. Run adapter chain
        adapter_names = sv_cfg.get("additional_adapters", [])
        if adapter_names:
            chain_assets = await _run_adapter_chain(
                chain_settings, subvault_addr, adapter_names, sv_cfg
            )
            subvault_assets.extend(chain_assets)
            logger.debug(
                "Chain %s / Subvault %s: %d adapter assets",
                chain_name, subvault_addr, len(chain_assets),
            )

        all_assets.append(subvault_assets)
        subvault_asset_map[subvault_addr.lower()] = subvault_assets
        logger.info(
            "Chain %s / Subvault %s: %d total assets",
            chain_name, subvault_addr, len(subvault_assets),
        )

    # Flatten for result
    flat_assets = [asset for assets_list in all_assets for asset in assets_list]
    total = sum(a.amount for a in flat_assets)

    logger.info(
        "Chain %s: collected %d assets from %d subvaults, total value: %d",
        chain_name, len(flat_assets), len(chain_config.subvault_adapters), total,
    )

    return ChainAssetResult(
        chain_name=chain_name,
        chain_config=chain_config,
        assets=flat_assets,
        total_amount=total,
        subvault_asset_map=subvault_asset_map,
        block_number=block_number,
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
