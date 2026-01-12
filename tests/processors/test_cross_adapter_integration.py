"""Cross-adapter integration tests for multi-protocol scenarios.

This module tests the full flow when multiple adapters (Pendle, Aave, idle_balances)
return assets that need to be aggregated and priced correctly, with special focus
on decimal handling across different token types.

These tests are CRITICAL for ensuring the oracle correctly handles real-world
scenarios where vaults have positions across multiple protocols.
"""

import pytest
from web3 import Web3

from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.adapters.price_adapters.base import PriceData
from tq_oracle.processors.asset_aggregator import (
    AggregatedAssets,
    compute_total_aggregated_assets,
)
from tq_oracle.processors.total_assets import calculate_total_assets


# =============================================================================
# Fixtures - Token Addresses
# =============================================================================


@pytest.fixture
def usdc():
    """USDC address (6 decimals)."""
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def usdt():
    """USDT address (6 decimals)."""
    return "0xdAC17F958D2ee523a2206206994597C13D831ec7"


@pytest.fixture
def usde():
    """USDe address (18 decimals)."""
    return "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"


@pytest.fixture
def weth():
    """WETH address (18 decimals)."""
    return "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


@pytest.fixture
def wsteth():
    """wstETH address (18 decimals)."""
    return "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"


@pytest.fixture
def dai():
    """DAI address (18 decimals)."""
    return "0x6B175474E89094C44Da98b954EedeAC495271d0F"


# =============================================================================
# Cross-Adapter Aggregation Tests
# =============================================================================


class TestCrossAdapterAggregation:
    """Tests for aggregating assets from multiple adapters."""

    @pytest.mark.asyncio
    async def test_pendle_and_aave_same_asset_aggregates(self, usdc):
        """Pendle PT-USDC and Aave aUSDC should aggregate to same underlying."""
        # Simulate: Pendle returns USDC, Aave returns USDC
        pendle_assets = [AssetData(usdc, 1000 * 10**6)]  # 1000 USDC from Pendle
        aave_assets = [AssetData(usdc, 500 * 10**6)]  # 500 USDC from Aave supply

        result = await compute_total_aggregated_assets([pendle_assets, aave_assets])

        assert result.assets[usdc] == 1500 * 10**6

    @pytest.mark.asyncio
    async def test_pendle_and_aave_with_borrow_nets_correctly(self, usdc):
        """Pendle USDC + Aave supply - Aave borrow should net correctly."""
        pendle_assets = [AssetData(usdc, 1000 * 10**6)]  # 1000 USDC from Pendle
        aave_supply = [AssetData(usdc, 500 * 10**6)]  # 500 USDC supplied
        aave_borrow = [AssetData(usdc, -200 * 10**6)]  # 200 USDC borrowed (negative)

        result = await compute_total_aggregated_assets(
            [
                pendle_assets,
                aave_supply,
                aave_borrow,
            ]
        )

        # 1000 + 500 - 200 = 1300 USDC
        assert result.assets[usdc] == 1300 * 10**6

    @pytest.mark.asyncio
    async def test_pendle_weth_aave_usdc_separate_assets(self, usdc, weth):
        """Different assets from different protocols should remain separate."""
        pendle_assets = [AssetData(weth, 10 * 10**18)]  # 10 WETH from Pendle
        aave_assets = [AssetData(usdc, 5000 * 10**6)]  # 5000 USDC from Aave

        result = await compute_total_aggregated_assets([pendle_assets, aave_assets])

        assert result.assets[weth] == 10 * 10**18
        assert result.assets[usdc] == 5000 * 10**6
        assert len(result.assets) == 2

    @pytest.mark.asyncio
    async def test_idle_pendle_aave_all_contributing(self, usdc, weth, wsteth):
        """Three adapters contributing different amounts should all aggregate."""
        # Simulate real scenario:
        # - idle_balances: has some USDC sitting in vault
        # - Pendle: PT-USDC position
        # - Aave: wstETH supplied, WETH borrowed

        idle_assets = [
            AssetData(usdc, 100 * 10**6),  # 100 USDC idle
            AssetData(weth, 1 * 10**18),  # 1 WETH idle
        ]

        pendle_assets = [
            AssetData(usdc, 900 * 10**6),  # 900 USDC from PT
        ]

        aave_assets = [
            AssetData(wsteth, 50 * 10**18),  # 50 wstETH supplied
            AssetData(weth, -20 * 10**18),  # 20 WETH borrowed
        ]

        result = await compute_total_aggregated_assets(
            [
                idle_assets,
                pendle_assets,
                aave_assets,
            ]
        )

        assert result.assets[usdc] == 1000 * 10**6  # 100 + 900
        assert result.assets[weth] == -19 * 10**18  # 1 - 20
        assert result.assets[wsteth] == 50 * 10**18
        assert len(result.assets) == 3

    @pytest.mark.asyncio
    async def test_multiple_pendle_markets_aggregate(self, usdc, usde):
        """Multiple Pendle markets with different accounting assets."""
        # Two Pendle markets: one USDC-based, one USDe-based
        pendle_usdc_market = [AssetData(usdc, 1000 * 10**6)]
        pendle_usde_market = [AssetData(usde, 2000 * 10**18)]

        result = await compute_total_aggregated_assets(
            [
                pendle_usdc_market,
                pendle_usde_market,
            ]
        )

        assert result.assets[usdc] == 1000 * 10**6
        assert result.assets[usde] == 2000 * 10**18

    @pytest.mark.asyncio
    async def test_address_case_normalization_across_adapters(self, usdc):
        """Different case addresses from different adapters should aggregate."""
        # Simulating adapters returning addresses in different formats
        adapter1 = [AssetData(usdc.lower(), 1000 * 10**6)]
        adapter2 = [AssetData(usdc.upper(), 500 * 10**6)]
        adapter3 = [AssetData(usdc, 300 * 10**6)]

        result = await compute_total_aggregated_assets([adapter1, adapter2, adapter3])

        checksummed = Web3.to_checksum_address(usdc)
        assert result.assets[checksummed] == 1800 * 10**6
        assert len(result.assets) == 1


# =============================================================================
# Mixed Decimal TVL Calculation Tests
# =============================================================================


class TestMixedDecimalTVL:
    """Tests for TVL calculation with mixed decimal tokens."""

    def test_6_decimal_tokens_tvl(self, usdc, usdt):
        """TVL calculation with 6-decimal stablecoins."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 1000 * 10**6,  # 1000 USDC
                usdt: 500 * 10**6,  # 500 USDT
            }
        )

        # Prices in ETH (18 decimals): assume $3000/ETH
        # 1 USDC = 0.000333... ETH
        usdc_price = 333333333333333  # ~0.000333 ETH
        usdt_price = 333333333333333

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: usdc_price, usdt: usdt_price},
            decimals={usdc: 6, usdt: 6},
        )

        total = calculate_total_assets(aggregated, prices)

        # 1000 USDC * price / 10^6 + 500 USDT * price / 10^6
        expected_usdc = (1000 * 10**6 * usdc_price) // 10**6
        expected_usdt = (500 * 10**6 * usdt_price) // 10**6
        assert total == expected_usdc + expected_usdt

    def test_18_decimal_tokens_tvl(self, weth, wsteth):
        """TVL calculation with 18-decimal tokens."""
        aggregated = AggregatedAssets(
            assets={
                weth: 10 * 10**18,  # 10 WETH
                wsteth: 5 * 10**18,  # 5 wstETH
            }
        )

        # WETH = 1 ETH, wstETH = 1.1 ETH
        prices = PriceData(
            base_asset="ETH",
            prices={
                weth: 10**18,  # 1 ETH per WETH
                wsteth: 11 * 10**17,  # 1.1 ETH per wstETH
            },
            decimals={weth: 18, wsteth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        # 10 WETH * 1 + 5 wstETH * 1.1 = 10 + 5.5 = 15.5 ETH
        expected_weth = (10 * 10**18 * 10**18) // 10**18  # 10 ETH
        expected_wsteth = (5 * 10**18 * 11 * 10**17) // 10**18  # 5.5 ETH
        assert total == expected_weth + expected_wsteth
        assert total == 155 * 10**17  # 15.5 ETH

    def test_mixed_6_and_18_decimal_tokens_tvl(self, usdc, weth):
        """TVL calculation mixing 6 and 18 decimal tokens."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 3000 * 10**6,  # 3000 USDC (~1 ETH worth at $3000)
                weth: 2 * 10**18,  # 2 WETH
            }
        )

        # Prices: USDC = 0.000333 ETH, WETH = 1 ETH
        usdc_price = 333333333333333
        weth_price = 10**18

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: usdc_price, weth: weth_price},
            decimals={usdc: 6, weth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        # 3000 USDC -> ~1 ETH, 2 WETH -> 2 ETH = ~3 ETH total
        expected_usdc = (3000 * 10**6 * usdc_price) // 10**6
        expected_weth = (2 * 10**18 * weth_price) // 10**18
        assert total == expected_usdc + expected_weth

        # Verify it's approximately 3 ETH
        assert 29 * 10**17 < total < 31 * 10**17  # Between 2.9 and 3.1 ETH

    def test_negative_amounts_from_borrows(self, usdc, weth):
        """TVL with negative amounts (borrows) should subtract correctly."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 5000 * 10**6,  # 5000 USDC supplied
                weth: -1 * 10**18,  # 1 WETH borrowed (negative)
            }
        )

        usdc_price = 333333333333333  # ~0.000333 ETH
        weth_price = 10**18  # 1 ETH

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: usdc_price, weth: weth_price},
            decimals={usdc: 6, weth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        # 5000 USDC (~1.67 ETH) - 1 WETH = ~0.67 ETH
        expected_usdc = (5000 * 10**6 * usdc_price) // 10**6
        expected_weth = (-1 * 10**18 * weth_price) // 10**18
        expected = expected_usdc + expected_weth

        assert total == expected
        assert total > 0  # Should still be positive (collateral > debt)

    def test_net_negative_position_allowed(self, usdc, weth):
        """TVL can be negative if borrows exceed supplies."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 1000 * 10**6,  # 1000 USDC supplied (~0.33 ETH)
                weth: -2 * 10**18,  # 2 WETH borrowed
            }
        )

        usdc_price = 333333333333333
        weth_price = 10**18

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: usdc_price, weth: weth_price},
            decimals={usdc: 6, weth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        # 0.33 ETH - 2 ETH = -1.67 ETH (underwater position)
        assert total < 0

    def test_all_common_decimal_variations(self, usdc, usdt, dai, weth, wsteth, usde):
        """Comprehensive test with all common decimal variations."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 1000 * 10**6,  # 6 decimals
                usdt: 2000 * 10**6,  # 6 decimals
                dai: 3000 * 10**18,  # 18 decimals
                weth: 1 * 10**18,  # 18 decimals
                wsteth: 2 * 10**18,  # 18 decimals
                usde: 4000 * 10**18,  # 18 decimals
            }
        )

        # Prices (ETH @ $3000)
        stablecoin_price = 333333333333333  # ~0.000333 ETH per USD
        prices = PriceData(
            base_asset="ETH",
            prices={
                usdc: stablecoin_price,
                usdt: stablecoin_price,
                dai: stablecoin_price,
                weth: 10**18,
                wsteth: 115 * 10**16,  # 1.15 ETH per wstETH
                usde: stablecoin_price,
            },
            decimals={
                usdc: 6,
                usdt: 6,
                dai: 18,
                weth: 18,
                wsteth: 18,
                usde: 18,
            },
        )

        total = calculate_total_assets(aggregated, prices)

        # Verify total is positive and reasonable
        assert total > 0

        # Calculate expected manually:
        # USDC: 1000 * ~0.000333 = ~0.333 ETH
        # USDT: 2000 * ~0.000333 = ~0.666 ETH
        # DAI: 3000 * ~0.000333 = ~1 ETH
        # WETH: 1 ETH
        # wstETH: 2 * 1.15 = 2.3 ETH
        # USDe: 4000 * ~0.000333 = ~1.333 ETH
        # Total: ~6.63 ETH

        assert 6 * 10**18 < total < 7 * 10**18

    def test_extreme_price_difference(self, usdc, weth):
        """Handle extreme price differences between assets."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 1 * 10**6,  # 1 USDC (tiny)
                weth: 1000 * 10**18,  # 1000 WETH (huge)
            }
        )

        usdc_price = 333333333333333
        weth_price = 10**18

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: usdc_price, weth: weth_price},
            decimals={usdc: 6, weth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        # 1 USDC is negligible, 1000 WETH dominates
        expected_weth = 1000 * 10**18
        assert total > expected_weth * 99 // 100  # At least 99% from WETH


# =============================================================================
# Real-World Scenario Tests
# =============================================================================


class TestRealWorldScenarios:
    """Tests simulating real-world multi-protocol vault scenarios."""

    @pytest.mark.asyncio
    async def test_leveraged_stablecoin_vault(self, usdc, usdt, weth):
        """Simulate a leveraged stablecoin vault using Aave."""
        # Scenario: Vault supplies stablecoins, borrows ETH
        # Strategy: yield farming with borrowed ETH

        idle_balances = [
            AssetData(usdc, 50 * 10**6),  # 50 USDC buffer
        ]

        aave_positions = [
            AssetData(usdc, 10000 * 10**6),  # 10,000 USDC supplied
            AssetData(usdt, 5000 * 10**6),  # 5,000 USDT supplied
            AssetData(weth, -3 * 10**18),  # 3 ETH borrowed
        ]

        aggregated = await compute_total_aggregated_assets(
            [
                idle_balances,
                aave_positions,
            ]
        )

        # Verify aggregation
        assert aggregated.assets[usdc] == 10050 * 10**6
        assert aggregated.assets[usdt] == 5000 * 10**6
        assert aggregated.assets[weth] == -3 * 10**18

        # Calculate TVL
        usdc_price = 333333333333333
        usdt_price = 333333333333333
        weth_price = 10**18

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: usdc_price, usdt: usdt_price, weth: weth_price},
            decimals={usdc: 6, usdt: 6, weth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        # ~10050 USDC (~3.35 ETH) + 5000 USDT (~1.67 ETH) - 3 ETH = ~2 ETH net
        assert total > 0
        assert 15 * 10**17 < total < 25 * 10**17  # Between 1.5 and 2.5 ETH

    @pytest.mark.asyncio
    async def test_pendle_yield_strategy_vault(self, usdc, usde, weth):
        """Simulate a Pendle yield strategy vault."""
        # Scenario: Vault holds PT tokens for fixed yield

        idle_balances = [
            AssetData(usdc, 100 * 10**6),  # 100 USDC buffer
        ]

        # Pendle PT positions (converted to accounting assets)
        pendle_pt_usdc = [
            AssetData(usdc, 4750 * 10**6),  # 5000 PT-USDC at 0.95 rate = 4750 USDC
        ]

        pendle_pt_usde = [
            AssetData(usde, 9800 * 10**18),  # 10000 PT-USDe at 0.98 rate = 9800 USDe
        ]

        aggregated = await compute_total_aggregated_assets(
            [
                idle_balances,
                pendle_pt_usdc,
                pendle_pt_usde,
            ]
        )

        assert aggregated.assets[usdc] == 4850 * 10**6
        assert aggregated.assets[usde] == 9800 * 10**18

    @pytest.mark.asyncio
    async def test_complex_multi_protocol_vault(self, usdc, usdt, weth, wsteth, usde):
        """Simulate a complex vault with positions across multiple protocols."""
        # Scenario: Diversified DeFi vault
        # - Idle: USDC buffer
        # - Aave: wstETH supplied, USDC borrowed
        # - Pendle: PT-USDe positions

        idle_balances = [
            AssetData(usdc, 500 * 10**6),
            AssetData(weth, 5 * 10**17),  # 0.5 WETH buffer
        ]

        aave_positions = [
            AssetData(wsteth, 100 * 10**18),  # 100 wstETH supplied
            AssetData(usdc, -50000 * 10**6),  # 50,000 USDC borrowed
        ]

        pendle_positions = [
            AssetData(usde, 25000 * 10**18),  # PT-USDe position
        ]

        # Additional subvault with different strategy
        subvault_2 = [
            AssetData(usdt, 10000 * 10**6),  # USDT in another strategy
        ]

        aggregated = await compute_total_aggregated_assets(
            [
                idle_balances,
                aave_positions,
                pendle_positions,
                subvault_2,
            ]
        )

        # Verify all assets tracked
        assert usdc in aggregated.assets
        assert usdt in aggregated.assets
        assert weth in aggregated.assets
        assert wsteth in aggregated.assets
        assert usde in aggregated.assets

        # Verify USDC nets correctly (500 idle - 50000 borrowed = -49500)
        assert aggregated.assets[usdc] == -49500 * 10**6

        # Calculate TVL
        stablecoin_price = 333333333333333
        prices = PriceData(
            base_asset="ETH",
            prices={
                usdc: stablecoin_price,
                usdt: stablecoin_price,
                weth: 10**18,
                wsteth: 115 * 10**16,
                usde: stablecoin_price,
            },
            decimals={
                usdc: 6,
                usdt: 6,
                weth: 18,
                wsteth: 18,
                usde: 18,
            },
        )

        total = calculate_total_assets(aggregated, prices)

        # wstETH dominates: 100 * 1.15 = 115 ETH
        # Stable positions add/subtract small amounts
        # Total should be significantly positive
        assert total > 0

    @pytest.mark.asyncio
    async def test_zero_balance_filtering(self, usdc, weth, wsteth):
        """Verify zero balances from adapters are handled correctly."""
        # Some adapters may return zero balances
        adapter1 = [
            AssetData(usdc, 1000 * 10**6),
            AssetData(weth, 0),  # Zero balance
        ]

        adapter2 = [
            AssetData(wsteth, 5 * 10**18),
            AssetData(usdc, 0),  # Zero balance
        ]

        aggregated = await compute_total_aggregated_assets([adapter1, adapter2])

        # Zero balances should still be in aggregation (0 + 0 = 0)
        # But they don't add to TVL
        assert usdc in aggregated.assets
        assert weth in aggregated.assets
        assert wsteth in aggregated.assets

        # USDC should only count the non-zero
        assert aggregated.assets[usdc] == 1000 * 10**6
        assert aggregated.assets[weth] == 0
        assert aggregated.assets[wsteth] == 5 * 10**18


# =============================================================================
# Edge Cases and Boundary Tests
# =============================================================================


class TestEdgeCasesAndBoundaries:
    """Edge cases that could cause issues in production."""

    def test_single_wei_amounts(self, usdc, weth):
        """Single wei amounts should not cause underflow."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 1,  # 1 wei of USDC
                weth: 1,  # 1 wei of WETH
            }
        )

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: 333333333333333, weth: 10**18},
            decimals={usdc: 6, weth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        # Should not raise, result truncates to small value
        assert total >= 0

    def test_maximum_realistic_amounts(self, weth):
        """Maximum realistic vault amounts should not overflow."""
        # 1 million ETH is extreme but tests boundaries
        aggregated = AggregatedAssets(
            assets={
                weth: 10**6 * 10**18,  # 1 million WETH
            }
        )

        prices = PriceData(
            base_asset="ETH",
            prices={weth: 10**18},
            decimals={weth: 18},
        )

        total = calculate_total_assets(aggregated, prices)

        assert total == 10**6 * 10**18

    def test_high_precision_prices(self, usdc):
        """High precision price values should maintain accuracy."""
        aggregated = AggregatedAssets(
            assets={
                usdc: 1000000 * 10**6,  # 1 million USDC
            }
        )

        # Very precise price: 0.000333333333333333
        precise_price = 333333333333333

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: precise_price},
            decimals={usdc: 6},
        )

        total = calculate_total_assets(aggregated, prices)

        # 1M USDC * 0.000333... ETH = ~333.33 ETH
        expected = (1000000 * 10**6 * precise_price) // 10**6
        assert total == expected

    def test_rounding_accumulation(self, usdc):
        """Multiple small amounts should accumulate without losing precision."""
        # Simulate many small positions
        small_amount = 1 * 10**6  # 1 USDC

        aggregated = AggregatedAssets(
            assets={
                usdc: small_amount * 1000,  # 1000 separate 1 USDC positions aggregated
            }
        )

        prices = PriceData(
            base_asset="ETH",
            prices={usdc: 333333333333333},
            decimals={usdc: 6},
        )

        total = calculate_total_assets(aggregated, prices)

        # Should be ~0.333 ETH (1000 USDC at $3000/ETH)
        assert 3 * 10**17 < total < 4 * 10**17

    @pytest.mark.asyncio
    async def test_empty_adapter_results(self, usdc):
        """Empty results from adapters should not affect aggregation."""
        real_assets = [AssetData(usdc, 1000 * 10**6)]
        empty_assets_1 = []
        empty_assets_2 = []

        aggregated = await compute_total_aggregated_assets(
            [
                real_assets,
                empty_assets_1,
                empty_assets_2,
            ]
        )

        assert aggregated.assets[usdc] == 1000 * 10**6
