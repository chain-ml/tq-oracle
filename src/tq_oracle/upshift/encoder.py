"""Calldata encoder for Upshift updateTotalAssets."""

from __future__ import annotations

from eth_abi import encode


# updateTotalAssets(uint256) function selector
UPDATE_TOTAL_ASSETS_SELECTOR = bytes.fromhex("1f4f519c")


def encode_update_total_assets(total_assets: int) -> bytes:
    """Encode updateTotalAssets(uint256) calldata.

    Args:
        total_assets: Total assets value in base asset native units.

    Returns:
        ABI-encoded calldata bytes (selector + encoded uint256).
    """
    encoded_args = encode(["uint256"], [total_assets])
    return UPDATE_TOTAL_ASSETS_SELECTOR + encoded_args
