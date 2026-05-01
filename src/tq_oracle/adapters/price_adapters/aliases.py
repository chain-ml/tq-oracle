"""Cross-chain price alias resolution.

Substitutes cross-chain token addresses with mainnet equivalents before
pricing, then copies prices back to the original addresses afterward.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .base import PriceData

logger = logging.getLogger(__name__)


@dataclass
class AliasResolution:
    """Tracks alias substitutions for post-processing."""

    pricing_addresses: list[str] = field(default_factory=list)
    reverse_aliases: dict[str, list[str]] = field(default_factory=dict)
    has_aliases: bool = False


def resolve_aliases(
    asset_addresses: list[str],
    price_aliases: dict[str, str],
) -> AliasResolution:
    """Pre-process asset addresses by substituting aliases.

    Args:
        asset_addresses: Original asset addresses to price
        price_aliases: Map of cross-chain address → mainnet equivalent

    Returns:
        AliasResolution with substituted addresses and reverse mapping
    """
    if not price_aliases:
        return AliasResolution(pricing_addresses=asset_addresses)

    alias_map = {addr.lower(): mainnet for addr, mainnet in price_aliases.items()}
    reverse_aliases: dict[str, list[str]] = {}
    pricing_addresses: list[str] = []

    for addr in asset_addresses:
        mainnet = alias_map.get(addr.lower())
        if mainnet:
            pricing_addresses.append(mainnet)
            reverse_aliases.setdefault(mainnet.lower(), []).append(addr)
            logger.debug("Price alias: %s → %s", addr, mainnet)
        else:
            pricing_addresses.append(addr)

    # Deduplicate while preserving order
    pricing_addresses = list(dict.fromkeys(pricing_addresses))

    return AliasResolution(
        pricing_addresses=pricing_addresses,
        reverse_aliases=reverse_aliases,
        has_aliases=bool(reverse_aliases),
    )


def apply_aliases(price_data: PriceData, resolution: AliasResolution) -> None:
    """Post-process: copy prices from mainnet addresses back to originals.

    Args:
        price_data: Price data with mainnet prices populated
        resolution: Alias resolution from resolve_aliases()
    """
    if not resolution.has_aliases:
        return

    for mainnet_lower, originals in resolution.reverse_aliases.items():
        for priced_addr in list(price_data.prices):
            if priced_addr.lower() == mainnet_lower:
                for orig in originals:
                    price_data.prices[orig] = price_data.prices[priced_addr]
                    if priced_addr in price_data.decimals:
                        price_data.decimals[orig] = price_data.decimals[priced_addr]
                    logger.debug(
                        "Applied price alias: %s ← %s (price=%d)",
                        orig,
                        priced_addr,
                        price_data.prices[priced_addr],
                    )
                break
