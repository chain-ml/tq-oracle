"""Asset collection from various adapters."""

import asyncio
from typing import Any

from ..abi import fetch_subvault_addresses
from ..adapters.asset_adapters import get_adapter_class
from ..adapters.asset_adapters.base import AssetData
from ..adapters.asset_adapters.idle_balances import IdleBalancesAdapter
from ..adapters.asset_adapters.stakewise import StakeWiseAdapter
from ..adapters.asset_adapters.streth import StrETHAdapter
from ..processors import compute_total_aggregated_assets
from .context import PipelineContext


def _sanitize_adapter_kwargs(values: dict[str, Any]) -> dict[str, Any]:
    """Drop None or empty collection values from adapter kwargs."""

    return {
        key: value
        for key, value in values.items()
        if value is not None and (not isinstance(value, (list, dict)) or value)
    }


def _process_adapter_results(
    tasks_info: list[Any],
    results: tuple[BaseException | list[AssetData], ...],
    asset_data: list[list[AssetData]],
    log: Any,
) -> None:
    """Process asyncio.gather results from adapter tasks.

    Args:
        tasks_info: List of task information tuples (varying structure)
        results: Results from asyncio.gather (may contain exceptions)
        asset_data: List to append successful asset results to
        log: Logger instance

    Raises:
        ValueError: If any adapter failed
    """
    failures: list[tuple[str, BaseException]] = []

    for task_info, result in zip(tasks_info, results):
        if isinstance(result, BaseException):
            e = result
            if len(task_info) == 2:  # (name, _)
                name = task_info[0]
                log.error("Adapter '%s' failed: %s", name, e)
                failures.append((name, e))
            elif len(task_info) == 3:  # (subvault_addr, adapter, name)
                subvault_addr, _, name = task_info
                identifier = f"{name} (subvault {subvault_addr})"
                log.error(
                    "Adapter '%s' failed for subvault %s: %s",
                    name,
                    subvault_addr,
                    e,
                )
                failures.append((identifier, e))
        elif isinstance(result, list):
            assets = result
            if len(task_info) == 2:  # (name, _)
                name = task_info[0]
                log.debug("Adapter '%s' returned %d assets", name, len(assets))
            elif len(task_info) == 3:  # (subvault_addr, adapter, name)
                subvault_addr, _, name = task_info
                log.debug(
                    "Adapter '%s' for subvault %s returned %d assets",
                    name,
                    subvault_addr,
                    len(assets),
                )
            asset_data.append(assets)

    if failures:
        failure_list = ", ".join(name for name, _ in failures)
        raise ValueError(
            f"Failed to collect assets from {len(failures)} adapter(s): {failure_list}"
        )


async def collect_assets(ctx: PipelineContext) -> None:
    """Collect assets from all configured adapters.

    Args:
        ctx: Pipeline context containing state

    Sets the aggregated assets in the context.
    """
    s = ctx.state.settings
    log = ctx.state.logger

    log.info("Discovering subvaults from vault contract...")
    subvault_addresses = await fetch_subvault_addresses(s)
    log.info("Found %d subvaults", len(subvault_addresses))

    subvault_config_map = {
        cfg["subvault_address"].lower(): cfg for cfg in s.subvault_adapters
    }

    dangerous_configs = [
        cfg
        for cfg in s.subvault_adapters
        if cfg.get("skip_subvault_existence_check", False)
    ]
    if dangerous_configs and not s.allow_dangerous:
        addresses = [cfg["subvault_address"] for cfg in dangerous_configs]
        raise ValueError(
            f"Configuration uses 'skip_subvault_existence_check' for subvault(s): "
            f"{', '.join(addresses)}. This is a dangerous operation that bypasses "
            f"subvault existence validation. You must explicitly allow this by "
            f"passing the --allow-dangerous CLI flag."
        )

    # Validate subvault_adapters config references existing subvaults
    if s.subvault_adapters:
        normalized_subvault_addrs = {addr.lower() for addr in subvault_addresses}
        invalid_subvaults = [
            cfg["subvault_address"]
            for cfg in s.subvault_adapters
            if not cfg.get("skip_subvault_existence_check", False)
            and cfg["subvault_address"].lower() not in normalized_subvault_addrs
        ]

        if invalid_subvaults:
            raise ValueError(
                f"Config specifies adapters for non-existent subvaults: "
                f"{', '.join(invalid_subvaults)}"
            )

    adapter_defaults = {
        name.lower(): value
        for name, value in s.adapters.model_dump(
            exclude_none=True, exclude_defaults=True
        ).items()
        if isinstance(value, dict)
    }

    log.info("Setting up asset adapters...")
    asset_fetch_tasks = []

    default_subvault_config = {
        "additional_adapters": [],
        "skip_idle_balances": False,
        "adapter_overrides": {},
    }

    def get_subvault_config(subvault_address: str) -> dict[str, Any]:
        """Get adapter configuration for a specific subvault."""

        return subvault_config_map.get(subvault_address.lower()) or {
            **default_subvault_config,
            "subvault_address": subvault_address,
        }

    # 1. Add default idle_balances (runs against ALL subvaults unless globally skipped)
    should_run_default_idle_balances = any(
        not get_subvault_config(addr).get("skip_idle_balances", False)
        for addr in subvault_addresses
    )

    if should_run_default_idle_balances:
        idle_vault_adapter = IdleBalancesAdapter(s)
        asset_fetch_tasks.append(
            ("idle_balances_vault_chain", idle_vault_adapter.fetch_all_assets())
        )
        log.debug("Added default idle_balances adapter (all subvaults)")

    # 2. Add default stakewise adapter (runs against all subvaults + extra_addresses)
    stakewise_config = s.adapters.stakewise
    if stakewise_config.stakewise_vault_addresses:
        stakewise_adapter = StakeWiseAdapter(s)
        asset_fetch_tasks.append(
            ("stakewise_all_subvaults", stakewise_adapter.fetch_all_assets())
        )
        log.debug(
            "Added default stakewise adapter (all subvaults + %d extra addresses)",
            len(stakewise_config.extra_addresses),
        )

    # 3. Add additional adapters per subvault
    should_run_streth = any(
        not get_subvault_config(addr).get("skip_streth", False)
        for addr in subvault_addresses
    )

    if should_run_streth:
        streth_adapter = StrETHAdapter(s)
        asset_fetch_tasks.append(
            ("streth_vault_chain", streth_adapter.fetch_all_assets())
        )
        log.debug("Added default vault_chain strETH adapter")

    # 4. Add additional adapters per subvault
    def create_adapter_task(
        subvault_addr: str, adapter_name: str
    ) -> tuple[str, Any, str] | None:
        """Create an adapter instance for the given subvault and adapter name."""
        adapter_class = get_adapter_class(adapter_name)

        adapter_overrides: dict[str, Any] = {}
        overrides_config = get_subvault_config(subvault_addr).get(
            "adapter_overrides", {}
        )
        if isinstance(overrides_config, dict):
            candidate = overrides_config.get(adapter_name)
            if candidate is None:
                candidate = overrides_config.get(adapter_name.lower())
            if isinstance(candidate, dict):
                adapter_overrides = candidate
            elif candidate is not None:
                log.warning(
                    "adapter_overrides for adapter %s on subvault %s must be a mapping; got %r",
                    adapter_name,
                    subvault_addr,
                    candidate,
                )
        else:
            log.warning(
                "adapter_overrides for subvault %s must be a mapping; got %r",
                subvault_addr,
                overrides_config,
            )

        defaults = adapter_defaults.get(adapter_name.lower(), {})
        adapter_kwargs = _sanitize_adapter_kwargs({**defaults, **adapter_overrides})

        if adapter_kwargs:
            adapter = adapter_class(s, **adapter_kwargs)
        else:
            adapter = adapter_class(s)

        log.debug(
            "Subvault %s → additional adapter: %s",
            subvault_addr,
            adapter_name,
        )
        return (subvault_addr, adapter, adapter_name)

    extra_subvaults = {
        cfg["subvault_address"]
        for cfg in s.subvault_adapters
        if cfg.get("skip_subvault_existence_check", False)
    }
    subvaults_to_process = set(subvault_addresses) | extra_subvaults

    for extra in extra_subvaults - set(subvault_addresses):
        log.debug(
            "Including non-vault address for adapters: %s (skip_subvault_existence_check=true)",
            extra,
        )

    adapter_tasks: list[tuple[str, Any, str]] = [
        task
        for subvault_addr in subvaults_to_process
        for adapter_name in get_subvault_config(subvault_addr).get(
            "additional_adapters", []
        )
        if (task := create_adapter_task(subvault_addr, adapter_name)) is not None
    ]

    log.info(
        "Fetching assets: %d adapter tasks + %d additional per-subvault tasks...",
        len(asset_fetch_tasks),
        len(adapter_tasks),
    )

    default_results = await asyncio.gather(
        *[task for _, task in asset_fetch_tasks], return_exceptions=True
    )

    # Group adapter tasks by subvault for sequential execution with chaining
    subvault_adapter_chains: dict[str, list[tuple[Any, str]]] = {}
    for subvault_addr, adapter, adapter_name in adapter_tasks:
        key = subvault_addr.lower()
        if key not in subvault_adapter_chains:
            subvault_adapter_chains[key] = []
        subvault_adapter_chains[key].append((adapter, adapter_name))

    async def run_adapter_chain(subvault_addr: str, adapters: list[tuple[Any, str]]):
        """Run adapters sequentially for a subvault, passing results forward."""
        accumulated_assets: list[AssetData] | None = None

        for adapter, adapter_name in adapters:
            try:
                # Pass previous adapter results to this adapter
                new_assets = await adapter.fetch_assets(subvault_addr, accumulated_assets)
                accumulated_assets = new_assets
                log.debug(
                    "Adapter chain %s for %s: %s returned %d assets",
                    adapter_name,
                    subvault_addr,
                    adapter_name,
                    len(new_assets) if new_assets else 0,
                )
            except Exception as e:
                # Return exception for error handling
                log.error(
                    "Adapter chain %s for %s failed: %s",
                    adapter_name,
                    subvault_addr,
                    e,
                )
                return e

        return accumulated_assets or []

    # Run adapter chains (sequential within subvault, parallel across subvaults)
    per_subvault_results = await asyncio.gather(
        *[
            run_adapter_chain(subvault_addr, adapters)
            for subvault_addr, adapters in subvault_adapter_chains.items()
        ],
        return_exceptions=True,
    )

    asset_data: list[list[AssetData]] = []
    _process_adapter_results(asset_fetch_tasks, default_results, asset_data, log)

    # Process chained adapter results
    subvault_chain_task_info = [
        (subvault_addr, None, f"adapter_chain_{len(adapters)}_adapters")
        for subvault_addr, adapters in subvault_adapter_chains.items()
    ]
    _process_adapter_results(subvault_chain_task_info, per_subvault_results, asset_data, log)

    # Track which assets came from which subvault
    # Fetch per-subvault assets directly for breakdown
    subvault_asset_map: dict[str, list[AssetData]] = {}

    log.info("Fetching per-subvault assets for breakdown...")
    log.debug(f"subvault_adapter_chains keys: {list(subvault_adapter_chains.keys())}")
    for subvault_addr in subvault_addresses:
        subvault_assets: list[AssetData] = []
        log.debug(f"Processing subvault: {subvault_addr} (lowercased: {subvault_addr.lower()})")

        # Idle balances
        if should_run_default_idle_balances and not get_subvault_config(
            subvault_addr
        ).get("skip_idle_balances", False):
            try:
                idle_assets = await idle_vault_adapter.fetch_assets(subvault_addr)
                if idle_assets:
                    log.info(
                        f"  idle_balances returned {len(idle_assets)} assets for {subvault_addr}"
                    )
                    for asset in idle_assets:
                        log.debug(f"    - {asset.asset_address}: {asset.amount}")
                else:
                    log.debug(f"  idle_balances returned 0 assets for {subvault_addr}")
                subvault_assets.extend(idle_assets)
            except Exception as e:
                log.debug(f"  idle_balances failed for {subvault_addr}: {e}")

        # StakeWise
        if stakewise_config.stakewise_vault_addresses:
            try:
                stakewise_assets = await stakewise_adapter.fetch_assets(subvault_addr)
                if stakewise_assets:
                    log.info(
                        f"  stakewise returned {len(stakewise_assets)} assets for {subvault_addr}"
                    )
                    for asset in stakewise_assets:
                        log.debug(f"    - {asset.asset_address}: {asset.amount}")
                else:
                    log.debug(f"  stakewise returned 0 assets for {subvault_addr}")
                subvault_assets.extend(stakewise_assets)
            except Exception as e:
                log.debug(f"  stakewise failed for {subvault_addr}: {e}")

        # strETH
        if should_run_streth and not get_subvault_config(subvault_addr).get(
            "skip_streth", False
        ):
            try:
                streth_assets = await streth_adapter.fetch_assets(subvault_addr)
                if streth_assets:
                    log.info(
                        f"  streth returned {len(streth_assets)} assets for {subvault_addr}"
                    )
                    for asset in streth_assets:
                        log.debug(f"    - {asset.asset_address}: {asset.amount}")
                else:
                    log.debug(f"  streth returned 0 assets for {subvault_addr}")
                subvault_assets.extend(streth_assets)
            except Exception as e:
                log.debug(f"  streth failed for {subvault_addr}: {e}")

        # Add chained adapter results to this subvault
        subvault_key = subvault_addr.lower()
        if subvault_key in subvault_adapter_chains:
            # Find the result for this subvault's adapter chain
            chain_keys = list(subvault_adapter_chains.keys())
            log.debug(f"  Looking for {subvault_key} in chain keys: {chain_keys}")
            try:
                chain_result_idx = chain_keys.index(subvault_key)
                log.debug(f"  Found chain at index {chain_result_idx}, total results: {len(per_subvault_results)}")
                if chain_result_idx < len(per_subvault_results):
                    chain_result = per_subvault_results[chain_result_idx]
                    if isinstance(chain_result, list) and not isinstance(chain_result, BaseException):
                        if chain_result:
                            log.info(f"  adapter_chain returned {len(chain_result)} assets for {subvault_addr}")
                            for asset in chain_result:
                                log.debug(f"    - {asset.asset_address}: {asset.amount}")
                        else:
                            log.debug(f"  adapter_chain returned empty list for {subvault_addr}")
                        subvault_assets.extend(chain_result)
                    else:
                        log.warning(f"  adapter_chain result was not a list for {subvault_addr}: {type(chain_result)}")
                else:
                    log.warning(f"  Chain index {chain_result_idx} out of range for results length {len(per_subvault_results)}")
            except ValueError as e:
                log.error(f"  Failed to find {subvault_key} in chain keys: {e}")
        else:
            log.debug(f"  No adapter chain configured for {subvault_addr}")

        subvault_asset_map[subvault_addr.lower()] = subvault_assets

    log.info("Computing aggregated assets...")
    aggregated = await compute_total_aggregated_assets(asset_data)
    log.debug("Total aggregated assets: %d", len(aggregated.assets))

    # Flatten asset_data for per-subvault breakdown later
    raw_assets = [asset for assets_list in asset_data for asset in assets_list]

    ctx.aggregated = aggregated
    ctx.raw_assets = raw_assets

    # Store subvault mapping in context for later use
    ctx.subvault_asset_map = subvault_asset_map
