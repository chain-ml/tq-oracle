"""Comprehensive tests for ManualPriceAdapter."""

import pytest
from unittest.mock import MagicMock

from tq_oracle.adapters.price_adapters.base import PriceData
from tq_oracle.adapters.price_adapters.manual import ManualPriceAdapter
from tq_oracle.settings import Network, OracleSettings


@pytest.fixture
def config_with_manual_prices():
    """Config with manual prices configured."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        manual_prices={
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": 333333,  # USDC
            "0xdAC17F958D2ee523a2206206994597C13D831ec7": 333334,  # USDT
        },
    )


@pytest.fixture
def config_no_manual_prices():
    """Config without manual prices."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        manual_prices={},
    )


@pytest.fixture
def config_with_zero_price():
    """Config with zero price (for testing)."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        manual_prices={
            "0xZeroPrice": 0,
        },
    )


@pytest.fixture
def eth_address(config_with_manual_prices):
    return config_with_manual_prices.assets["ETH"]


@pytest.fixture
def usdc_address():
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def usdt_address():
    return "0xdAC17F958D2ee523a2206206994597C13D831ec7"


class TestManualPriceAdapterInit:
    """Tests for ManualPriceAdapter initialization."""

    def test_adapter_name(self, config_with_manual_prices):
        adapter = ManualPriceAdapter(config_with_manual_prices)
        assert adapter.adapter_name == "manual"

    def test_skip_when_no_prices_configured(self, config_no_manual_prices):
        adapter = ManualPriceAdapter(config_no_manual_prices)
        assert adapter._skip is True

    def test_not_skip_when_prices_configured(self, config_with_manual_prices):
        adapter = ManualPriceAdapter(config_with_manual_prices)
        assert adapter._skip is False

    def test_manual_prices_lowercased(self, config_with_manual_prices):
        adapter = ManualPriceAdapter(config_with_manual_prices)
        for addr in adapter.manual_prices:
            assert addr == addr.lower()

    def test_preserves_price_values(self, config_with_manual_prices):
        adapter = ManualPriceAdapter(config_with_manual_prices)
        usdc_lower = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
        assert adapter.manual_prices[usdc_lower] == 333333

    def test_initializes_decimals_cache(self, config_with_manual_prices):
        adapter = ManualPriceAdapter(config_with_manual_prices)
        assert adapter._decimals_cache == {}

    def test_stores_eth_address(self, config_with_manual_prices):
        adapter = ManualPriceAdapter(config_with_manual_prices)
        assert adapter.eth_address == config_with_manual_prices.assets["ETH"]


class TestFetchPrices:
    """Tests for fetch_prices method."""

    @pytest.mark.asyncio
    async def test_skipped_adapter_returns_unchanged(
        self, config_no_manual_prices, eth_address
    ):
        """Adapter with no prices should return accumulator unchanged."""
        adapter = ManualPriceAdapter(config_no_manual_prices)
        accumulator = PriceData(base_asset=eth_address, prices={"0x111": 100})

        result = await adapter.fetch_prices(["0xUSDC"], accumulator)

        assert result.prices == {"0x111": 100}

    @pytest.mark.asyncio
    async def test_raises_on_wrong_base_asset(self, config_with_manual_prices):
        """Should raise ValueError if base_asset is not ETH."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        wrong_base = "0xWrongBase"

        with pytest.raises(ValueError, match="only supports ETH as base asset"):
            await adapter.fetch_prices(
                ["0xUSDC"],
                PriceData(base_asset=wrong_base, prices={}),
            )

    @pytest.mark.asyncio
    async def test_applies_manual_price(
        self, mocker, config_with_manual_prices, eth_address, usdc_address
    ):
        """Should apply configured manual price."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert usdc_address in result.prices
        assert result.prices[usdc_address] == 333333

    @pytest.mark.asyncio
    async def test_applies_multiple_manual_prices(
        self, mocker, config_with_manual_prices, eth_address, usdc_address, usdt_address
    ):
        """Should apply manual prices for multiple tokens."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address, usdt_address], accumulator)

        assert result.prices[usdc_address] == 333333
        assert result.prices[usdt_address] == 333334

    @pytest.mark.asyncio
    async def test_skips_non_configured_tokens(
        self, mocker, config_with_manual_prices, eth_address
    ):
        """Tokens without manual prices should not be priced."""
        adapter = ManualPriceAdapter(config_with_manual_prices)

        non_configured = "0xNotConfigured"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([non_configured], accumulator)

        assert non_configured not in result.prices

    @pytest.mark.asyncio
    async def test_preserves_existing_prices(
        self, mocker, config_with_manual_prices, eth_address, usdc_address
    ):
        """Should preserve prices already in accumulator (but manual overrides them)."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        # Note: Manual adapter should OVERRIDE existing prices
        existing_prices = {"0x111": 999, usdc_address: 111111}
        accumulator = PriceData(base_asset=eth_address, prices=existing_prices.copy())
        result = await adapter.fetch_prices([usdc_address], accumulator)

        # Non-configured token should be preserved
        assert result.prices["0x111"] == 999
        # Configured token should be overridden
        assert result.prices[usdc_address] == 333333

    @pytest.mark.asyncio
    async def test_case_insensitive_matching(
        self, mocker, config_with_manual_prices, eth_address
    ):
        """Token matching should be case-insensitive."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        # Use uppercase version
        uppercase_usdc = "0xA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([uppercase_usdc], accumulator)

        assert uppercase_usdc in result.prices
        assert result.prices[uppercase_usdc] == 333333

    @pytest.mark.asyncio
    async def test_stores_token_decimals(
        self, mocker, config_with_manual_prices, eth_address, usdc_address
    ):
        """Should store token decimals in result."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert usdc_address in result.decimals
        assert result.decimals[usdc_address] == 6


class TestValidationSkipping:
    """Tests verifying that ManualPriceAdapter skips validation."""

    @pytest.mark.asyncio
    async def test_allows_zero_price(self, mocker, config_with_zero_price, eth_address):
        """Manual adapter should allow zero prices (for testing)."""
        adapter = ManualPriceAdapter(config_with_zero_price)
        mocker.patch.object(adapter, "get_token_decimals", return_value=18)

        zero_price_token = "0xZeroPrice"
        accumulator = PriceData(base_asset=eth_address, prices={})

        # Should NOT raise, even though price is 0
        result = await adapter.fetch_prices([zero_price_token], accumulator)

        assert zero_price_token in result.prices
        assert result.prices[zero_price_token] == 0

    @pytest.mark.asyncio
    async def test_does_not_call_validate_prices(
        self, mocker, config_with_manual_prices, eth_address, usdc_address
    ):
        """Should NOT call validate_prices (intentionally skipped)."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        # Spy on validate_prices
        mock_validate = mocker.patch.object(adapter, "validate_prices")

        accumulator = PriceData(base_asset=eth_address, prices={})
        await adapter.fetch_prices([usdc_address], accumulator)

        # validate_prices should NOT be called
        mock_validate.assert_not_called()


class TestNegativePrices:
    """Tests for negative price handling."""

    @pytest.mark.asyncio
    async def test_allows_negative_price(self, mocker, eth_address):
        """Manual adapter allows negative prices (validation is skipped).

        This is a potential issue - negative prices could cause problems
        downstream. This test documents the current behavior.
        """
        config = OracleSettings(
            vault_address="0xVault",
            oracle_helper_address="0xOracleHelper",
            vault_rpc="https://eth.drpc.org",
            block_number=23690139,
            network=Network.MAINNET,
            dry_run=False,
            manual_prices={
                "0xNegativePrice": -100,  # Negative price!
            },
        )
        adapter = ManualPriceAdapter(config)
        mocker.patch.object(adapter, "get_token_decimals", return_value=18)

        negative_token = "0xNegativePrice"
        accumulator = PriceData(base_asset=eth_address, prices={})

        # Should NOT raise, even though price is negative
        result = await adapter.fetch_prices([negative_token], accumulator)

        assert negative_token in result.prices
        assert result.prices[negative_token] == -100


class TestTokenDecimalsCache:
    """Tests for token decimals caching."""

    @pytest.mark.asyncio
    async def test_caches_decimals(self, mocker, config_with_manual_prices):
        """Token decimals should be cached after first fetch."""
        adapter = ManualPriceAdapter(config_with_manual_prices)

        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.return_value = 6
        mocker.patch.object(adapter.w3.eth, "contract", return_value=mock_contract)

        token = "0x1234567890123456789012345678901234567890"

        # First call
        result1 = await adapter.get_token_decimals(token)
        assert result1 == 6

        # Second call should use cache
        result2 = await adapter.get_token_decimals(token)
        assert result2 == 6

        # Contract should only be called once
        assert mock_contract.functions.decimals.call_count == 1


class TestHighPriorityBehavior:
    """Tests verifying ManualPriceAdapter's high-priority override behavior."""

    @pytest.mark.asyncio
    async def test_overrides_existing_price(
        self, mocker, config_with_manual_prices, eth_address, usdc_address
    ):
        """Manual prices should override any existing prices in accumulator.

        This is the key behavior - ManualPriceAdapter has HIGHEST priority,
        so it overrides prices set by any other adapter.
        """
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        # Simulate prices from lower-priority adapters
        accumulator = PriceData(
            base_asset=eth_address,
            prices={
                usdc_address: 999999,  # Price from CoW Swap or Chainlink
            },
        )

        result = await adapter.fetch_prices([usdc_address], accumulator)

        # Manual price should override
        assert result.prices[usdc_address] == 333333  # Not 999999


class TestEdgeCases:
    """Tests for edge cases."""

    @pytest.mark.asyncio
    async def test_empty_asset_list(
        self, mocker, config_with_manual_prices, eth_address
    ):
        """Should handle empty asset list gracefully."""
        adapter = ManualPriceAdapter(config_with_manual_prices)

        accumulator = PriceData(base_asset=eth_address, prices={"0x111": 100})
        result = await adapter.fetch_prices([], accumulator)

        # Original prices preserved, nothing added
        assert result.prices == {"0x111": 100}

    @pytest.mark.asyncio
    async def test_very_large_price(self, mocker, eth_address):
        """Should handle very large price values."""
        large_price = 10**30  # Very large price
        config = OracleSettings(
            vault_address="0xVault",
            oracle_helper_address="0xOracleHelper",
            vault_rpc="https://eth.drpc.org",
            block_number=23690139,
            network=Network.MAINNET,
            dry_run=False,
            manual_prices={
                "0xLargePrice": large_price,
            },
        )
        adapter = ManualPriceAdapter(config)
        mocker.patch.object(adapter, "get_token_decimals", return_value=18)

        large_token = "0xLargePrice"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([large_token], accumulator)

        assert result.prices[large_token] == large_price

    @pytest.mark.asyncio
    async def test_price_as_int_not_float(
        self, mocker, config_with_manual_prices, eth_address, usdc_address
    ):
        """Prices should be stored as integers, not floats."""
        adapter = ManualPriceAdapter(config_with_manual_prices)
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert isinstance(result.prices[usdc_address], int)
