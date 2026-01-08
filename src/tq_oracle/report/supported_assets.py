"""Fetch supported assets from Oracle contract."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from web3 import Web3

from ..abi import get_oracle_address_from_vault, load_oracle_abi

if TYPE_CHECKING:
    from ..settings import OracleSettings

logger = logging.getLogger(__name__)


async def fetch_supported_assets(config: OracleSettings) -> set[str]:
    """Fetch supported assets from the Oracle contract.

    These are the assets that the Oracle accepts price reports for.
    Only these assets should be included in the submitReports() calldata.

    Args:
        config: Oracle settings with vault and RPC configuration

    Returns:
        Set of supported asset addresses (lowercased)
    """
    w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
    oracle_address = get_oracle_address_from_vault(config)
    oracle_abi = load_oracle_abi()

    logger.debug("Fetching supported assets from Oracle: %s", oracle_address)

    contract = w3.eth.contract(
        address=w3.to_checksum_address(oracle_address),
        abi=oracle_abi,
    )

    block_identifier = config.block_number or "latest"

    # Get count of supported assets
    count = await asyncio.to_thread(
        contract.functions.supportedAssets().call,
        block_identifier=block_identifier,
    )

    logger.debug("Oracle reports %d supported assets", count)

    # Fetch all asset addresses
    async def fetch_asset_at(index: int) -> str:
        asset: str = await asyncio.to_thread(
            contract.functions.supportedAssetAt(index).call,
            block_identifier=block_identifier,
        )
        return asset.lower()

    assets = await asyncio.gather(*[fetch_asset_at(i) for i in range(count)])

    supported = set(assets)
    logger.info(
        "Fetched %d supported assets from Oracle: %s",
        len(supported),
        ", ".join(sorted(supported)[:5]) + ("..." if len(supported) > 5 else ""),
    )

    return supported
