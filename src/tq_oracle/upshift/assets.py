"""Asset collection for Upshift pipeline — explicit subaccounts, no vault discovery."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ..abi import load_erc20_abi
from ..adapters.asset_adapters import get_adapter_class, parse_adapter_name
from ..adapters.asset_adapters.base import AssetData
from ..constants import ETH_ASSET
from ..logger import get_logger
from ..processors import compute_total_aggregated_assets
from ..processors.asset_aggregator import AggregatedAssets
from ..settings import OracleSettings, UpshiftChainConfig
from ..state import AppState

logger = get_logger(__name__)


def _sanitize_adapter_kwargs(values: dict[str, Any]) -> dict[str, Any]:
    """Drop None or empty collection values from adapter kwargs."""
    return {
        key: value
        for key, value in values.items()
        if value is not None and (not isinstance(value, (list, dict)) or value)
    }


async def collect_subaccount_assets(
    state: AppState,
    base_asset: str,
) -> tuple[AggregatedAssets, dict[str, list[AssetData]]]:
    """Collect assets from all configured subaccounts.

    For each subaccount:
    1. Scan idle balances for tracked_tokens
    2. Run adapter chain (if additional_adapters configured)

    Args:
        state: Application state with settings
        base_asset: Base asset address (for reference)

    Returns:
        Tuple of (aggregated assets, per-subaccount asset map)
    """
    s = state.settings
    log = state.logger

    subaccount_configs = s.subaccount_adapters
    tracked_token_addresses = [
        Web3.to_checksum_address(addr) for addr in s.tracked_tokens.values()
    ]

    w3 = Web3(Web3.HTTPProvider(s.vault_rpc_required))
    block_number = s.block_number_required

    subaccount_asset_map: dict[str, list[AssetData]] = {}
    all_assets: list[list[AssetData]] = []

    for cfg in subaccount_configs:
        subaccount_addr = cfg["subaccount_address"]
        subaccount_assets: list[AssetData] = []

        # 1. Scan idle balances for tracked tokens
        if tracked_token_addresses:
            idle_assets = await _scan_token_balances(
                w3, subaccount_addr, tracked_token_addresses, block_number, s
            )
            subaccount_assets.extend(idle_assets)
            log.debug(
                "Subaccount %s: %d idle token balances found",
                subaccount_addr,
                len(idle_assets),
            )

        # 2. Run adapter chain
        adapter_names = cfg.get("additional_adapters", [])
        if adapter_names:
            chain_assets = await _run_adapter_chain(
                s, subaccount_addr, adapter_names, cfg, log
            )
            subaccount_assets.extend(chain_assets)
            log.debug(
                "Subaccount %s: %d adapter chain assets found",
                subaccount_addr,
                len(chain_assets),
            )

        subaccount_asset_map[subaccount_addr.lower()] = subaccount_assets
        all_assets.append(subaccount_assets)
        log.info(
            "Subaccount %s: %d total assets", subaccount_addr, len(subaccount_assets)
        )

    aggregated = await compute_total_aggregated_assets(all_assets)
    log.info("Aggregated %d unique assets across %d subaccounts",
             len(aggregated.assets), len(subaccount_configs))
    return aggregated, subaccount_asset_map


@backoff.on_exception(
    backoff.expo, (ProviderConnectionError,), max_time=30, jitter=backoff.full_jitter
)
async def _rpc_call(fn, *args, sem: asyncio.Semaphore, timeout: float,
                    delay: float, jitter: float, **kwargs):
    """Execute RPC call with throttling, timeout, and retry."""
    async with sem:
        result = await asyncio.wait_for(
            asyncio.to_thread(fn, *args, **kwargs),
            timeout=timeout,
        )
    sleep_time = delay + random.random() * jitter
    if sleep_time > 0:
        await asyncio.sleep(sleep_time)
    return result


async def _scan_token_balances(
    w3: Web3,
    address: str,
    token_addresses: list[str],
    block_number: int,
    settings: OracleSettings,
) -> list[AssetData]:
    """Scan ERC20 balances for tracked tokens at the given address.

    Handles native ETH (sentinel 0xEee...) via eth.get_balance.

    Args:
        w3: Web3 instance
        address: Account address to scan
        token_addresses: List of token addresses to check
        block_number: Block number for state snapshot
        settings: Oracle settings (for RPC throttling)

    Returns:
        List of AssetData with non-zero balances
    """
    checksum_address = Web3.to_checksum_address(address)
    erc20_abi = load_erc20_abi()
    eth_sentinel = Web3.to_checksum_address(ETH_ASSET)

    # Gas reserve: configurable token address and amount
    gas_token = Web3.to_checksum_address(settings.gas_token_address)
    gas_reserve_native = int(settings.gas_reserve * 10**settings.gas_token_decimals)

    sem = asyncio.Semaphore(settings.rpc_max_concurrent_calls)
    rpc_kwargs = {
        "sem": sem,
        "timeout": settings.rpc_timeout,
        "delay": settings.rpc_delay,
        "jitter": settings.rpc_jitter,
    }

    async def fetch_balance(token_addr: str) -> AssetData | None:
        if token_addr == eth_sentinel:
            balance = await _rpc_call(
                w3.eth.get_balance, checksum_address,
                block_identifier=block_number, **rpc_kwargs,
            )
        else:
            contract = w3.eth.contract(
                address=Web3.to_checksum_address(token_addr), abi=erc20_abi
            )
            balance = await _rpc_call(
                contract.functions.balanceOf(checksum_address).call,
                block_identifier=block_number, **rpc_kwargs,
            )

        # Subtract gas reserve from the configured gas token
        if gas_reserve_native > 0 and balance > 0 and token_addr == gas_token:
            balance = max(0, balance - gas_reserve_native)
            if balance == 0:
                logger.debug(
                    "Gas token %s at %s fully reserved for gas (reserve=%.4f)",
                    gas_token, address, settings.gas_reserve,
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
            logger.error("Failed to fetch balance for %s at %s: %s",
                         token_addr, address, result)
            raise result
        if result is not None:
            assets.append(result)

    return assets


async def _run_adapter_chain(
    settings: OracleSettings,
    subaccount_addr: str,
    adapter_names: list[str],
    subaccount_config: dict[str, Any],
    log,
) -> list[AssetData]:
    """Run adapters sequentially, passing results forward (adapter chaining).

    Same logic as pipeline/assets.py create_adapter_task + run_adapter_chain.

    Args:
        settings: Oracle settings
        subaccount_addr: Subaccount address
        adapter_names: List of adapter names (e.g., ["aave_v3.aave", "pendle"])
        subaccount_config: Subaccount config dict (for adapter_overrides)
        log: Logger

    Returns:
        List of assets from the adapter chain
    """
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
            settings, adapter_name, subaccount_config, adapter_defaults, log
        )
        new_assets = await adapter.fetch_assets(subaccount_addr, accumulated)
        accumulated = new_assets
        log.debug(
            "Subaccount %s → %s returned %d assets",
            subaccount_addr,
            adapter_name,
            len(new_assets) if new_assets else 0,
        )

    return accumulated or []


def _create_adapter(
    settings: OracleSettings,
    adapter_name: str,
    subaccount_config: dict[str, Any],
    adapter_defaults: dict[str, Any],
    log,
):
    """Create an adapter instance with proper config resolution.

    Mirrors pipeline/assets.py create_adapter_task logic.
    """
    base_name, instance_name = parse_adapter_name(adapter_name)
    adapter_class = get_adapter_class(adapter_name)

    # Resolve adapter overrides from subaccount config
    adapter_overrides: dict[str, Any] = {}
    overrides_config = subaccount_config.get("adapter_overrides", {})
    if isinstance(overrides_config, dict):
        candidate = overrides_config.get(adapter_name)
        if candidate is None:
            candidate = overrides_config.get(adapter_name.lower())
        if candidate is None:
            candidate = overrides_config.get(base_name)
        if isinstance(candidate, dict):
            adapter_overrides = candidate

    # Resolve defaults (multi-instance support for aave_v3)
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
        adapter = adapter_class(settings, **adapter_kwargs)
    else:
        adapter = adapter_class(settings)

    log.debug(
        "Subaccount → adapter: %s%s",
        adapter_name,
        f" (instance: {instance_name})" if instance_name else "",
    )
    return adapter


# ---------------------------------------------------------------------------
# Multi-chain upshift asset collection
# ---------------------------------------------------------------------------


async def collect_multi_chain_subaccount_assets(
    state: AppState,
    base_asset: str,
) -> tuple[AggregatedAssets, dict[str, list[AssetData]]]:
    """Collect assets from all configured upshift chains in parallel.

    For each chain, runs subaccount scanning + adapter chaining using that
    chain's own RPC, tracked tokens, gas config, and adapter settings.

    Args:
        state: Application state with settings
        base_asset: Base asset address (for reference)

    Returns:
        Tuple of (aggregated assets across all chains, per-subaccount asset map)
    """
    s = state.settings
    log = state.logger

    log.info("Multi-chain upshift: collecting from %d chain(s)", len(s.upshift_chains))

    tasks = [
        _collect_chain_subaccounts(state, chain_cfg, base_asset)
        for chain_cfg in s.upshift_chains
    ]

    results = await asyncio.gather(*tasks, return_exceptions=True)

    combined_map: dict[str, list[AssetData]] = {}
    all_assets: list[list[AssetData]] = []

    for i, result in enumerate(results):
        chain_cfg = s.upshift_chains[i]
        if isinstance(result, BaseException):
            if chain_cfg.required:
                log.error("Required chain %s failed: %s", chain_cfg.name, result)
                raise result
            log.error("Chain %s failed (non-required, skipping): %s",
                       chain_cfg.name, result)
            continue
        _chain_agg, chain_map = result
        combined_map.update(chain_map)
        all_assets.extend(list(chain_map.values()))

    aggregated = await compute_total_aggregated_assets(all_assets)
    log.info("Multi-chain aggregated %d unique assets across %d chain(s)",
             len(aggregated.assets), len(s.upshift_chains))
    return aggregated, combined_map


async def _collect_chain_subaccounts(
    state: AppState,
    chain_cfg: UpshiftChainConfig,
    base_asset: str,
) -> tuple[AggregatedAssets, dict[str, list[AssetData]]]:
    """Collect assets from a single chain's subaccounts.

    Args:
        state: Application state
        chain_cfg: Per-chain configuration
        base_asset: Base asset address

    Returns:
        Tuple of (aggregated assets for this chain, per-subaccount asset map)
    """
    log = state.logger
    log.info("Chain %s: collecting from %d subaccounts",
             chain_cfg.name, len(chain_cfg.subaccount_adapters))

    w3 = Web3(Web3.HTTPProvider(chain_cfg.rpc))
    block_number = chain_cfg.block_number
    if block_number is None:
        block_number = w3.eth.block_number
        log.debug("Chain %s: resolved block number %d", chain_cfg.name, block_number)

    chain_settings = _build_chain_settings(state.settings, chain_cfg, block_number)

    tracked_token_addresses = [
        Web3.to_checksum_address(addr) for addr in chain_cfg.tracked_tokens.values()
    ]

    subaccount_asset_map: dict[str, list[AssetData]] = {}
    all_assets: list[list[AssetData]] = []

    for cfg in chain_cfg.subaccount_adapters:
        subaccount_addr = cfg["subaccount_address"]
        subaccount_assets: list[AssetData] = []

        # 1. Scan idle balances for tracked tokens
        if tracked_token_addresses:
            idle_assets = await _scan_token_balances(
                w3, subaccount_addr, tracked_token_addresses, block_number, chain_settings
            )
            subaccount_assets.extend(idle_assets)
            log.debug(
                "Chain %s / Subaccount %s: %d idle balances",
                chain_cfg.name, subaccount_addr, len(idle_assets),
            )

        # 2. Run adapter chain
        adapter_names = cfg.get("additional_adapters", [])
        if adapter_names:
            chain_assets = await _run_adapter_chain(
                chain_settings, subaccount_addr, adapter_names, cfg, log
            )
            subaccount_assets.extend(chain_assets)
            log.debug(
                "Chain %s / Subaccount %s: %d adapter assets",
                chain_cfg.name, subaccount_addr, len(chain_assets),
            )

        key = f"{chain_cfg.name}:{subaccount_addr.lower()}"
        subaccount_asset_map[key] = subaccount_assets
        all_assets.append(subaccount_assets)
        log.info(
            "Chain %s / Subaccount %s: %d total assets",
            chain_cfg.name, subaccount_addr, len(subaccount_assets),
        )

    aggregated = await compute_total_aggregated_assets(all_assets)
    log.info("Chain %s: %d unique assets across %d subaccounts",
             chain_cfg.name, len(aggregated.assets), len(chain_cfg.subaccount_adapters))
    return aggregated, subaccount_asset_map


def _build_chain_settings(
    base: OracleSettings,
    chain: UpshiftChainConfig,
    block_number: int,
) -> OracleSettings:
    """Build a settings overlay for a specific chain.

    Creates a copy of the base settings with chain-specific values for
    RPC, gas config, tracked tokens, subaccounts, network, and adapters.
    """
    from ..settings import Network

    try:
        network = Network(chain.network)
    except ValueError:
        network = base.network

    return base.model_copy(update={
        "vault_rpc": chain.rpc,
        "block_number": block_number,
        "network": network,
        "gas_reserve": chain.gas_reserve,
        "gas_token_address": chain.gas_token_address,
        "gas_token_decimals": chain.gas_token_decimals,
        "tracked_tokens": chain.tracked_tokens,
        "subaccount_adapters": chain.subaccount_adapters,
        "adapters": chain.adapters,
    })
