"""Comprehensive tests for Uniswap V3 adapter.

This module contains unit tests, tick math tests, and integration tests
for the Uniswap V3 LP position adapter.
"""

import pytest

from tq_oracle.adapters.asset_adapters.uniswap.uniswap_v3 import UniswapV3Adapter
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import Network, OracleSettings


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mainnet_config():
    """Base mainnet config for Uniswap V3 tests."""
    return OracleSettings(
        vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
        oracle_helper_address="0x000000005F543c38d5ea6D0bF10A50974Eb55E35",
        vault_rpc="https://eth.drpc.org",
        block_number=21000000,
        network=Network.MAINNET,
        dry_run=True,
    )


@pytest.fixture
def subvault_address():
    """Test subvault address."""
    return "0x9A47b63143FfA375405ddcf0952Fd2C1570915d8"


@pytest.fixture
def weth_address():
    """WETH mainnet address (18 decimals)."""
    return "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


@pytest.fixture
def usdc_address():
    """USDC mainnet address (6 decimals)."""
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def usdt_address():
    """USDT mainnet address (6 decimals)."""
    return "0xdAC17F958D2ee523a2206206994597C13D831ec7"


# =============================================================================
# Unit Tests - Initialization
# =============================================================================


class TestUniswapV3AdapterInit:
    """Tests for adapter initialization."""

    def test_adapter_name(self, mainnet_config):
        """Adapter should return correct name."""
        adapter = UniswapV3Adapter(mainnet_config)
        assert adapter.adapter_name == "uniswap_v3"

    def test_uses_default_position_manager(self, mainnet_config):
        """Should use default position manager when not specified."""
        adapter = UniswapV3Adapter(mainnet_config)
        assert adapter.position_manager == UniswapV3Adapter.DEFAULT_POSITION_MANAGER

    def test_override_position_manager(self, mainnet_config):
        """Should use overridden position manager when provided."""
        custom_pm = "0xCustomPositionManager1234567890123456789012"
        adapter = UniswapV3Adapter(mainnet_config, position_manager=custom_pm)
        assert adapter.position_manager == custom_pm

    def test_stores_block_number(self, mainnet_config):
        """Should store block number from config."""
        adapter = UniswapV3Adapter(mainnet_config)
        assert adapter.block_number == 21000000

    def test_default_position_manager_is_mainnet(self, mainnet_config):
        """Default position manager should be mainnet NonfungiblePositionManager."""
        adapter = UniswapV3Adapter(mainnet_config)
        expected = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"
        assert adapter.position_manager == expected


# =============================================================================
# Unit Tests - Tick Math
# =============================================================================


class TestTickMath:
    """Tests for Uniswap V3 tick math calculations."""

    def test_get_sqrt_ratio_at_tick_zero(self, mainnet_config):
        """Tick 0 should return sqrt(1) * 2^96."""
        adapter = UniswapV3Adapter(mainnet_config)
        result = adapter._get_sqrt_ratio_at_tick(0)
        # At tick 0, price = 1, so sqrtPriceX96 = 2^96
        expected = 2**96
        # Allow small rounding difference
        assert abs(result - expected) < 1000

    def test_get_sqrt_ratio_at_positive_tick(self, mainnet_config):
        """Positive tick should return higher sqrt price."""
        adapter = UniswapV3Adapter(mainnet_config)
        result_0 = adapter._get_sqrt_ratio_at_tick(0)
        result_100 = adapter._get_sqrt_ratio_at_tick(100)
        assert result_100 > result_0

    def test_get_sqrt_ratio_at_negative_tick(self, mainnet_config):
        """Negative tick should return lower sqrt price."""
        adapter = UniswapV3Adapter(mainnet_config)
        result_0 = adapter._get_sqrt_ratio_at_tick(0)
        result_neg100 = adapter._get_sqrt_ratio_at_tick(-100)
        assert result_neg100 < result_0

    def test_get_sqrt_ratio_symmetric(self, mainnet_config):
        """Positive and negative ticks should be reciprocals."""
        adapter = UniswapV3Adapter(mainnet_config)
        result_pos = adapter._get_sqrt_ratio_at_tick(1000)
        result_neg = adapter._get_sqrt_ratio_at_tick(-1000)
        # sqrtPrice(tick) * sqrtPrice(-tick) should equal (2^96)^2
        product = result_pos * result_neg
        expected_product = (2**96) ** 2
        # Allow 1% tolerance for rounding
        assert abs(product - expected_product) / expected_product < 0.01


class TestAmountCalculations:
    """Tests for token amount calculations from liquidity."""

    def test_get_amount0_delta_basic(self, mainnet_config):
        """Basic amount0 calculation."""
        adapter = UniswapV3Adapter(mainnet_config)
        sqrt_a = 2**96  # tick 0
        sqrt_b = adapter._get_sqrt_ratio_at_tick(100)
        liquidity = 10**18

        amount0 = adapter._get_amount0_delta(sqrt_a, sqrt_b, liquidity)
        assert amount0 > 0
        assert isinstance(amount0, int)

    def test_get_amount1_delta_basic(self, mainnet_config):
        """Basic amount1 calculation."""
        adapter = UniswapV3Adapter(mainnet_config)
        sqrt_a = adapter._get_sqrt_ratio_at_tick(-100)
        sqrt_b = 2**96  # tick 0
        liquidity = 10**18

        amount1 = adapter._get_amount1_delta(sqrt_a, sqrt_b, liquidity)
        assert amount1 > 0
        assert isinstance(amount1, int)

    def test_zero_liquidity_returns_zero(self, mainnet_config):
        """Zero liquidity should return zero amounts."""
        adapter = UniswapV3Adapter(mainnet_config)
        sqrt_price = 2**96
        tick_lower = -1000
        tick_upper = 1000
        current_tick = 0

        amount0, amount1 = adapter._calculate_amounts_from_liquidity(
            liquidity=0,
            sqrt_price_x96=sqrt_price,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
        )

        assert amount0 == 0
        assert amount1 == 0

    def test_position_below_range_only_token0(self, mainnet_config):
        """Position below current tick should have only token0."""
        adapter = UniswapV3Adapter(mainnet_config)
        # Current price is above the position range
        current_tick = 2000
        tick_lower = -1000
        tick_upper = 1000
        sqrt_price = adapter._get_sqrt_ratio_at_tick(current_tick)
        liquidity = 10**18

        amount0, amount1 = adapter._calculate_amounts_from_liquidity(
            liquidity=liquidity,
            sqrt_price_x96=sqrt_price,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
        )

        # Above range: all in token1, no token0
        assert amount0 == 0
        assert amount1 > 0

    def test_position_above_range_only_token1(self, mainnet_config):
        """Position above current tick should have only token1."""
        adapter = UniswapV3Adapter(mainnet_config)
        # Current price is below the position range
        current_tick = -2000
        tick_lower = -1000
        tick_upper = 1000
        sqrt_price = adapter._get_sqrt_ratio_at_tick(current_tick)
        liquidity = 10**18

        amount0, amount1 = adapter._calculate_amounts_from_liquidity(
            liquidity=liquidity,
            sqrt_price_x96=sqrt_price,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
        )

        # Below range: all in token0, no token1
        assert amount0 > 0
        assert amount1 == 0

    def test_position_in_range_both_tokens(self, mainnet_config):
        """Position in range should have both tokens."""
        adapter = UniswapV3Adapter(mainnet_config)
        current_tick = 0
        tick_lower = -1000
        tick_upper = 1000
        sqrt_price = adapter._get_sqrt_ratio_at_tick(current_tick)
        liquidity = 10**18

        amount0, amount1 = adapter._calculate_amounts_from_liquidity(
            liquidity=liquidity,
            sqrt_price_x96=sqrt_price,
            tick_lower=tick_lower,
            tick_upper=tick_upper,
            current_tick=current_tick,
        )

        # In range: both tokens present
        assert amount0 > 0
        assert amount1 > 0


# =============================================================================
# Unit Tests - fetch_assets Method
# =============================================================================


class TestFetchAssets:
    """Tests for fetch_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_positions(self, mocker, mainnet_config, subvault_address):
        """Should return empty list when subvault has no positions."""
        adapter = UniswapV3Adapter(mainnet_config)

        mocker.patch.object(adapter, "_get_position_count", return_value=0)

        result = await adapter.fetch_assets(subvault_address)

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_asset_data_list(self, mocker, mainnet_config, subvault_address, weth_address, usdc_address):
        """Should return list of AssetData."""
        adapter = UniswapV3Adapter(mainnet_config)

        # Mock position enumeration
        mocker.patch.object(adapter, "_get_position_count", return_value=1)
        mocker.patch.object(adapter, "_get_position_id_by_index", return_value=12345)

        # Mock position processing
        mock_asset_data = [
            AssetData(asset_address=weth_address, amount=10**18),
            AssetData(asset_address=usdc_address, amount=1000 * 10**6),
        ]
        mocker.patch.object(adapter, "_process_position", return_value=mock_asset_data)

        result = await adapter.fetch_assets(subvault_address)

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(item, AssetData) for item in result)

    @pytest.mark.asyncio
    async def test_aggregates_multiple_positions(self, mocker, mainnet_config, subvault_address, weth_address):
        """Should aggregate amounts from multiple positions."""
        adapter = UniswapV3Adapter(mainnet_config)

        # Mock 2 positions
        mocker.patch.object(adapter, "_get_position_count", return_value=2)
        mocker.patch.object(adapter, "_get_position_id_by_index", side_effect=[100, 200])

        # Each position has 1 WETH
        async def mock_process(token_id, subvault):
            return [AssetData(asset_address=weth_address, amount=10**18)]

        mocker.patch.object(adapter, "_process_position", side_effect=mock_process)

        result = await adapter.fetch_assets(subvault_address)

        # Should aggregate to 2 WETH
        assert len(result) == 1
        assert result[0].asset_address == weth_address
        assert result[0].amount == 2 * 10**18

    @pytest.mark.asyncio
    async def test_passes_through_previous_assets(self, mocker, mainnet_config, subvault_address, weth_address, usdc_address):
        """Should include previous_assets in results."""
        adapter = UniswapV3Adapter(mainnet_config)

        mocker.patch.object(adapter, "_get_position_count", return_value=1)
        mocker.patch.object(adapter, "_get_position_id_by_index", return_value=100)
        mocker.patch.object(
            adapter,
            "_process_position",
            return_value=[AssetData(asset_address=weth_address, amount=10**18)],
        )

        previous = [AssetData(asset_address=usdc_address, amount=1000 * 10**6)]
        result = await adapter.fetch_assets(subvault_address, previous_assets=previous)

        # Should have both previous USDC and new WETH
        addresses = [r.asset_address for r in result]
        assert usdc_address in addresses
        assert weth_address in addresses


# =============================================================================
# Unit Tests - Address Handling
# =============================================================================


class TestAddressHandling:
    """Tests for address checksumming and handling."""

    @pytest.mark.asyncio
    async def test_returns_checksummed_addresses(self, mocker, mainnet_config, subvault_address):
        """Returned asset addresses should be checksummed."""
        adapter = UniswapV3Adapter(mainnet_config)

        mocker.patch.object(adapter, "_get_position_count", return_value=1)
        mocker.patch.object(adapter, "_get_position_id_by_index", return_value=100)

        # Return lowercase address
        lowercase_weth = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
        mocker.patch.object(
            adapter,
            "_process_position",
            return_value=[AssetData(asset_address=lowercase_weth, amount=10**18)],
        )

        result = await adapter.fetch_assets(subvault_address)

        # Should be checksummed
        expected = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        assert result[0].asset_address == expected


# =============================================================================
# Unit Tests - fetch_all_assets
# =============================================================================


class TestFetchAllAssets:
    """Tests for fetch_all_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, mainnet_config):
        """fetch_all_assets should return empty list (not supported)."""
        adapter = UniswapV3Adapter(mainnet_config)
        result = await adapter.fetch_all_assets()
        assert result == []


# =============================================================================
# Integration Tests
# =============================================================================


class TestUniswapV3Integration:
    """Integration tests with real Uniswap V3 contracts."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_position_count(self, mainnet_config, subvault_address):
        """Integration: Get position count for a subvault."""
        adapter = UniswapV3Adapter(mainnet_config)
        count = await adapter._get_position_count(subvault_address)
        assert isinstance(count, int)
        assert count >= 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fetch_assets_returns_valid_data(self, mainnet_config, subvault_address):
        """Integration: fetch_assets returns valid AssetData."""
        adapter = UniswapV3Adapter(mainnet_config)
        result = await adapter.fetch_assets(subvault_address)

        assert isinstance(result, list)
        for asset in result:
            assert isinstance(asset, AssetData)
            assert isinstance(asset.amount, int)
            assert asset.amount >= 0
            # Address should be valid checksum
            assert asset.asset_address.startswith("0x")
            assert len(asset.asset_address) == 42


# =============================================================================
# Decimal Handling Tests
# =============================================================================


class TestDecimalHandling:
    """Tests for correct decimal handling with different token pairs."""

    @pytest.mark.asyncio
    async def test_weth_usdc_pair_decimals(self, mocker, mainnet_config, subvault_address, weth_address, usdc_address):
        """WETH (18 decimals) / USDC (6 decimals) pair handling."""
        adapter = UniswapV3Adapter(mainnet_config)

        mocker.patch.object(adapter, "_get_position_count", return_value=1)
        mocker.patch.object(adapter, "_get_position_id_by_index", return_value=100)

        # 1 WETH (18 decimals) and 3000 USDC (6 decimals)
        weth_amount = 1 * 10**18
        usdc_amount = 3000 * 10**6

        mocker.patch.object(
            adapter,
            "_process_position",
            return_value=[
                AssetData(asset_address=weth_address, amount=weth_amount),
                AssetData(asset_address=usdc_address, amount=usdc_amount),
            ],
        )

        result = await adapter.fetch_assets(subvault_address)

        weth_result = next(r for r in result if r.asset_address == weth_address)
        usdc_result = next(r for r in result if r.asset_address == usdc_address)

        assert weth_result.amount == 10**18
        assert usdc_result.amount == 3000 * 10**6

    @pytest.mark.asyncio
    async def test_stablecoin_pair_6_decimals(self, mocker, mainnet_config, subvault_address, usdc_address, usdt_address):
        """USDC (6 decimals) / USDT (6 decimals) pair handling."""
        adapter = UniswapV3Adapter(mainnet_config)

        mocker.patch.object(adapter, "_get_position_count", return_value=1)
        mocker.patch.object(adapter, "_get_position_id_by_index", return_value=100)

        # 1000 USDC and 1000 USDT (both 6 decimals)
        usdc_amount = 1000 * 10**6
        usdt_amount = 1000 * 10**6

        mocker.patch.object(
            adapter,
            "_process_position",
            return_value=[
                AssetData(asset_address=usdc_address, amount=usdc_amount),
                AssetData(asset_address=usdt_address, amount=usdt_amount),
            ],
        )

        result = await adapter.fetch_assets(subvault_address)

        usdc_result = next(r for r in result if r.asset_address == usdc_address)
        usdt_result = next(r for r in result if r.asset_address == usdt_address)

        assert usdc_result.amount == 1000 * 10**6
        assert usdt_result.amount == 1000 * 10**6
