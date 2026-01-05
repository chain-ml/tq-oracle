"""Utilities for converting asset amounts between different denominations.

This module provides flexible conversion utilities for:
- USD stablecoins to ETH
- ERC-4626 vault shares to underlying assets
- Custom on-chain conversions for RWAs and other assets
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from web3 import Web3

from ..logger import get_logger

if TYPE_CHECKING:
    from ..adapters.price_adapters.base import PriceData
    from ..settings import OracleSettings

logger = get_logger(__name__)


async def convert_usd_to_eth(
    usd_amount: int,
    usd_asset_address: str,
    prices: PriceData,
) -> int:
    """Convert USD amount to ETH using prices from PriceData.

    Args:
        usd_amount: Amount in USD asset (in native decimals)
        usd_asset_address: Address of the USD stablecoin
        prices: PriceData containing ETH-denominated prices

    Returns:
        Equivalent amount in ETH (wei)

    Example:
        If USDC price is 0.0004 ETH per USDC (18 decimals: 400000000000000)
        And usd_amount is 1000 USDC (6 decimals: 1000000000)
        Then: (1000000000 * 400000000000000) / 10^18 = 400000000000 wei (0.0004 ETH)
    """
    if usd_asset_address not in prices.prices:
        raise ValueError(f"Price not found for USD asset: {usd_asset_address}")

    usd_price_in_eth = prices.prices[usd_asset_address]
    eth_amount = (usd_amount * usd_price_in_eth) // (10**18)

    logger.debug(
        "Converted %d of %s to %d wei ETH using price %d",
        usd_amount,
        usd_asset_address,
        eth_amount,
        usd_price_in_eth,
    )

    return eth_amount


async def convert_shares_to_assets_erc4626(
    vault_address: str,
    shares: int,
    config: OracleSettings,
) -> tuple[str, int]:
    """Convert ERC-4626 vault shares to underlying asset amount.

    This calls the convertToAssets(shares) function on the ERC-4626 vault
    to get the current value of shares in terms of underlying assets.

    Args:
        vault_address: Address of the ERC-4626 vault
        shares: Number of shares to convert
        config: Oracle settings with RPC configuration

    Returns:
        Tuple of (underlying_asset_address, asset_amount)

    Example:
        For an RWA vault where 100 shares = 105 USDC:
        convert_shares_to_assets_erc4626(vault, 100e18, config)
        -> (usdc_address, 105e6)
    """
    w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))

    # ERC-4626 ABI for convertToAssets and asset
    erc4626_abi = [
        {
            "inputs": [
                {"internalType": "uint256", "name": "shares", "type": "uint256"}
            ],
            "name": "convertToAssets",
            "outputs": [
                {"internalType": "uint256", "name": "assets", "type": "uint256"}
            ],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [],
            "name": "asset",
            "outputs": [
                {
                    "internalType": "address",
                    "name": "assetTokenAddress",
                    "type": "address",
                }
            ],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(vault_address),
        abi=erc4626_abi,
    )

    # Get underlying asset address and convert shares to assets
    underlying_address, asset_amount = await asyncio.gather(
        asyncio.to_thread(
            contract.functions.asset().call,
            block_identifier=config.block_number_required,
        ),
        asyncio.to_thread(
            contract.functions.convertToAssets(shares).call,
            block_identifier=config.block_number_required,
        ),
    )

    logger.debug(
        "Converted %d shares of %s to %d of underlying %s",
        shares,
        vault_address,
        asset_amount,
        underlying_address,
    )

    return underlying_address, asset_amount


async def convert_asset_to_eth(
    asset_address: str,
    amount: int,
    prices: PriceData,
) -> int:
    """Convert any asset amount to ETH using prices.

    This is a general-purpose conversion that works for any asset
    with a price in the PriceData.

    Args:
        asset_address: Address of the asset
        amount: Amount in native asset decimals
        prices: PriceData containing ETH-denominated prices

    Returns:
        Amount in ETH (wei)
    """
    if asset_address not in prices.prices:
        raise ValueError(f"Price not found for asset: {asset_address}")

    asset_price_in_eth = prices.prices[asset_address]
    eth_amount = (amount * asset_price_in_eth) // (10**18)

    logger.debug(
        "Converted %d of %s to %d wei ETH using price %d",
        amount,
        asset_address,
        eth_amount,
        asset_price_in_eth,
    )

    return eth_amount


class RWAConverter:
    """Helper for converting RWA vault positions to base assets.

    This class handles the two-step conversion:
    1. RWA vault shares -> underlying asset (via ERC-4626)
    2. Underlying asset -> ETH (via prices)

    Usage:
        converter = RWAConverter(config)
        underlying_addr, amount = await converter.convert_to_underlying(vault_addr, shares)
        eth_value = await converter.convert_to_eth(vault_addr, shares, prices)
    """

    def __init__(self, config: OracleSettings):
        """Initialize RWA converter.

        Args:
            config: Oracle settings with RPC configuration
        """
        self.config = config

    async def convert_to_underlying(
        self,
        vault_address: str,
        shares: int,
    ) -> tuple[str, int]:
        """Convert RWA vault shares to underlying asset.

        Args:
            vault_address: Address of the ERC-4626 RWA vault
            shares: Number of shares

        Returns:
            Tuple of (underlying_asset_address, amount)
        """
        return await convert_shares_to_assets_erc4626(
            vault_address, shares, self.config
        )

    async def convert_to_eth(
        self,
        vault_address: str,
        shares: int,
        prices: PriceData,
    ) -> int:
        """Convert RWA vault shares to ETH value.

        This performs:
        1. Vault shares -> underlying asset (ERC-4626 convertToAssets)
        2. Underlying asset -> ETH (using prices)

        Args:
            vault_address: Address of the ERC-4626 RWA vault
            shares: Number of shares
            prices: PriceData for pricing underlying asset

        Returns:
            Amount in ETH (wei)
        """
        underlying_address, underlying_amount = await self.convert_to_underlying(
            vault_address, shares
        )

        return await convert_asset_to_eth(underlying_address, underlying_amount, prices)

    async def convert_to_usd(
        self,
        vault_address: str,
        shares: int,
        usd_asset_address: str | None = None,
    ) -> tuple[str, int]:
        """Convert RWA vault shares to USD value.

        If the underlying asset IS a USD stablecoin, returns it directly.
        Otherwise, you'll need to convert via prices.

        Args:
            vault_address: Address of the ERC-4626 RWA vault
            shares: Number of shares
            usd_asset_address: Expected USD asset (for validation)

        Returns:
            Tuple of (underlying_asset_address, amount)

        Note:
            This assumes the RWA vault's underlying IS the USD asset.
            For non-USD RWAs, use convert_to_eth() instead.
        """
        underlying_address, underlying_amount = await self.convert_to_underlying(
            vault_address, shares
        )

        if (
            usd_asset_address
            and underlying_address.lower() != usd_asset_address.lower()
        ):
            logger.warning(
                "RWA vault %s has underlying %s but expected %s",
                vault_address,
                underlying_address,
                usd_asset_address,
            )

        return underlying_address, underlying_amount
