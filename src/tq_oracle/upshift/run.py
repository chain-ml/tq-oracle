"""Upshift pipeline orchestrator — simplified TVL report without vault contract."""

from __future__ import annotations

import asyncio
import json

from web3 import Web3

from ..abi import load_erc20_abi
from ..constants import ETH_ASSET
from ..processors.total_assets import calculate_total_assets
from ..report.subvault_breakdown import log_subvault_breakdown
from ..state import AppState
from .assets import collect_multi_chain_subaccount_assets, collect_subaccount_assets
from .encoder import encode_update_total_assets
from .pricing import price_all_assets


async def _query_vault_base_balance(
    vault_address: str,
    base_asset: str,
    rpc: str,
    block_number: int,
    base_asset_decimals: int,
) -> int:
    """Query the vault contract's own balance of the base asset.

    Args:
        vault_address: Vault contract address
        base_asset: Base asset token address
        rpc: RPC endpoint
        block_number: Block number for state snapshot
        base_asset_decimals: Base asset decimals (for logging)

    Returns:
        Vault's base asset balance in native units
    """
    w3 = Web3(Web3.HTTPProvider(rpc))
    vault = Web3.to_checksum_address(vault_address)
    base = Web3.to_checksum_address(base_asset)
    eth_sentinel = Web3.to_checksum_address(ETH_ASSET)

    if base == eth_sentinel:
        balance = await asyncio.to_thread(
            w3.eth.get_balance, vault, block_identifier=block_number
        )
    else:
        contract = w3.eth.contract(address=base, abi=load_erc20_abi())
        balance = await asyncio.to_thread(
            contract.functions.balanceOf(vault).call,
            block_identifier=block_number,
        )
    return int(balance)


async def run_upshift(state: AppState) -> None:
    """Execute the Upshift TVL pipeline.

    Simplified pipeline:
    1. Collect assets from explicit subaccounts (no vault discovery)
    2. Price all assets using standard price adapters
    3. Calculate external TVL in base asset (subaccount holdings)
    4. Query vault's own base asset balance
    5. Log per-subaccount breakdown
    6. Encode and output updateTotalAssets calldata (external assets only)

    Args:
        state: Application state containing settings and logger
    """
    s = state.settings
    log = state.logger

    base_asset = s.base_asset_address
    if not base_asset:
        raise ValueError("base_asset_address must be configured for upshift mode")

    multi_chain = bool(s.upshift_chains)

    log.info("Starting Upshift TVL report%s", " (multi-chain)" if multi_chain else "")
    log.info("  Vault (target): %s", s.vault_address)
    log.info("  Base asset: %s (decimals: %d)", base_asset, s.base_asset_decimals)
    if multi_chain:
        log.info("  Chains: %d", len(s.upshift_chains))
        for chain_cfg in s.upshift_chains:
            log.info("    - %s: %d subaccounts", chain_cfg.name,
                     len(chain_cfg.subaccount_adapters))
    else:
        log.info("  Subaccounts: %d", len(s.subaccount_adapters))
        log.info("  Tracked tokens: %d", len(s.tracked_tokens))

    # 1. Collect assets from all subaccounts
    log.info("Collecting assets...")
    if multi_chain:
        aggregated, subaccount_asset_map = await collect_multi_chain_subaccount_assets(
            state, base_asset
        )
    else:
        aggregated, subaccount_asset_map = await collect_subaccount_assets(state, base_asset)

    # 2. Price all assets
    log.info("Pricing assets...")
    price_data = await price_all_assets(state, aggregated, base_asset)

    # 3. Calculate external TVL in base asset native units (subaccount holdings)
    external_assets = calculate_total_assets(aggregated, price_data, s.base_asset_decimals)

    # 4. Query vault's own base asset balance
    vault_rpc = s.vault_rpc_required
    vault_balance = await _query_vault_base_balance(
        s.vault_address_required, base_asset, vault_rpc, s.block_number_required,
        s.base_asset_decimals,
    )
    total_assets = vault_balance + external_assets

    # 5. Log per-subaccount breakdown
    log.info("Per-subaccount breakdown:")
    for subaccount_addr, assets in subaccount_asset_map.items():
        await log_subvault_breakdown(
            subaccount_addr,
            assets,
            price_data,
            s,
            base_asset_decimals=s.base_asset_decimals,
        )

    # 6. Encode and output
    decimals = s.base_asset_decimals
    vault_display = vault_balance / 10**decimals
    external_display = external_assets / 10**decimals
    total_display = total_assets / 10**decimals

    log.info("Vault base asset balance: %d (%.6f)", vault_balance, vault_display)
    log.info("External assets (subaccounts): %d (%.6f)", external_assets, external_display)
    log.info("Total assets: %d (%.6f)", total_assets, total_display)

    calldata = encode_update_total_assets(external_assets)
    log.info("Target contract: %s", s.vault_address)
    log.info("Calldata (external assets): 0x%s", calldata.hex())

    output = {
        "vault_address": s.vault_address,
        "vault_balance": vault_balance,
        "vault_balance_display": vault_display,
        "external_assets": external_assets,
        "external_assets_display": external_display,
        "total_assets": total_assets,
        "total_assets_display": total_display,
        "base_asset": base_asset,
        "base_asset_decimals": s.base_asset_decimals,
        "target": s.vault_address,
        "calldata": "0x" + calldata.hex(),
    }
    print(json.dumps(output, indent=2))
