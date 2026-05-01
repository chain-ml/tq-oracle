#!/usr/bin/env python3
"""Encode manual prices for WETH, ETH, and wstETH - outputs calldata only.

This script encodes prices in submitReports() calldata format:
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


def encode_prices_calldata(
    eth_price_eth: float = 0.9,  # ETH price in ETH (you specified 0.9)
    wsteth_ratio: float = 1.2241,  # wstETH ratio to ETH (you specified 1.2241)
) -> str:
    """Encode submitReports() calldata for manual prices.

    Args:
        eth_price_eth: ETH price in ETH units (you specified 0.9)
        wsteth_ratio: wstETH ratio to ETH (you specified 1.2241)

    Returns:
        Hex-encoded calldata string (with 0x prefix)
    """
    w3 = Web3()
    abi = load_oracle_abi()

    # Create a dummy contract just for encoding (address doesn't matter)
    contract = w3.eth.contract(
        address=w3.to_checksum_address("0x0000000000000000000000000000000000000000"),
        abi=abi,
    )

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
    print("MANUAL PRICE ENCODING FOR submitReports()")
    print("=" * 80)
    print(f"\n📊 Configuration:")
    print(f"   ETH/WETH Price: {eth_price_eth} ETH")
    print(f"   wstETH Ratio: {wsteth_ratio}x ETH")
    print(f"   wstETH Price: {wsteth_ratio * eth_price_eth:.6f} ETH")
    print(f"\n🔢 Prices being encoded ({len(reports_array)} assets):\n")

    for asset_addr, price_d18 in reports_array:
        price_decimal = price_d18 / 10**18
        # Get token name
        if asset_addr == w3.to_checksum_address(ETH_ADDRESS):
            token_name = "ETH"
        elif asset_addr == w3.to_checksum_address(WETH_ADDRESS):
            token_name = "WETH"
        elif asset_addr == w3.to_checksum_address(WSTETH_ADDRESS):
            token_name = "wstETH"
        else:
            token_name = "Unknown"

        print(f"  {token_name:8} {asset_addr}")
        print(f"           Price D18: {price_d18}")
        print(f"           Price:     {price_decimal:.18f} ETH")
        print()

    # Encode the transaction
    calldata_hex = contract.encode_abi(
        abi_element_identifier="submitReports",
        args=[reports_array],
    )

    print("=" * 80)
    print("✅ ENCODED CALLDATA (Paste this into Safe)")
    print("=" * 80)
    print(f"\n{calldata_hex}\n")
    print("=" * 80)
    print(f"Calldata length: {len(bytes.fromhex(calldata_hex.removeprefix('0x')))} bytes")
    print("=" * 80)

    return calldata_hex


if __name__ == "__main__":
    # Configuration
    ETH_PRICE = 0.95  # 0.9 ETH (as you specified)
    WSTETH_RATIO = 1.2241  # wstETH ratio (as you specified)

    calldata = encode_prices_calldata(
        eth_price_eth=ETH_PRICE,
        wsteth_ratio=WSTETH_RATIO,
    )

    print("\n📋 For Safe Transaction Builder:")
    print("   1. Go to your Safe's Transaction Builder")
    print("   2. Paste the calldata above into the 'Data (Hex encoded)' field")
    print("   3. Set the 'To Address' to your Oracle contract address")
    print("   4. Review and submit!\n")
