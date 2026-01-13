"""Comprehensive tests for Pendle adapter.

This module contains unit tests, decimal edge case tests, and integration tests
for the Pendle PT and LP mark-to-market adapter.
"""

import pytest
from tq_oracle.adapters.asset_adapters.pendle import PendleAdapter
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import Network, OracleSettings
from tq_oracle.constants import PENDLE_ORACLE_MAINNET


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mainnet_config():
    """Base mainnet config for Pendle tests."""
    return OracleSettings(
        vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
        oracle_helper_address="0x000000005F543c38d5ea6D0bF10A50974Eb55E35",
        vault_rpc="https://eth.drpc.org",
        block_number=21000000,
        network=Network.MAINNET,
        dry_run=True,
    )


@pytest.fixture
def sepolia_config():
    """Sepolia config - Pendle adapter should skip."""
    return OracleSettings(
        vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
        oracle_helper_address="0x65464fe20562C22B2802B4094d3E042E18b5dfC2",
        vault_rpc="https://sepolia.drpc.org",
        block_number=9522842,
        network=Network.SEPOLIA,
        dry_run=True,
    )


@pytest.fixture
def subvault_address():
    """Test subvault address."""
    return "0x90c983DC732e65DB6177638f0125914787b8Cb78"


# Common token addresses
@pytest.fixture
def usdc_address():
    """USDC mainnet address (6 decimals)."""
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def weth_address():
    """WETH mainnet address (18 decimals)."""
    return "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


@pytest.fixture
def usde_address():
    """USDe mainnet address (18 decimals)."""
    return "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"


@pytest.fixture
def wsteth_address():
    """wstETH mainnet address (18 decimals)."""
    return "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"


# Example Pendle market addresses (mainnet)
@pytest.fixture
def pendle_usde_market():
    """Pendle USDe market address (example)."""
    return "0x8A49f2AC2730ba15AB4BffaDB6cf1cB5Db55a3A8"


@pytest.fixture
def pendle_wsteth_market():
    """Pendle wstETH market address (example)."""
    return "0xD0354D4e7bCf345fB117cabe41aCaDb724eccCa2"


# =============================================================================
# Unit Tests - Initialization
# =============================================================================


class TestPendleAdapterInit:
    """Tests for adapter initialization."""

    def test_adapter_name(self, mainnet_config):
        """Adapter should return correct name."""
        adapter = PendleAdapter(mainnet_config)
        assert adapter.adapter_name == "pendle"

    def test_skips_on_non_mainnet(self, sepolia_config):
        """Adapter should skip on non-mainnet networks."""
        adapter = PendleAdapter(sepolia_config)
        assert adapter._skip is True

    def test_not_skipped_on_mainnet(self, mainnet_config):
        """Adapter should not skip on mainnet."""
        adapter = PendleAdapter(mainnet_config)
        assert adapter._skip is False

    def test_uses_default_oracle_address(self, mainnet_config):
        """Should use default Pendle oracle address when not specified."""
        adapter = PendleAdapter(mainnet_config)
        assert adapter.oracle_address == PENDLE_ORACLE_MAINNET

    def test_override_oracle_address(self, mainnet_config):
        """Should use overridden oracle address when provided."""
        custom_oracle = "0xCustomOracleAddress1234567890123456789012"
        adapter = PendleAdapter(mainnet_config, oracle_address=custom_oracle)
        assert adapter.oracle_address == custom_oracle

    def test_empty_markets_by_default(self, mainnet_config):
        """Markets should be empty by default."""
        adapter = PendleAdapter(mainnet_config)
        assert adapter.markets == {}

    def test_override_markets(self, mainnet_config, usdc_address):
        """Should use overridden markets configuration."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)
        assert adapter.markets == markets
        assert len(adapter.markets) == 1

    def test_stores_block_number(self, mainnet_config):
        """Should store block number from config."""
        adapter = PendleAdapter(mainnet_config)
        assert adapter.block_number == 21000000


# =============================================================================
# Unit Tests - fetch_assets Method
# =============================================================================


class TestPendleFetchAssets:
    """Tests for fetch_assets method with mocked RPC calls."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_skipped(self, sepolia_config, subvault_address):
        """Skipped adapter should return empty list."""
        adapter = PendleAdapter(sepolia_config)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_markets_configured(
        self, mainnet_config, subvault_address
    ):
        """Should return empty list when no markets are configured."""
        adapter = PendleAdapter(mainnet_config)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_skips_market_missing_config(self, mainnet_config, subvault_address):
        """Should skip markets with missing required config fields."""
        markets = {
            "incomplete_market": {
                "market": "0x1234567890123456789012345678901234567890",
                # missing accounting_asset
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_asset_data_list(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """Should return list of AssetData when positions exist."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        # Mock PT address lookup
        mocker.patch.object(
            adapter,
            "_get_pt_address",
            return_value="0xPTAddress12345678901234567890123456789012",
        )
        # Mock balances: PT balance = 1000 * 10^6, LP balance = 0
        mocker.patch.object(adapter, "_balance_of", side_effect=[1000 * 10**6, 0])
        # Mock PT rate: 0.95 (95% of underlying)
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=95 * 10**16)

        result = await adapter.fetch_assets(subvault_address)

        assert isinstance(result, list)
        assert all(isinstance(item, AssetData) for item in result)

    @pytest.mark.asyncio
    async def test_zero_balance_not_included(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """Zero balance positions should not be in results."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        mocker.patch.object(
            adapter,
            "_get_pt_address",
            return_value="0xPTAddress12345678901234567890123456789012",
        )
        mocker.patch.object(adapter, "_balance_of", return_value=0)
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=10**18)
        mocker.patch.object(adapter, "_get_lp_to_asset_rate", return_value=10**18)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_returns_accounting_asset_address(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """Should return accounting asset address, not PT/LP address."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        pt_address = "0xPTAddress12345678901234567890123456789012"
        mocker.patch.object(adapter, "_get_pt_address", return_value=pt_address)
        mocker.patch.object(adapter, "_balance_of", side_effect=[1000 * 10**6, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=10**18)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].asset_address == usdc_address
        assert result[0].asset_address != pt_address


# =============================================================================
# Unit Tests - Rate Calculation
# =============================================================================


class TestPendleRateCalculation:
    """Tests for PT/LP to asset rate calculations."""

    @pytest.mark.asyncio
    async def test_pt_rate_calculation_1_to_1(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """1:1 rate should return exact balance."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        pt_balance = 1000 * 10**6  # 1000 PT (6 decimals like USDC)
        pt_rate = 10**18  # 1:1 rate

        mocker.patch.object(
            adapter,
            "_get_pt_address",
            return_value="0xPTAddress12345678901234567890123456789012",
        )
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        # (1000 * 10^6 * 10^18) // 10^18 = 1000 * 10^6
        assert result[0].amount == 1000 * 10**6

    @pytest.mark.asyncio
    async def test_pt_rate_calculation_discounted(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """PT trading at discount (e.g., 0.95) should return discounted value."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        pt_balance = 1000 * 10**6  # 1000 PT (6 decimals)
        pt_rate = 95 * 10**16  # 0.95 rate (5% discount)

        mocker.patch.object(
            adapter,
            "_get_pt_address",
            return_value="0xPTAddress12345678901234567890123456789012",
        )
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        # (1000 * 10^6 * 0.95 * 10^18) // 10^18 = 950 * 10^6
        expected = (pt_balance * pt_rate) // 10**18
        assert result[0].amount == expected
        assert result[0].amount == 950 * 10**6

    @pytest.mark.asyncio
    async def test_lp_rate_calculation(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """LP position should be calculated using LP to asset rate."""
        market_address = "0x1234567890123456789012345678901234567890"
        markets = {
            "test_market": {
                "market": market_address,
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        lp_balance = 500 * 10**6  # 500 LP tokens
        lp_rate = 12 * 10**17  # 1.2 rate (LP worth more than underlying)

        mocker.patch.object(
            adapter,
            "_get_pt_address",
            return_value="0xPTAddress12345678901234567890123456789012",
        )
        # PT balance = 0, LP balance = 500 * 10^6
        mocker.patch.object(adapter, "_balance_of", side_effect=[0, lp_balance])
        mocker.patch.object(adapter, "_get_lp_to_asset_rate", return_value=lp_rate)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        # (500 * 10^6 * 1.2 * 10^18) // 10^18 = 600 * 10^6
        expected = (lp_balance * lp_rate) // 10**18
        assert result[0].amount == expected
        assert result[0].amount == 600 * 10**6

    @pytest.mark.asyncio
    async def test_combined_pt_and_lp_positions(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """Both PT and LP positions should be returned separately."""
        market_address = "0x1234567890123456789012345678901234567890"
        markets = {
            "test_market": {
                "market": market_address,
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        pt_balance = 1000 * 10**6
        lp_balance = 500 * 10**6
        pt_rate = 95 * 10**16  # 0.95
        lp_rate = 12 * 10**17  # 1.2

        mocker.patch.object(
            adapter,
            "_get_pt_address",
            return_value="0xPTAddress12345678901234567890123456789012",
        )
        mocker.patch.object(
            adapter, "_balance_of", side_effect=[pt_balance, lp_balance]
        )
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)
        mocker.patch.object(adapter, "_get_lp_to_asset_rate", return_value=lp_rate)

        result = await adapter.fetch_assets(subvault_address)

        # Both PT and LP positions should be returned
        assert len(result) == 2
        total_amount = sum(r.amount for r in result)
        expected_pt = (pt_balance * pt_rate) // 10**18  # 950 * 10^6
        expected_lp = (lp_balance * lp_rate) // 10**18  # 600 * 10^6
        assert total_amount == expected_pt + expected_lp


# =============================================================================
# Decimal Edge Case Tests
# =============================================================================


class TestPendleDecimalHandling:
    """Tests for correct decimal handling across different token types."""

    @pytest.mark.asyncio
    async def test_6_decimal_token_usdc(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """USDC (6 decimals) PT should calculate correctly."""
        markets = {
            "usdc_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        # 1000 USDC worth of PT (6 decimals)
        pt_balance = 1000 * 10**6
        pt_rate = 10**18  # 1:1

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].amount == 1000 * 10**6
        # Verify it's in 6 decimal representation
        assert result[0].amount == 1_000_000_000  # 1000 USDC

    @pytest.mark.asyncio
    async def test_18_decimal_token_weth(
        self, mocker, mainnet_config, subvault_address, weth_address
    ):
        """WETH (18 decimals) PT should calculate correctly."""
        markets = {
            "weth_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": weth_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        # 10 WETH worth of PT (18 decimals)
        pt_balance = 10 * 10**18
        pt_rate = 10**18  # 1:1

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].amount == 10 * 10**18
        # Verify it's in 18 decimal representation
        assert result[0].amount == 10_000_000_000_000_000_000  # 10 WETH

    @pytest.mark.asyncio
    async def test_18_decimal_token_usde(
        self, mocker, mainnet_config, subvault_address, usde_address
    ):
        """USDe (18 decimals) PT should calculate correctly."""
        markets = {
            "usde_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usde_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        # 5000 USDe worth of PT (18 decimals)
        pt_balance = 5000 * 10**18
        pt_rate = 98 * 10**16  # 0.98 rate

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        expected = (pt_balance * pt_rate) // 10**18
        assert result[0].amount == expected
        # 5000 * 0.98 = 4900 USDe
        assert result[0].amount == 4900 * 10**18

    @pytest.mark.asyncio
    async def test_small_balance_no_underflow(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """Very small balances should not cause underflow."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        # 1 wei of PT
        pt_balance = 1
        pt_rate = 95 * 10**16  # 0.95

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        # Should truncate to 0 (no underflow)
        # (1 * 0.95 * 10^18) // 10^18 = 0
        if result:
            assert result[0].amount >= 0

    @pytest.mark.asyncio
    async def test_large_balance_no_overflow(
        self, mocker, mainnet_config, subvault_address, weth_address
    ):
        """Very large balances should not cause overflow."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": weth_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        # Very large balance (but still reasonable for a vault)
        pt_balance = 10**24  # 1 million ETH worth
        pt_rate = 10**18

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].amount == 10**24

    @pytest.mark.asyncio
    async def test_fractional_rate_precision(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """Fractional rates should maintain precision."""
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": usdc_address,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        pt_balance = 1000 * 10**6  # 1000 USDC
        # Rate of 0.987654321 (high precision)
        pt_rate = 987654321000000000

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(adapter, "_balance_of", side_effect=[pt_balance, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=pt_rate)

        result = await adapter.fetch_assets(subvault_address)

        expected = (pt_balance * pt_rate) // 10**18
        assert result[0].amount == expected
        # Should be approximately 987.654321 USDC
        assert 987 * 10**6 < result[0].amount < 988 * 10**6


# =============================================================================
# Multiple Markets Tests
# =============================================================================


class TestPendleMultipleMarkets:
    """Tests for handling multiple markets."""

    @pytest.mark.asyncio
    async def test_multiple_markets_same_accounting_asset(
        self, mocker, mainnet_config, subvault_address, usdc_address
    ):
        """Multiple markets with same accounting asset should return separate entries."""
        markets = {
            "market1": {
                "market": "0x1111111111111111111111111111111111111111",
                "accounting_asset": usdc_address,
            },
            "market2": {
                "market": "0x2222222222222222222222222222222222222222",
                "accounting_asset": usdc_address,
            },
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        # Market 1: 1000 PT, Market 2: 500 PT
        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(
            adapter,
            "_balance_of",
            side_effect=[
                1000 * 10**6,
                0,  # Market 1: PT, LP
                500 * 10**6,
                0,  # Market 2: PT, LP
            ],
        )
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=10**18)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 2
        total = sum(r.amount for r in result)
        assert total == 1500 * 10**6

    @pytest.mark.asyncio
    async def test_multiple_markets_different_accounting_assets(
        self, mocker, mainnet_config, subvault_address, usdc_address, weth_address
    ):
        """Multiple markets with different accounting assets should return correctly."""
        markets = {
            "usdc_market": {
                "market": "0x1111111111111111111111111111111111111111",
                "accounting_asset": usdc_address,
            },
            "weth_market": {
                "market": "0x2222222222222222222222222222222222222222",
                "accounting_asset": weth_address,
            },
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(
            adapter,
            "_balance_of",
            side_effect=[
                1000 * 10**6,
                0,  # USDC market: PT, LP (6 decimals)
                5 * 10**18,
                0,  # WETH market: PT, LP (18 decimals)
            ],
        )
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=10**18)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 2
        usdc_result = next(r for r in result if r.asset_address == usdc_address)
        weth_result = next(r for r in result if r.asset_address == weth_address)
        assert usdc_result.amount == 1000 * 10**6
        assert weth_result.amount == 5 * 10**18


# =============================================================================
# Address Handling Tests
# =============================================================================


class TestPendleAddressHandling:
    """Tests for address checksumming and normalization."""

    @pytest.mark.asyncio
    async def test_returns_checksummed_addresses(
        self, mocker, mainnet_config, subvault_address
    ):
        """Returned asset addresses should be checksummed."""
        lowercase_usdc = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
        markets = {
            "test_market": {
                "market": "0x1234567890123456789012345678901234567890",
                "accounting_asset": lowercase_usdc,
            }
        }
        adapter = PendleAdapter(mainnet_config, markets=markets)

        mocker.patch.object(adapter, "_get_pt_address", return_value="0xPT")
        mocker.patch.object(adapter, "_balance_of", side_effect=[1000 * 10**6, 0])
        mocker.patch.object(adapter, "_get_pt_to_asset_rate", return_value=10**18)

        result = await adapter.fetch_assets(subvault_address)

        expected_checksummed = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        assert result[0].asset_address == expected_checksummed


# =============================================================================
# fetch_all_assets Tests
# =============================================================================


class TestPendleFetchAllAssets:
    """Tests for fetch_all_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, mainnet_config):
        """fetch_all_assets should return empty list (not supported)."""
        adapter = PendleAdapter(mainnet_config)
        result = await adapter.fetch_all_assets()
        assert result == []


# =============================================================================
# Integration Tests - Real Contracts
# =============================================================================


# Active Pendle market: PT-USDe-5FEB2026
# Market: 0xaadbc004dacf10e1fdbd87ca1a40ecaf77cc5b02
# PT: 0x1f84a51296691320478c98b8d77f2bbd17d34350
# SY: 0x925a15bd6a1582fa7c0ebbfc3dbd29c34f58340e
# Underlying: USDe (18 decimals) - expires Feb 5, 2026
INTEGRATION_MARKET_USDE_FEB2026 = "0xaadbc004dacf10e1fdbd87ca1a40ecaf77cc5b02"
INTEGRATION_PT_USDE_FEB2026 = "0x1f84a51296691320478c98b8d77f2bbd17d34350"
USDE_ADDRESS = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"


class TestPendleIntegration:
    """Integration tests with real Pendle contracts on mainnet.

    These tests require --run-integration flag and a working RPC endpoint.
    Uses the PT-USDe-5FEB2026 market which expires Feb 5, 2026.
    """

    @pytest.fixture
    def integration_config(self):
        """Config for integration tests with real RPC."""
        return OracleSettings(
            vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
            oracle_helper_address="0x000000005F543c38d5ea6D0bF10A50974Eb55E35",
            vault_rpc="https://eth.drpc.org",
            block_number=24220000,  # Recent block (Jan 2026) for reproducibility
            network=Network.MAINNET,
            dry_run=True,
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_oracle_address_is_valid_contract(self, integration_config):
        """Verify Pendle oracle address is a valid contract."""
        from web3 import Web3

        adapter = PendleAdapter(integration_config)

        # Use the adapter's w3 instance and oracle address
        code = adapter.w3.eth.get_code(
            Web3.to_checksum_address(adapter.oracle_address),
            block_identifier=adapter.block_number,
        )
        assert len(code) > 0, "Oracle address should have contract code"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_pt_to_asset_rate_returns_valid_rate(self, integration_config):
        """Integration: getPtToAssetRate should return a valid rate for USDe market."""
        markets = {
            "usde_feb2026": {
                "market": INTEGRATION_MARKET_USDE_FEB2026,
                "accounting_asset": USDE_ADDRESS,
            }
        }

        adapter = PendleAdapter(integration_config, markets=markets)
        rate = await adapter._get_pt_to_asset_rate(INTEGRATION_MARKET_USDE_FEB2026)

        # Rate should be positive and reasonable (between 0.9 and 1.05 for near-maturity PT)
        assert rate > 0, "Rate should be positive"
        assert rate >= 9 * 10**17, f"Rate {rate} should be at least 0.9"
        assert rate <= 105 * 10**16, f"Rate {rate} should be at most 1.05"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_lp_to_asset_rate_returns_valid_rate(self, integration_config):
        """Integration: getLpToAssetRate should return a valid rate for USDe market."""
        markets = {
            "usde_feb2026": {
                "market": INTEGRATION_MARKET_USDE_FEB2026,
                "accounting_asset": USDE_ADDRESS,
            }
        }

        adapter = PendleAdapter(integration_config, markets=markets)
        rate = await adapter._get_lp_to_asset_rate(INTEGRATION_MARKET_USDE_FEB2026)

        # LP rate should be positive and reasonable
        assert rate > 0, "Rate should be positive"
        assert rate >= 5 * 10**17, f"Rate {rate} should be at least 0.5"
        assert rate <= 30 * 10**17, f"Rate {rate} should be at most 3.0"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_pt_address_from_market(self, integration_config):
        """Integration: readTokens should return correct PT address."""
        markets = {
            "usde_feb2026": {
                "market": INTEGRATION_MARKET_USDE_FEB2026,
                "accounting_asset": USDE_ADDRESS,
            }
        }

        adapter = PendleAdapter(integration_config, markets=markets)
        pt_address = await adapter._get_pt_address(INTEGRATION_MARKET_USDE_FEB2026)

        # PT address should match the known PT for this market
        assert pt_address.lower() == INTEGRATION_PT_USDE_FEB2026.lower(), (
            f"Expected PT {INTEGRATION_PT_USDE_FEB2026}, got {pt_address}"
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_balance_of_returns_integer(self, integration_config):
        """Integration: _balance_of should return an integer."""
        # Use a random address - balance will likely be 0 but should return int
        test_address = "0x90c983DC732e65DB6177638f0125914787b8Cb78"

        markets = {
            "usde_feb2026": {
                "market": INTEGRATION_MARKET_USDE_FEB2026,
                "accounting_asset": USDE_ADDRESS,
            }
        }

        adapter = PendleAdapter(integration_config, markets=markets)
        balance = await adapter._balance_of(INTEGRATION_PT_USDE_FEB2026, test_address)

        assert isinstance(balance, int)
        assert balance >= 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_full_fetch_assets_flow(self, integration_config):
        """Integration: Full fetch_assets flow should work end-to-end."""
        test_address = "0x90c983DC732e65DB6177638f0125914787b8Cb78"

        markets = {
            "usde_feb2026": {
                "market": INTEGRATION_MARKET_USDE_FEB2026,
                "accounting_asset": USDE_ADDRESS,
            }
        }

        adapter = PendleAdapter(integration_config, markets=markets)
        result = await adapter.fetch_assets(test_address)

        # Result should be a list (may be empty if address has no positions)
        assert isinstance(result, list)
        for asset_data in result:
            assert isinstance(asset_data, AssetData)
            assert isinstance(asset_data.amount, int)
            assert asset_data.asset_address.startswith("0x")

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_market_returns_usde_as_accounting_asset(self, integration_config):
        """Integration: Verify the market correctly returns USDe as accounting asset."""
        test_address = "0x0000000000000000000000000000000000000001"  # Dummy address

        markets = {
            "usde_feb2026": {
                "market": INTEGRATION_MARKET_USDE_FEB2026,
                "accounting_asset": USDE_ADDRESS,
            }
        }

        adapter = PendleAdapter(integration_config, markets=markets)
        result = await adapter.fetch_assets(test_address)

        # If there are results, they should all be for USDe
        for asset_data in result:
            assert asset_data.asset_address.lower() == USDE_ADDRESS.lower(), (
                f"Expected USDe {USDE_ADDRESS}, got {asset_data.asset_address}"
            )
