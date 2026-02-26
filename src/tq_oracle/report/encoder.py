"""Transaction encoder for Safe transactions."""

from __future__ import annotations

import logging

from eth_typing.evm import ChecksumAddress
from web3 import Web3

from ..abi import load_oracle_abi
from .generator import OracleReport

logger = logging.getLogger(__name__)


def encode_submit_reports(
    oracle_address: str,
    report: OracleReport,
    supported_assets: set[str] | None = None,
) -> tuple[str, bytes]:
    """Encode submitReports() transaction data.

    Args:
        oracle_address: IOracle contract address
        report: Oracle report with final_prices dict
        supported_assets: Set of supported asset addresses (lowercased).
                         If provided, only these assets will be included in calldata.
                         If None, all assets from report will be included.

    Returns:
        Tuple of (to_address, encoded_calldata)

    The submitReports function expects:
        struct Report[] reports where Report = (address asset, uint224 priceD18)
    """
    w3 = Web3()
    abi = load_oracle_abi()

    checksum_address = w3.to_checksum_address(oracle_address)
    contract = w3.eth.contract(address=checksum_address, abi=abi)
    base_asset = w3.to_checksum_address(report.base_asset)

    # Filter prices to only include supported assets (if provided)
    if supported_assets is not None:
        filtered_prices = {
            asset_addr: price_d18
            for asset_addr, price_d18 in report.final_prices.items()
            if asset_addr.lower() in supported_assets
        }
        excluded_count = len(report.final_prices) - len(filtered_prices)
        if excluded_count > 0:
            logger.info(
                "Filtered out %d asset(s) not in Oracle's supported assets list",
                excluded_count,
            )
    else:
        filtered_prices = report.final_prices

    # Apply decimal scaling: price * 10^(36 - 2*decimals)
    # For 18-decimal tokens: no change (10^0 = 1)
    # For 6-decimal tokens: multiply by 10^24
    scaled_prices = _apply_decimal_scaling(
        filtered_prices, report.asset_decimals, default_decimals=report.base_asset_decimals
    )

    reports_array: list[tuple[ChecksumAddress, int]] = [
        (w3.to_checksum_address(asset_addr), price_d18)
        for asset_addr, price_d18 in sorted(
            scaled_prices.items(),
            key=lambda x: 0 if w3.to_checksum_address(x[0]) == base_asset else 1,
        )
    ]

    logger.info("Encoding submitReports() with %d report(s):", len(reports_array))
    for asset_addr, price_d18 in reports_array:
        price_decimal = price_d18 / 10**18
        logger.info(
            "  - Asset: %s, Price: %d D18 (%.6f)", asset_addr, price_d18, price_decimal
        )

    calldata_hex = contract.encode_abi(
        abi_element_identifier="submitReports",
        args=[reports_array],
    )

    calldata = bytes.fromhex(calldata_hex.removeprefix("0x"))

    return (oracle_address, calldata)


def _apply_decimal_scaling(
    prices: dict[str, int],
    asset_decimals: dict[str, int],
    default_decimals: int = 18,
) -> dict[str, int]:
    """Apply decimal scaling to prices for submitReports.

    The oracle contract expects prices scaled by 10^(36 - 2*decimals):
    - 18-decimal tokens: 10^(36-36) = 10^0 = 1 (unchanged)
    - 6-decimal tokens: 10^(36-12) = 10^24 (multiply by 10^24)

    Args:
        prices: Asset address -> price (D18) mapping
        asset_decimals: Asset address -> token decimals mapping
        default_decimals: Fallback decimals if asset not in asset_decimals

    Returns:
        Prices with decimal scaling applied
    """
    scaled: dict[str, int] = {}

    for asset_addr, price_d18 in prices.items():
        decimals = asset_decimals.get(
            asset_addr.lower(), asset_decimals.get(asset_addr, default_decimals)
        )
        scale_exponent = 36 - 2 * decimals

        if scale_exponent == 0:
            scaled[asset_addr] = price_d18
        else:
            scale_factor = 10**scale_exponent
            scaled_price = price_d18 * scale_factor
            logger.info(
                "Decimal scaling %s: decimals=%d, scale=10^%d, price %d -> %d",
                asset_addr,
                decimals,
                scale_exponent,
                price_d18,
                scaled_price,
            )
            scaled[asset_addr] = scaled_price

    return scaled
