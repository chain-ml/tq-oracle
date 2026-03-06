import pytest

from tq_oracle.adapters.price_adapters.base import PriceData
from tq_oracle.adapters.price_adapters.eth import ETHAdapter
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
def weth_address(config):
    address = config.assets["WETH"]
    assert address is not None
    return address


@pytest.fixture
def oseth_address(config):
    address = config.assets["OSETH"]
    assert address is not None
    return address


@pytest.mark.asyncio
async def test_adapter_name(config):
    adapter = ETHAdapter(config)
    assert adapter.adapter_name == "eth"


@pytest.mark.asyncio
async def test_fetch_prices_returns_empty_prices_on_unsupported_asset(
    config, eth_address
):
    adapter = ETHAdapter(config)
    unsupported_address = "0xUnsupported"

    result = await adapter.fetch_prices(
        [unsupported_address], PriceData(base_asset=eth_address, prices={})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 0


@pytest.mark.asyncio
async def test_fetch_prices_skips_on_unsupported_base_asset(config):
    adapter = ETHAdapter(config)
    unsupported_address = "0xUnsupported"
    accumulator = PriceData(base_asset=unsupported_address, prices={"0x111": 1})
    result = await adapter.fetch_prices([unsupported_address], accumulator)
    assert result is accumulator
    assert result.prices == {"0x111": 1}


@pytest.mark.asyncio
async def test_fetch_prices_returns_previous_prices_on_unsupported_asset(
    config, eth_address
):
    adapter = ETHAdapter(config)
    unsupported_address = "0xUnsupported"
    result = await adapter.fetch_prices(
        [unsupported_address],
        PriceData(base_asset=eth_address, prices={"0x111": 1}),
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 1
    assert result.prices["0x111"] == 1


@pytest.mark.asyncio
async def test_fetch_prices_eth_returns_one(config, eth_address):
    adapter = ETHAdapter(config)
    result = await adapter.fetch_prices(
        [eth_address], PriceData(base_asset=eth_address, prices={})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 1
    assert result.prices[eth_address] == 10**18


@pytest.mark.asyncio
async def test_fetch_prices_weth_returns_one_to_one(config, eth_address, weth_address):
    adapter = ETHAdapter(config)
    result = await adapter.fetch_prices(
        [weth_address], PriceData(base_asset=eth_address, prices={})
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 1
    assert result.prices[weth_address] == 10**18


@pytest.mark.asyncio
async def test_fetch_prices_all_three_assets(config, eth_address, weth_address):
    adapter = ETHAdapter(config)
    result = await adapter.fetch_prices(
        [eth_address, weth_address],
        PriceData(base_asset=eth_address, prices={}),
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 2
    assert result.prices[eth_address] == 10**18
    assert result.prices[weth_address] == 10**18


@pytest.mark.asyncio
async def test_fetch_prices_preserves_existing_prices(
    config, eth_address, weth_address
):
    adapter = ETHAdapter(config)
    result = await adapter.fetch_prices(
        [weth_address],
        PriceData(base_asset=eth_address, prices={"0x111": 123}),
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 2
    assert result.prices["0x111"] == 123
    assert result.prices[weth_address] == 10**18


@pytest.mark.asyncio
async def test_fetch_prices_oseth_uses_native_value(
    mocker, config, eth_address, oseth_address
):
    adapter = ETHAdapter(config)
    mock_price = 987654321
    mock_get_oseth_price = mocker.patch.object(
        adapter, "_get_oseth_price", return_value=mock_price
    )

    result = await adapter.fetch_prices(
        [oseth_address], PriceData(base_asset=eth_address, prices={})
    )

    mock_get_oseth_price.assert_called_once()
    assert result.prices[oseth_address] == mock_price


@pytest.mark.asyncio
async def test_fetch_prices_oseth_skipped_when_disabled(
    mocker, eth_address, oseth_address
):
    config = OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        additional_asset_support=False,
        dry_run=False,
    )
    adapter = ETHAdapter(config)
    mocker.patch.object(
        adapter,
        "_get_oseth_price",
        side_effect=AssertionError(
            "Should not be called when additional assets disabled"
        ),
    )

    result = await adapter.fetch_prices(
        [oseth_address], PriceData(base_asset=eth_address, prices={})
    )

    assert oseth_address not in result.prices


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_prices_all_assets_integration(config, eth_address, weth_address):
    adapter = ETHAdapter(config)
    result = await adapter.fetch_prices(
        [eth_address, weth_address],
        PriceData(base_asset=eth_address, prices={"0x111": 456}),
    )
    assert isinstance(result, PriceData)
    assert len(result.prices) == 3
    assert result.prices["0x111"] == 456
    assert result.prices[eth_address] == 10**18
    assert result.prices[weth_address] == 10**18
