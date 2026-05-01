import pytest

from decimal import Decimal
from tq_oracle.adapters.price_adapters.base import PriceData
from tq_oracle.adapters.price_adapters.cow_swap import CowSwapAdapter
from tq_oracle.settings import OracleSettings
from tq_oracle.settings import Network


@pytest.fixture
def config():
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        safe_address=None,
        dry_run=False,
        private_key=None,
        safe_txn_srvc_api_key=None,
    )


@pytest.fixture
def eth_address(config):
    address = config.assets["ETH"]
    assert address is not None
    return address


@pytest.fixture
def usdc_address(config):
    address = config.assets["USDC"]
    assert address is not None
    return address


@pytest.fixture
def usdt_address(config):
    address = config.assets["USDT"]
    assert address is not None
    return address


@pytest.fixture
def usds_address(config):
    address = config.assets["USDS"]
    assert address is not None
    return address


@pytest.fixture
def oseth_address(config):
    address = config.assets["OSETH"]
    assert address is not None
    return address


@pytest.mark.asyncio
async def test_fetch_prices_returns_empty_prices_on_unsupported_asset(
    config, eth_address
):
    adapter = CowSwapAdapter(config)
    unsupported_address = "0xUnsupported"

    result = await adapter.fetch_prices(
        [unsupported_address], PriceData(base_asset=eth_address, prices={})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 0


@pytest.mark.asyncio
async def test_fetch_prices_skips_on_unsupported_base_asset(config, eth_address):
    adapter = CowSwapAdapter(config)
    unsupported_address = "0xUnsupported"
    accumulator = PriceData(base_asset=unsupported_address, prices={"0x111": 1})
    result = await adapter.fetch_prices([unsupported_address], accumulator)
    assert result is accumulator
    assert result.prices == {"0x111": 1}


def test_oseth_is_skipped(config, oseth_address):
    adapter = CowSwapAdapter(config)
    assert oseth_address.lower() in adapter.skipped_assets


@pytest.mark.asyncio
async def test_fetch_prices_returns_previous_prices_on_unsupported_asset(
    config, eth_address
):
    adapter = CowSwapAdapter(config)
    unsupported_address = "0xUnsupported"
    result = await adapter.fetch_prices(
        [unsupported_address], PriceData(base_asset=eth_address, prices={"0x111": 1})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 1
    assert result.prices["0x111"] == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_prices_usdc_integration_with_previous_prices(
    config, eth_address, usdc_address
):
    adapter = CowSwapAdapter(config)
    result = await adapter.fetch_prices(
        [usdc_address], PriceData(base_asset=eth_address, prices={"0x111": 1})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 2
    assert result.prices["0x111"] == 1
    price = result.prices[usdc_address]
    assert isinstance(price, int)
    assert price >= 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_prices_usdt_integration_with_previous_prices(
    config, eth_address, usdt_address
):
    adapter = CowSwapAdapter(config)
    result = await adapter.fetch_prices(
        [usdt_address], PriceData(base_asset=eth_address, prices={"0x111": 1})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 2
    assert result.prices["0x111"] == 1
    price = result.prices[usdt_address]
    assert isinstance(price, int)
    assert price >= 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_prices_usdc_and_usdt_integration(
    config, eth_address, usdc_address, usdt_address
):
    adapter = CowSwapAdapter(config)
    result = await adapter.fetch_prices(
        [usdc_address, usdt_address], PriceData(base_asset=eth_address, prices={})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 2
    usdc_price = result.prices[usdc_address]
    usdt_price = result.prices[usdt_address]
    assert isinstance(usdc_price, int)
    assert isinstance(usdt_price, int)
    assert usdc_price >= 0
    assert usdt_price >= 0


@pytest.mark.asyncio
async def test_fetch_prices_usdt_not_supported_on_testnet(eth_address, usdt_address):
    testnet_config = OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://sepolia.drpc.org",
        block_number=9522842,
        network=Network.SEPOLIA,
        safe_address=None,
        dry_run=False,
        private_key=None,
        safe_txn_srvc_api_key=None,
    )
    adapter = CowSwapAdapter(testnet_config)
    result = await adapter.fetch_prices(
        [usdt_address], PriceData(base_asset=eth_address, prices={})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_prices_usds_integration_with_previous_prices(
    config, eth_address, usds_address
):
    adapter = CowSwapAdapter(config)
    result = await adapter.fetch_prices(
        [usds_address], PriceData(base_asset=eth_address, prices={"0x111": 1})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 2
    assert result.prices["0x111"] == 1
    price = result.prices[usds_address]
    assert isinstance(price, int)
    assert price >= 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_prices_all_stablecoins_integration(
    config, eth_address, usdc_address, usdt_address, usds_address
):
    adapter = CowSwapAdapter(config)
    result = await adapter.fetch_prices(
        [usdc_address, usdt_address, usds_address],
        PriceData(base_asset=eth_address, prices={}),
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 3
    usdc_price = result.prices[usdc_address]
    usdt_price = result.prices[usdt_address]
    usds_price = result.prices[usds_address]
    assert isinstance(usdc_price, int)
    assert isinstance(usdt_price, int)
    assert isinstance(usds_price, int)
    assert usdc_price >= 0
    assert usdt_price >= 0
    assert usds_price >= 0


@pytest.mark.asyncio
async def test_fetch_prices_usds_not_supported_on_testnet(eth_address, usds_address):
    testnet_config = OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://sepolia.drpc.org",
        block_number=9522842,
        network=Network.SEPOLIA,
        safe_address=None,
        dry_run=False,
        private_key=None,
        safe_txn_srvc_api_key=None,
    )
    adapter = CowSwapAdapter(testnet_config)
    result = await adapter.fetch_prices(
        [usds_address], PriceData(base_asset=eth_address, prices={})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 0


@pytest.mark.asyncio
async def test_fetch_prices_skips_on_unsupported_network(eth_address):
    """CowSwap should gracefully skip on unsupported networks (e.g., Monad)."""
    monad_config = OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://rpc.monad.xyz",
        block_number=1000000,
        network=Network.MONAD,
        safe_address=None,
        dry_run=False,
        private_key=None,
        safe_txn_srvc_api_key=None,
    )
    adapter = CowSwapAdapter(monad_config)
    assert adapter._skip is True
    result = await adapter.fetch_prices(
        ["0xSomeToken"],
        PriceData(base_asset=eth_address, prices={"0x111": 42}),
    )
    # Should return accumulator unchanged
    assert result.prices == {"0x111": 42}


@pytest.mark.asyncio
async def test_fetch_prices_preserves_precision(mocker, config, eth_address):
    adapter = CowSwapAdapter(config)
    test_asset_address = "0xTestAsset"
    high_precision_price_str = "12345.123456789123456789"  # More than float precision

    mocker.patch.object(
        adapter,
        "fetch_native_price",
        return_value=high_precision_price_str,
    )
    mocker.patch.object(adapter, "get_token_decimals", return_value=18)

    prices_accumulator = PriceData(base_asset=eth_address, prices={})
    result = await adapter.fetch_prices([test_asset_address], prices_accumulator)

    expected_price_wei = int(Decimal(high_precision_price_str) * 10**18)
    # For a token with 18 decimals, price_wei_normalized should be the same as price_wei
    expected_price_wei_normalized = expected_price_wei // (10 ** (18 - 18))

    assert test_asset_address in result.prices
    assert result.prices[test_asset_address] == expected_price_wei_normalized
    assert isinstance(result.prices[test_asset_address], int)


@pytest.mark.asyncio
async def test_fetch_prices_skips_oseth(mocker, config, eth_address, oseth_address):
    adapter = CowSwapAdapter(config)
    mocker.patch.object(
        adapter,
        "fetch_native_price",
        side_effect=AssertionError("osETH should not be priced via CowSwap"),
    )
    mocker.patch.object(
        adapter,
        "get_token_decimals",
        side_effect=AssertionError("osETH decimals should not be fetched via CowSwap"),
    )

    prices_accumulator = PriceData(base_asset=eth_address, prices={})
    result = await adapter.fetch_prices([oseth_address], prices_accumulator)

    assert oseth_address not in result.prices
