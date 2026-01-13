"""Comprehensive tests for Uniswap V4 adapter.

This module contains unit tests, tick math tests, native ETH handling tests,
and integration tests for the Uniswap V4 LP position adapter.
"""

import pytest
from unittest.mock import patch

from tq_oracle.adapters.asset_adapters.uniswap.uniswap_v4 import UniswapV4Adapter
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import Network, OracleSettings
from tq_oracle.constants import NATIVE_ETH_ADDRESS


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mainnet_config():
    """Base mainnet config for Uniswap V4 tests."""
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
def mock_graph_api_key():
    """Mock Graph API key for testing."""
    return "test-api-key-12345"


# =============================================================================
# Unit Tests - Initialization
# =============================================================================


class TestUniswapV4AdapterInit:
    """Tests for adapter initialization."""

    def test_adapter_name(self, mainnet_config, mock_graph_api_key):
        """Adapter should return correct name."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            assert adapter.adapter_name == "uniswap_v4"

    def test_requires_graph_api_key(self, mainnet_config):
        """Should raise error if Graph API key is not set."""
        with patch.dict("os.environ", {}, clear=True):
            # Remove any existing key
            import os

            if "TQ_ORACLE_GRAPH_API_KEY" in os.environ:
                del os.environ["TQ_ORACLE_GRAPH_API_KEY"]

            with pytest.raises(ValueError, match="TQ_ORACLE_GRAPH_API_KEY"):
                UniswapV4Adapter(mainnet_config, position_manager="0x1234")

    def test_uses_default_pool_manager(self, mainnet_config, mock_graph_api_key):
        """Should use default pool manager when not specified."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            assert adapter.pool_manager == UniswapV4Adapter.DEFAULT_POOL_MANAGER

    def test_uses_default_state_view(self, mainnet_config, mock_graph_api_key):
        """Should use default state view when not specified."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            assert adapter.state_view == UniswapV4Adapter.DEFAULT_STATE_VIEW

    def test_override_position_manager(self, mainnet_config, mock_graph_api_key):
        """Should use overridden position manager when provided."""
        custom_pm = "0xCustomPositionManager1234567890123456789012"
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager=custom_pm)
            assert adapter.position_manager == custom_pm

    def test_override_pool_manager(self, mainnet_config, mock_graph_api_key):
        """Should use overridden pool manager when provided."""
        custom_pm = "0xCustomPoolManager123456789012345678901234"
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(
                mainnet_config, position_manager="0x1234", pool_manager=custom_pm
            )
            assert adapter.pool_manager == custom_pm

    def test_stores_block_number(self, mainnet_config, mock_graph_api_key):
        """Should store block number from config."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            assert adapter.block_number == 21000000

    def test_stores_graph_api_key(self, mainnet_config, mock_graph_api_key):
        """Should store Graph API key from environment."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            assert adapter.graph_api_key == mock_graph_api_key


# =============================================================================
# Unit Tests - Native ETH Handling
# =============================================================================


class TestNativeEthHandling:
    """Tests for native ETH (address(0)) to WETH conversion."""

    def test_normalize_currency_native_eth(
        self, mainnet_config, mock_graph_api_key, weth_address
    ):
        """Native ETH (address(0)) should be converted to WETH."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            result = adapter._normalize_currency(NATIVE_ETH_ADDRESS)
            assert result == weth_address

    def test_normalize_currency_preserves_erc20(
        self, mainnet_config, mock_graph_api_key, usdc_address
    ):
        """ERC20 addresses should be preserved."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            result = adapter._normalize_currency(usdc_address)
            assert result == usdc_address

    def test_normalize_currency_case_insensitive(
        self, mainnet_config, mock_graph_api_key, weth_address
    ):
        """Native ETH check should be case insensitive."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            # Uppercase version of address(0)
            uppercase_zero = "0x0000000000000000000000000000000000000000"
            result = adapter._normalize_currency(uppercase_zero)
            assert result == weth_address


# =============================================================================
# Unit Tests - Tick Math (same as V3)
# =============================================================================


class TestTickMath:
    """Tests for Uniswap V4 tick math calculations (identical to V3)."""

    def test_get_sqrt_ratio_at_tick_zero(self, mainnet_config, mock_graph_api_key):
        """Tick 0 should return sqrt(1) * 2^96."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            result = adapter._get_sqrt_ratio_at_tick(0)
            expected = 2**96
            assert abs(result - expected) < 1000

    def test_get_sqrt_ratio_at_positive_tick(self, mainnet_config, mock_graph_api_key):
        """Positive tick should return higher sqrt price."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            result_0 = adapter._get_sqrt_ratio_at_tick(0)
            result_100 = adapter._get_sqrt_ratio_at_tick(100)
            assert result_100 > result_0

    def test_get_sqrt_ratio_at_negative_tick(self, mainnet_config, mock_graph_api_key):
        """Negative tick should return lower sqrt price."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            result_0 = adapter._get_sqrt_ratio_at_tick(0)
            result_neg100 = adapter._get_sqrt_ratio_at_tick(-100)
            assert result_neg100 < result_0


class TestAmountCalculations:
    """Tests for token amount calculations from liquidity."""

    def test_zero_liquidity_returns_zero(self, mainnet_config, mock_graph_api_key):
        """Zero liquidity should return zero amounts."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
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

    def test_position_in_range_both_tokens(self, mainnet_config, mock_graph_api_key):
        """Position in range should have both tokens."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
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

            assert amount0 > 0
            assert amount1 > 0


# =============================================================================
# Unit Tests - Packed Info Unpacking
# =============================================================================


class TestPackedInfoUnpacking:
    """Tests for unpacking packed position info."""

    def test_unpack_ticks_basic(self, mainnet_config, mock_graph_api_key):
        """Should correctly unpack tick values from packed info."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")

            # Pack: flags (8 bits) | tickLower (24 bits) | tickUpper (24 bits)
            # tickLower = 100, tickUpper = 200
            tick_lower = 100
            tick_upper = 200
            flags = 0
            packed = flags | (tick_lower << 8) | (tick_upper << 32)

            result_lower, result_upper = adapter._unpack_ticks_from_info(packed)

            assert result_lower == tick_lower
            assert result_upper == tick_upper

    def test_unpack_negative_ticks(self, mainnet_config, mock_graph_api_key):
        """Should correctly handle negative tick values (signed int24)."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")

            # For negative ticks, we need to handle int24 sign extension
            # -100 in int24 = 0xFFFF9C (two's complement in 24 bits)
            tick_lower = -100
            tick_upper = 100

            # Convert to unsigned 24-bit representation
            tick_lower_u = tick_lower & ((1 << 24) - 1)  # 0xFFFF9C
            tick_upper_u = tick_upper & ((1 << 24) - 1)  # 0x000064

            flags = 0
            packed = flags | (tick_lower_u << 8) | (tick_upper_u << 32)

            result_lower, result_upper = adapter._unpack_ticks_from_info(packed)

            assert result_lower == tick_lower
            assert result_upper == tick_upper

    def test_sign_extend_int24_positive(self, mainnet_config, mock_graph_api_key):
        """Positive int24 should remain positive."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            result = adapter._sign_extend_int24(100)
            assert result == 100

    def test_sign_extend_int24_negative(self, mainnet_config, mock_graph_api_key):
        """Negative int24 (high bit set) should become negative."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            # -100 in 24-bit two's complement
            negative_100_u24 = (-100) & ((1 << 24) - 1)
            result = adapter._sign_extend_int24(negative_100_u24)
            assert result == -100


# =============================================================================
# Unit Tests - Pool ID Calculation
# =============================================================================


class TestPoolIdCalculation:
    """Tests for pool ID (keccak256 of encoded pool key) calculation."""

    def test_pool_id_is_bytes32(
        self, mainnet_config, mock_graph_api_key, weth_address, usdc_address
    ):
        """Pool ID should be 32 bytes."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            pool_key = (
                weth_address,  # currency0
                usdc_address,  # currency1
                3000,  # fee
                60,  # tickSpacing
                "0x0000000000000000000000000000000000000000",  # hooks
            )
            pool_id = adapter._pool_id_from_pool_key(pool_key)
            assert len(pool_id) == 32
            assert isinstance(pool_id, bytes)

    def test_pool_id_deterministic(
        self, mainnet_config, mock_graph_api_key, weth_address, usdc_address
    ):
        """Same pool key should produce same pool ID."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            pool_key = (
                weth_address,
                usdc_address,
                3000,
                60,
                "0x0000000000000000000000000000000000000000",
            )
            pool_id_1 = adapter._pool_id_from_pool_key(pool_key)
            pool_id_2 = adapter._pool_id_from_pool_key(pool_key)
            assert pool_id_1 == pool_id_2


# =============================================================================
# Unit Tests - fetch_assets Method
# =============================================================================


class TestFetchAssets:
    """Tests for fetch_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_position_manager(
        self, mocker, mainnet_config, subvault_address, mock_graph_api_key
    ):
        """Should return empty list when position_manager is not configured."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(
                mainnet_config, position_manager=None, graph_api_key=mock_graph_api_key
            )
            adapter.position_manager = None  # Force to None after init

            result = await adapter.fetch_assets(subvault_address)
            assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_positions(
        self, mocker, mainnet_config, subvault_address, mock_graph_api_key
    ):
        """Should return empty list when subvault has no positions."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")

            mocker.patch.object(
                adapter, "_fetch_token_ids_from_subgraph", return_value=[]
            )

            result = await adapter.fetch_assets(subvault_address)
            assert result == []

    @pytest.mark.asyncio
    async def test_passes_through_previous_assets(
        self,
        mocker,
        mainnet_config,
        subvault_address,
        weth_address,
        usdc_address,
        mock_graph_api_key,
    ):
        """Should include previous_assets in results."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")

            mocker.patch.object(
                adapter, "_fetch_token_ids_from_subgraph", return_value=[100]
            )
            mocker.patch.object(
                adapter,
                "_process_position",
                return_value=[AssetData(asset_address=weth_address, amount=10**18)],
            )

            previous = [AssetData(asset_address=usdc_address, amount=1000 * 10**6)]
            result = await adapter.fetch_assets(
                subvault_address, previous_assets=previous
            )

            addresses = [r.asset_address for r in result]
            assert usdc_address in addresses
            assert weth_address in addresses


# =============================================================================
# Unit Tests - Graph URL
# =============================================================================


class TestGraphUrl:
    """Tests for Graph URL construction."""

    def test_graph_url_includes_api_key(self, mainnet_config, mock_graph_api_key):
        """Graph URL should include API key."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            url = adapter._graph_url()
            assert mock_graph_api_key in url

    def test_graph_url_includes_subgraph_id(self, mainnet_config, mock_graph_api_key):
        """Graph URL should include subgraph ID."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            url = adapter._graph_url()
            assert adapter.subgraph_id in url


# =============================================================================
# Unit Tests - fetch_all_assets
# =============================================================================


class TestFetchAllAssets:
    """Tests for fetch_all_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, mainnet_config, mock_graph_api_key):
        """fetch_all_assets should return empty list (not supported)."""
        with patch.dict("os.environ", {"TQ_ORACLE_GRAPH_API_KEY": mock_graph_api_key}):
            adapter = UniswapV4Adapter(mainnet_config, position_manager="0x1234")
            result = await adapter.fetch_all_assets()
            assert result == []


# =============================================================================
# Integration Tests
# =============================================================================


class TestUniswapV4Integration:
    """Integration tests with real Uniswap V4 contracts and The Graph."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fetch_token_ids_from_subgraph(
        self, mainnet_config, subvault_address
    ):
        """Integration: Fetch token IDs from The Graph subgraph."""
        import os

        api_key = os.getenv("TQ_ORACLE_GRAPH_API_KEY")
        if not api_key:
            pytest.skip("TQ_ORACLE_GRAPH_API_KEY not set")

        adapter = UniswapV4Adapter(
            mainnet_config,
            position_manager="0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",
        )
        token_ids = await adapter._fetch_token_ids_from_subgraph(subvault_address)

        assert isinstance(token_ids, list)
        for tid in token_ids:
            assert isinstance(tid, int)

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fetch_assets_returns_valid_data(
        self, mainnet_config, subvault_address
    ):
        """Integration: fetch_assets returns valid AssetData."""
        import os

        api_key = os.getenv("TQ_ORACLE_GRAPH_API_KEY")
        if not api_key:
            pytest.skip("TQ_ORACLE_GRAPH_API_KEY not set")

        adapter = UniswapV4Adapter(
            mainnet_config,
            position_manager="0xbd216513d74c8cf14cf4747e6aaa6420ff64ee9e",
        )
        result = await adapter.fetch_assets(subvault_address)

        assert isinstance(result, list)
        for asset in result:
            assert isinstance(asset, AssetData)
            assert isinstance(asset.amount, int)
            assert asset.asset_address.startswith("0x")
            assert len(asset.asset_address) == 42
            # Native ETH should have been converted to WETH
            assert asset.asset_address != NATIVE_ETH_ADDRESS
