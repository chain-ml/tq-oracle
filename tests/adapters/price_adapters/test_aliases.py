"""Tests for cross-chain price alias resolution."""

import pytest

from tq_oracle.adapters.price_adapters.aliases import (
    AliasResolution,
    apply_aliases,
    resolve_aliases,
)
from tq_oracle.adapters.price_adapters.base import PriceData


class TestResolveAliases:
    """Tests for resolve_aliases()."""

    def test_no_aliases_returns_original(self):
        """Empty alias map returns original addresses."""
        addresses = ["0xAAA", "0xBBB"]
        result = resolve_aliases(addresses, {})
        assert result.pricing_addresses == addresses
        assert result.has_aliases is False
        assert result.reverse_aliases == {}

    def test_substitutes_aliased_addresses(self):
        """Aliased addresses are substituted with mainnet equivalents."""
        addresses = ["0xMonadUSDC", "0xUnaliased"]
        aliases = {"0xMonadUSDC": "0xMainnetUSDC"}
        result = resolve_aliases(addresses, aliases)
        assert "0xMainnetUSDC" in result.pricing_addresses
        assert "0xUnaliased" in result.pricing_addresses
        assert "0xMonadUSDC" not in result.pricing_addresses
        assert result.has_aliases is True

    def test_case_insensitive_matching(self):
        """Alias matching should be case-insensitive."""
        addresses = ["0xABC"]
        aliases = {"0xabc": "0xMainnet"}
        result = resolve_aliases(addresses, aliases)
        assert "0xMainnet" in result.pricing_addresses
        assert result.has_aliases is True

    def test_deduplicates_pricing_addresses(self):
        """Multiple aliases to the same mainnet address should be deduplicated."""
        addresses = ["0xMonadUSDC", "0xArbitrumUSDC"]
        aliases = {
            "0xMonadUSDC": "0xMainnetUSDC",
            "0xArbitrumUSDC": "0xMainnetUSDC",
        }
        result = resolve_aliases(addresses, aliases)
        assert result.pricing_addresses.count("0xMainnetUSDC") == 1
        assert len(result.reverse_aliases) == 1

    def test_reverse_aliases_tracks_originals(self):
        """Reverse aliases should map mainnet back to all originals."""
        addresses = ["0xMonadUSDC", "0xArbitrumUSDC"]
        aliases = {
            "0xMonadUSDC": "0xMainnetUSDC",
            "0xArbitrumUSDC": "0xMainnetUSDC",
        }
        result = resolve_aliases(addresses, aliases)
        originals = result.reverse_aliases["0xmainnetusdc"]
        assert "0xMonadUSDC" in originals
        assert "0xArbitrumUSDC" in originals


class TestApplyAliases:
    """Tests for apply_aliases()."""

    def test_no_aliases_is_noop(self):
        """No aliases should not modify price data."""
        price_data = PriceData(
            base_asset="0xBase",
            prices={"0xToken": 1000},
        )
        resolution = AliasResolution(pricing_addresses=["0xToken"])
        apply_aliases(price_data, resolution)
        assert price_data.prices == {"0xToken": 1000}

    def test_copies_price_back_to_original(self):
        """Price from mainnet address should be copied to original."""
        price_data = PriceData(
            base_asset="0xBase",
            prices={"0xMainnetUSDC": 500000},
            decimals={"0xMainnetUSDC": 6},
        )
        resolution = AliasResolution(
            pricing_addresses=["0xMainnetUSDC"],
            reverse_aliases={"0xmainnetusdc": ["0xMonadUSDC"]},
            has_aliases=True,
        )
        apply_aliases(price_data, resolution)
        assert price_data.prices["0xMonadUSDC"] == 500000
        assert price_data.decimals["0xMonadUSDC"] == 6
        # Original mainnet price should still be there
        assert price_data.prices["0xMainnetUSDC"] == 500000

    def test_copies_to_multiple_originals(self):
        """Price should be copied to all originals mapping to same mainnet."""
        price_data = PriceData(
            base_asset="0xBase",
            prices={"0xMainnetUSDC": 500000},
        )
        resolution = AliasResolution(
            pricing_addresses=["0xMainnetUSDC"],
            reverse_aliases={
                "0xmainnetusdc": ["0xMonadUSDC", "0xArbitrumUSDC"],
            },
            has_aliases=True,
        )
        apply_aliases(price_data, resolution)
        assert price_data.prices["0xMonadUSDC"] == 500000
        assert price_data.prices["0xArbitrumUSDC"] == 500000
