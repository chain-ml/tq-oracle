import pytest

from tq_oracle.processors.asset_aggregator import AggregatedAssets
from tq_oracle.adapters.price_adapters.base import PriceData
from tq_oracle.processors.total_assets import calculate_total_assets


def test_calculate_total_assets_basic():
    aggregated = AggregatedAssets(
        assets={
            "0xA": 2,
            "0xB": 3,
        }
    )
    prices = PriceData(
        base_asset="0xA",
        prices={
            "0xA": 10**18,
            "0xB": 2 * 10**18,
        },
        decimals={
            "0xA": 18,
            "0xB": 18,
        },
    )

    result = calculate_total_assets(aggregated, prices)

    assert result == 8


def test_calculate_total_assets_empty_returns_zero():
    aggregated = AggregatedAssets(assets={})
    relative = PriceData(base_asset="", prices={})

    result = calculate_total_assets(aggregated, relative)

    assert result == 0


def test_calculate_total_assets_missing_prices_raises():
    aggregated = AggregatedAssets(
        assets={
            "0xA": 1,
            "0xB": 1,
        }
    )
    prices = PriceData(
        base_asset="0xA",
        prices={
            "0xA": 10**18,
        },
        decimals={
            "0xA": 18,
            "0xB": 18,
        },
    )

    with pytest.raises(ValueError, match=r"Prices missing for assets: \['0xB'\]"):
        calculate_total_assets(aggregated, prices)


def test_calculate_total_assets_multiple_missing_prices_raises():
    aggregated = AggregatedAssets(
        assets={
            "0xA": 1,
            "0xB": 2,
            "0xC": 3,
        }
    )
    prices = PriceData(
        base_asset="0xA",
        prices={
            "0xA": 10**18,
        },
        decimals={
            "0xA": 18,
            "0xB": 18,
            "0xC": 18,
        },
    )

    with pytest.raises(
        ValueError, match=r"Prices missing for assets: \['0xB', '0xC'\]"
    ):
        calculate_total_assets(aggregated, prices)


def test_calculate_total_assets_with_extra_prices():
    aggregated = AggregatedAssets(
        assets={
            "0xA": 2,
            "0xB": 3,
        }
    )
    prices = PriceData(
        base_asset="0xA",
        prices={
            "0xA": 10**18,
            "0xB": 2 * 10**18,
            "0xC": 5 * 10**18,
        },
        decimals={
            "0xA": 18,
            "0xB": 18,
            "0xC": 18,
        },
    )

    result = calculate_total_assets(aggregated, prices)

    assert result == 8


def test_calculate_total_assets_invalid_prices_raises():
    aggregated = AggregatedAssets(
        assets={
            "0xA": 1,
            "0xB": 1,
            "0xC": 1,
        }
    )
    prices = PriceData(
        base_asset="0xA",
        prices={
            "0xA": 10**18,
            "0xB": 0,
            "0xC": -100,
        },
        decimals={
            "0xA": 18,
            "0xB": 18,
            "0xC": 18,
        },
    )

    with pytest.raises(ValueError, match=r"Invalid prices for assets:"):
        calculate_total_assets(aggregated, prices)


@pytest.mark.parametrize(
    "test_name, assets, prices, expected_total",
    [
        (
            "single asset",
            {"0xA": 5},
            {"0xA": 2 * 10**18},
            5 * 2,
        ),
        (
            "asset with zero amount",
            {"0xA": 0, "0xB": 10},
            {"0xA": 500 * 10**18, "0xB": 2 * 10**18},
            20,
        ),
        (
            "all assets have zero amount",
            {"0xA": 0, "0xB": 0},
            {"0xA": 10**18, "0xB": 10**18},
            0,
        ),
        (
            "mixed zero and non-zero amounts",
            {"0xA": 10, "0xB": 0, "0xC": 5},
            {"0xA": 2 * 10**18, "0xB": 100 * 10**18, "0xC": 1 * 10**18},
            25,
        ),
    ],
)
def test_calculate_total_assets_scenarios(test_name, assets, prices, expected_total):
    """
    Tests various valid scenarios including single assets and zero amounts.
    - A single asset should be calculated correctly.
    - An asset with a zero amount should contribute nothing to the total.
    - A mix of zero and non-zero assets should be calculated correctly.
    """
    aggregated = AggregatedAssets(assets=assets)
    decimals = {addr: 18 for addr in prices.keys()}
    price_data = PriceData(base_asset="0xBASE", prices=prices, decimals=decimals)

    result = calculate_total_assets(aggregated, price_data)

    assert result == expected_total


@pytest.mark.parametrize(
    "test_name, amount, price, expected_value",
    [
        (
            "value just below 1 unit truncates to 0",
            1,
            10**18 - 1,
            0,
        ),
        (
            "value exactly 1 unit",
            1,
            10**18,
            1,
        ),
        (
            "value just above 1 unit truncates to 1",
            1,
            10**18 + 1,
            1,
        ),
        (
            "value just below 2 units truncates to 1",
            2,
            10**18 - 1,  # product is 2*10**18 - 2
            1,
        ),
        (
            "smallest non-zero values truncate to 0",
            1,
            1,
            0,
        ),
    ],
)
def test_calculate_total_assets_precision_and_truncation(
    test_name, amount, price, expected_value
):
    """
    Tests the integer division to ensure correct truncation of fractional results.
    The formula `amount * price // 10**decimals` should truncate, not round.
    """
    aggregated = AggregatedAssets(assets={"0xA": amount})
    prices = PriceData(base_asset="0xBASE", prices={"0xA": price}, decimals={"0xA": 18})

    result = calculate_total_assets(aggregated, prices)

    assert result == expected_value


def test_calculate_total_assets_with_large_but_valid_numbers():
    """
    Tests that the calculation handles very large numbers while staying within
    the uint256 range enforced by the OracleHelper contract.
    """
    # Bound amount so the final total (amount * price // 1e18) fits in uint256.
    large_amount = 2**200
    large_price = 5 * 10**18

    aggregated = AggregatedAssets(assets={"0xA": large_amount})
    prices = PriceData(
        base_asset="0xBASE", prices={"0xA": large_price}, decimals={"0xA": 18}
    )

    # (large_amount * 5 * 10**18) // 10**18 = large_amount * 5, still < 2**256
    expected_result = large_amount * 5
    result = calculate_total_assets(aggregated, prices)

    assert result == expected_result
