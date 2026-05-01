#!/usr/bin/env python3
"""Encode manual prices for WETH, ETH, and wstETH.

This script encodes prices in the same format as the oracle report encoder:
- ETH/WETH: 0.9e18 (base price)
- wstETH: 1.2241 * ETH price = 1.10169e18
"""

from eth_typing.evm import ChecksumAddress
from web3 import Web3

from src.tq_oracle.abi import load_oracle_abi

# Mainnet addresses
ETH_ADDRESS = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"  # Special ETH address
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH mainnet
WSTETH_ADDRESS = "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"  # wstETH mainnet

# Oracle contract address (replace with your actual oracle address)
ORACLE_ADDRESS = "0xYourOracleAddressHere"  # TODO: Replace with actual address


def encode_prices(
    oracle_address: str,
    eth_price_eth: float = 0.9,  # ETH price in ETH (typically 1.0, but you specified 0.9)
    wsteth_ratio: float = 1.2241,  # wstETH ratio to ETH
) -> tuple[str, bytes]:
    """Encode submitReports() transaction data for manual prices.

    Args:
        oracle_address: IOracle contract address
        eth_price_eth: ETH price in ETH units (you specified 0.9)
        wsteth_ratio: wstETH ratio to ETH (you specified 1.2241)

    Returns:
        Tuple of (to_address, encoded_calldata)
    """
    w3 = Web3()
    abi = load_oracle_abi()

    checksum_oracle = w3.to_checksum_address(oracle_address)
    contract = w3.eth.contract(address=checksum_oracle, abi=abi)

    # Calculate prices in D18 format (18 decimals)
    eth_price_d18 = int(eth_price_eth * 10**18)  # 0.9e18 = 900000000000000000
    weth_price_d18 = eth_price_d18  # WETH = ETH
    wsteth_price_d18 = int(wsteth_ratio * eth_price_eth * 10**18)  # 1.2241 * 0.9e18

    # Create reports array: [(address, priceD18), ...]
    # Sorted with base asset (ETH) first
    reports_array: list[tuple[ChecksumAddress, int]] = [
        (w3.to_checksum_address(ETH_ADDRESS), eth_price_d18),
        (w3.to_checksum_address(WETH_ADDRESS), weth_price_d18),
        (w3.to_checksum_address(WSTETH_ADDRESS), wsteth_price_d18),
    ]

    print("=" * 80)
    print("ENCODING MANUAL PRICES FOR submitReports()")
    print("=" * 80)
    print(f"\nOracle Address: {checksum_oracle}")
    print(f"\nPrices being encoded ({len(reports_array)} assets):\n")

    for asset_addr, price_d18 in reports_array:
        price_decimal = price_d18 / 10**18
        print(f"  • {asset_addr}")
        print(f"    Price D18: {price_d18}")
        print(f"    Price Decimal: {price_decimal:.18f}")
        print()

    # Encode the transaction
    calldata_hex = contract.encode_abi(
        abi_element_identifier="submitReports",
        args=[reports_array],
    )

    calldata = bytes.fromhex(calldata_hex.removeprefix("0x"))

    print("=" * 80)
    print("ENCODED CALLDATA")
    print("=" * 80)
    print(f"\nTo Address: {oracle_address}")
    print(f"\nCalldata (hex):\n{calldata_hex}")
    print(f"\nCalldata length: {len(calldata)} bytes")
    print("=" * 80)

    return (oracle_address, calldata)


if __name__ == "__main__":
    # You can modify these values as needed
    ETH_PRICE = 0.9  # 0.9 ETH (as you specified)
    WSTETH_RATIO = 1.2241  # wstETH ratio (as you specified)

    print("\n🔧 Configuration:")
    print(f"   ETH/WETH Price: {ETH_PRICE} ETH")
    print(f"   wstETH Ratio: {WSTETH_RATIO}x ETH")
    print(f"   wstETH Price: {WSTETH_RATIO * ETH_PRICE:.6f} ETH\n")

    # Update ORACLE_ADDRESS above before running!
    if ORACLE_ADDRESS == "0xYourOracleAddressHere":
        print("⚠️  WARNING: Please update ORACLE_ADDRESS in the script!")
        print("   Current value is a placeholder.\n")

    to_address, calldata = encode_prices(
        oracle_address=ORACLE_ADDRESS,
        eth_price_eth=ETH_PRICE,
        wsteth_ratio=WSTETH_RATIO,
    )

    # Also print as Python-compatible format for easy copy/paste
    print("\n📋 Python Format (for scripts):")
    print(f'to_address = "{to_address}"')
    print(f'calldata = bytes.fromhex("{calldata.hex()}")')
