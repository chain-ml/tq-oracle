"""High-level pipeline orchestration."""

from __future__ import annotations

import asyncio

from web3 import Web3

from ..abi import load_fee_manager_abi, load_vault_abi
from ..report.supported_assets import fetch_supported_assets
from ..state import AppState
from .assets import collect_assets
from .context import PipelineContext
from .preflight import run_preflight
from .pricing import price_assets
from .report import build_report, publish_report


def _canonical_address(address: str) -> str:
    try:
        return Web3.to_checksum_address(address)
    except ValueError:
        return address.lower()


ZERO_ADDRESSES = {
    "0x0000000000000000000000000000000000000000",
    "0x0",
}


def _is_zero_address(address: str) -> bool:
    return address.lower() in ZERO_ADDRESSES


async def _discover_base_asset(state: AppState) -> str:
    """Fetch the vault's base asset by traversing the FeeManager contract."""

    settings = state.settings
    log = state.logger

    w3 = Web3(Web3.HTTPProvider(settings.vault_rpc_required))
    block_identifier = settings.block_number_required
    vault_address_raw = settings.vault_address_required
    vault_checksum = Web3.to_checksum_address(vault_address_raw)
    vault_address = _canonical_address(vault_address_raw)

    vault_contract = w3.eth.contract(address=vault_checksum, abi=load_vault_abi())
    log.debug("Fetching FeeManager address from vault %s", vault_address)

    fee_manager_address = await asyncio.to_thread(
        vault_contract.functions.feeManager().call,
        block_identifier=block_identifier,
    )
    fee_manager_checksum = Web3.to_checksum_address(fee_manager_address)
    fee_manager_address = _canonical_address(fee_manager_address)

    fee_manager_contract = w3.eth.contract(
        address=fee_manager_checksum, abi=load_fee_manager_abi()
    )
    log.debug("Fetching base asset from FeeManager %s", fee_manager_address)

    base_asset = await asyncio.to_thread(
        fee_manager_contract.functions.baseAsset(vault_checksum).call,
        block_identifier=block_identifier,
    )
    base_asset = _canonical_address(base_asset)

    if _is_zero_address(base_asset):
        raise ValueError(
            "FeeManager returned zero base asset address; ensure contract is configured"
        )

    log.info(
        "Discovered base asset %s via FeeManager %s",
        base_asset,
        fee_manager_address,
    )

    return base_asset


async def run_report(state: AppState, vault_address: str) -> None:
    """Execute the complete oracle pipeline.

    This is a thin orchestrator that sequences the pipeline steps:
    1. Preflight checks
    2. Asset collection
    3. Pricing and validation
    4. Report generation
    5. Submission (if not dry-run)

    Args:
        state: Application state containing settings and logger
        vault_address: The vault address to report on
    """
    s = state.settings
    log = state.logger

    log.info(
        "Starting report",
        extra={
            "vault": vault_address,
            "dry_run": s.dry_run,
        },
    )

    ctx = PipelineContext(state=state, vault_address=vault_address)
    ctx.base_asset = await _discover_base_asset(state)

    await run_preflight(ctx)
    await collect_assets(ctx)
    await price_assets(ctx)

    # Fetch supported assets from Oracle contract for calldata filtering
    log.info("Fetching supported assets from Oracle...")
    ctx.supported_assets = await fetch_supported_assets(s)

    await build_report(ctx)
    await publish_report(ctx)

    log.info("Report completed", extra={"vault": vault_address})
