"""Comprehensive tests for ChainlinkAdapter."""

import pytest
from unittest.mock import MagicMock

from tq_oracle.adapters.price_adapters.base import PriceData
from tq_oracle.adapters.price_adapters.chainlink import ChainlinkAdapter
from tq_oracle.settings import Network, OracleSettings


@pytest.fixture
def config():
    """Base config with Chainlink enabled and stablecoins configured."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        chainlink_enabled=True,
        chainlink_stablecoins=[
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
            "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
        ],
    )


@pytest.fixture
def config_disabled():
    """Config with Chainlink disabled."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        chainlink_enabled=False,
    )


@pytest.fixture
def config_no_stablecoins():
    """Config with Chainlink enabled but no stablecoins."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        chainlink_enabled=True,
        chainlink_stablecoins=[],
    )


@pytest.fixture
def eth_address(config):
    return config.assets["ETH"]


@pytest.fixture
def usdc_address():
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def usdt_address():
    return "0xdAC17F958D2ee523a2206206994597C13D831ec7"


class TestChainlinkAdapterInit:
    """Tests for ChainlinkAdapter initialization."""

    def test_adapter_name(self, config):
        adapter = ChainlinkAdapter(config)
        assert adapter.adapter_name == "chainlink"

    def test_disabled_adapter_sets_skip_flag(self, config_disabled):
        adapter = ChainlinkAdapter(config_disabled)
        assert adapter._skip is True

    def test_no_stablecoins_sets_skip_flag(self, config_no_stablecoins):
        adapter = ChainlinkAdapter(config_no_stablecoins)
        assert adapter._skip is True

    def test_enabled_adapter_not_skipped(self, config):
        adapter = ChainlinkAdapter(config)
        assert adapter._skip is False

    def test_stablecoins_lowercased(self, config):
        adapter = ChainlinkAdapter(config)
        for addr in adapter.stablecoins:
            assert addr == addr.lower()

    def test_uses_default_feed_for_mainnet(self, config):
        adapter = ChainlinkAdapter(config)
        assert adapter.eth_usd_feed == "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"

    def test_uses_custom_feed_when_provided(self):
        custom_feed = "0xCustomFeed"
        config = OracleSettings(
            vault_address="0xVault",
            oracle_helper_address="0xOracleHelper",
            vault_rpc="https://eth.drpc.org",
            block_number=23690139,
            network=Network.MAINNET,
            dry_run=False,
            chainlink_enabled=True,
            chainlink_eth_usd_feed=custom_feed,
            chainlink_stablecoins=["0xUSDC"],
        )
        adapter = ChainlinkAdapter(config)
        assert adapter.eth_usd_feed == custom_feed

    def test_sepolia_default_feed(self):
        config = OracleSettings(
            vault_address="0xVault",
            oracle_helper_address="0xOracleHelper",
            vault_rpc="https://sepolia.drpc.org",
            block_number=9522842,
            network=Network.SEPOLIA,
            dry_run=False,
            chainlink_enabled=True,
            chainlink_stablecoins=["0xUSDC"],
        )
        adapter = ChainlinkAdapter(config)
        assert adapter.eth_usd_feed == "0x694AA1769357215DE4FAC081bf1f309aDC325306"


class TestEthUsdConversion:
    """Tests for ETH/USD to USD/ETH conversion logic."""

    def test_convert_typical_price(self, config):
        """Test conversion with typical ETH price of $3000."""
        adapter = ChainlinkAdapter(config)
        # ETH = $3000 with 8 decimals = 300000000000
        eth_usd_price = 3000 * 10**8
        feed_decimals = 8

        usd_in_eth = adapter._convert_eth_usd_to_usd_eth(eth_usd_price, feed_decimals)

        # Expected: (10^18 * 10^8) / (3000 * 10^8) = 10^18 / 3000 = 333333333333333
        expected = (10**18 * 10**8) // eth_usd_price
        assert usd_in_eth == expected
        # Approximately 0.000333 ETH per USD
        assert 333000000000000 < usd_in_eth < 334000000000000

    def test_convert_high_price(self, config):
        """Test conversion with high ETH price of $10000."""
        adapter = ChainlinkAdapter(config)
        eth_usd_price = 10000 * 10**8
        feed_decimals = 8

        usd_in_eth = adapter._convert_eth_usd_to_usd_eth(eth_usd_price, feed_decimals)

        # Expected: 10^18 / 10000 = 10^14 = 100000000000000
        expected = (10**18 * 10**8) // eth_usd_price
        assert usd_in_eth == expected
        assert usd_in_eth == 100000000000000

    def test_convert_low_price(self, config):
        """Test conversion with low ETH price of $100."""
        adapter = ChainlinkAdapter(config)
        eth_usd_price = 100 * 10**8
        feed_decimals = 8

        usd_in_eth = adapter._convert_eth_usd_to_usd_eth(eth_usd_price, feed_decimals)

        # Expected: 10^18 / 100 = 10^16 = 10000000000000000
        expected = (10**18 * 10**8) // eth_usd_price
        assert usd_in_eth == expected
        assert usd_in_eth == 10000000000000000

    def test_convert_with_fractional_price(self, config):
        """Test conversion with fractional ETH price $2567.89."""
        adapter = ChainlinkAdapter(config)
        # $2567.89 with 8 decimals = 256789000000
        eth_usd_price = 256789000000
        feed_decimals = 8

        usd_in_eth = adapter._convert_eth_usd_to_usd_eth(eth_usd_price, feed_decimals)

        expected = (10**18 * 10**8) // eth_usd_price
        assert usd_in_eth == expected

    def test_convert_different_decimals(self, config):
        """Test conversion with different feed decimals (e.g., 18)."""
        adapter = ChainlinkAdapter(config)
        # Price with 18 decimals
        eth_usd_price = 3000 * 10**18
        feed_decimals = 18

        usd_in_eth = adapter._convert_eth_usd_to_usd_eth(eth_usd_price, feed_decimals)

        expected = (10**18 * 10**18) // eth_usd_price
        assert usd_in_eth == expected

    def test_integer_division_truncation(self, config):
        """Test that integer division causes truncation (potential precision loss)."""
        adapter = ChainlinkAdapter(config)
        # Use a price that doesn't divide evenly
        eth_usd_price = 2999 * 10**8 + 99999999  # ~$2999.99999999
        feed_decimals = 8

        usd_in_eth = adapter._convert_eth_usd_to_usd_eth(eth_usd_price, feed_decimals)

        # Result should be truncated (integer division)
        assert isinstance(usd_in_eth, int)


class TestFetchPrices:
    """Tests for fetch_prices method."""

    @pytest.mark.asyncio
    async def test_skipped_adapter_returns_unchanged(
        self, config_disabled, eth_address
    ):
        """Disabled adapter should return accumulator unchanged."""
        adapter = ChainlinkAdapter(config_disabled)
        accumulator = PriceData(base_asset=eth_address, prices={"0x111": 100})

        result = await adapter.fetch_prices(["0xUSDC"], accumulator)

        assert result.prices == {"0x111": 100}

    @pytest.mark.asyncio
    async def test_raises_on_wrong_base_asset(self, config):
        """Should raise ValueError if base_asset is not ETH."""
        adapter = ChainlinkAdapter(config)
        wrong_base = "0xWrongBase"

        with pytest.raises(ValueError, match="only supports ETH as base asset"):
            await adapter.fetch_prices(
                ["0xUSDC"],
                PriceData(base_asset=wrong_base, prices={}),
            )

    @pytest.mark.asyncio
    async def test_prices_stablecoins(self, mocker, config, eth_address, usdc_address):
        """Test that stablecoins get priced correctly."""
        adapter = ChainlinkAdapter(config)

        # Mock the Chainlink price fetch
        mock_eth_usd_price = (3000 * 10**8, 8)  # $3000, 8 decimals
        mocker.patch.object(
            adapter, "_get_eth_usd_price", return_value=mock_eth_usd_price
        )

        # Mock token decimals
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert usdc_address in result.prices
        # Price should be USD/ETH conversion
        expected_price = (10**18 * 10**8) // (3000 * 10**8)
        assert result.prices[usdc_address] == expected_price
        assert result.decimals[usdc_address] == 6

    @pytest.mark.asyncio
    async def test_skips_non_configured_stablecoins(self, mocker, config, eth_address):
        """Assets not in chainlink_stablecoins should not be priced."""
        adapter = ChainlinkAdapter(config)

        mock_eth_usd_price = (3000 * 10**8, 8)
        mocker.patch.object(
            adapter, "_get_eth_usd_price", return_value=mock_eth_usd_price
        )

        non_stablecoin = "0xNonStablecoin"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([non_stablecoin], accumulator)

        assert non_stablecoin not in result.prices

    @pytest.mark.asyncio
    async def test_preserves_existing_prices(
        self, mocker, config, eth_address, usdc_address
    ):
        """Should preserve prices already in accumulator."""
        adapter = ChainlinkAdapter(config)

        mock_eth_usd_price = (3000 * 10**8, 8)
        mocker.patch.object(
            adapter, "_get_eth_usd_price", return_value=mock_eth_usd_price
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        existing_prices = {"0x111": 999}
        accumulator = PriceData(base_asset=eth_address, prices=existing_prices.copy())
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert result.prices["0x111"] == 999
        assert usdc_address in result.prices

    @pytest.mark.asyncio
    async def test_handles_fetch_failure_gracefully(
        self, mocker, config, eth_address, usdc_address
    ):
        """Should return unchanged accumulator on fetch failure."""
        adapter = ChainlinkAdapter(config)

        mocker.patch.object(
            adapter, "_get_eth_usd_price", side_effect=Exception("RPC error")
        )

        accumulator = PriceData(base_asset=eth_address, prices={"0x111": 100})
        result = await adapter.fetch_prices([usdc_address], accumulator)

        # Should return unchanged (silent failure)
        assert result.prices == {"0x111": 100}
        assert usdc_address not in result.prices

    @pytest.mark.asyncio
    async def test_multiple_stablecoins_same_price(
        self, mocker, config, eth_address, usdc_address, usdt_address
    ):
        """All stablecoins should get the same USD/ETH price."""
        adapter = ChainlinkAdapter(config)

        mock_eth_usd_price = (3000 * 10**8, 8)
        mocker.patch.object(
            adapter, "_get_eth_usd_price", return_value=mock_eth_usd_price
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address, usdt_address], accumulator)

        # Both should have the same price
        assert result.prices[usdc_address] == result.prices[usdt_address]

    @pytest.mark.asyncio
    async def test_case_insensitive_stablecoin_matching(
        self, mocker, config, eth_address
    ):
        """Stablecoin matching should be case-insensitive."""
        adapter = ChainlinkAdapter(config)

        mock_eth_usd_price = (3000 * 10**8, 8)
        mocker.patch.object(
            adapter, "_get_eth_usd_price", return_value=mock_eth_usd_price
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        # Use uppercase version of configured lowercase
        uppercase_usdc = "0xA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([uppercase_usdc], accumulator)

        assert uppercase_usdc in result.prices


class TestGetEthUsdPrice:
    """Tests for _get_eth_usd_price method."""

    @pytest.mark.asyncio
    async def test_raises_on_negative_price(self, mocker, config):
        """Should raise ValueError if Chainlink returns negative price."""
        adapter = ChainlinkAdapter(config)

        # Mock contract calls
        mock_contract = MagicMock()
        mock_contract.functions.latestRoundData.return_value.call.return_value = (
            1,
            -100,
            0,
            1234567890,
            1,  # Negative answer
        )
        mock_contract.functions.decimals.return_value.call.return_value = 8

        mocker.patch.object(adapter.w3.eth, "contract", return_value=mock_contract)

        with pytest.raises(ValueError, match="Invalid Chainlink price"):
            await adapter._get_eth_usd_price()

    @pytest.mark.asyncio
    async def test_raises_on_zero_price(self, mocker, config):
        """Should raise ValueError if Chainlink returns zero price."""
        adapter = ChainlinkAdapter(config)

        mock_contract = MagicMock()
        mock_contract.functions.latestRoundData.return_value.call.return_value = (
            1,
            0,
            0,
            1234567890,
            1,  # Zero answer
        )
        mock_contract.functions.decimals.return_value.call.return_value = 8

        mocker.patch.object(adapter.w3.eth, "contract", return_value=mock_contract)

        with pytest.raises(ValueError, match="Invalid Chainlink price"):
            await adapter._get_eth_usd_price()


class TestTokenDecimalsCache:
    """Tests for token decimals caching."""

    @pytest.mark.asyncio
    async def test_caches_decimals(self, mocker, config):
        """Token decimals should be cached after first fetch."""
        adapter = ChainlinkAdapter(config)

        # Mock the contract call
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


class TestValidation:
    """Tests for price validation."""

    @pytest.mark.asyncio
    async def test_validates_prices_after_fetch(self, mocker, config, eth_address):
        """Should call validate_prices after fetching."""
        adapter = ChainlinkAdapter(config)

        mock_eth_usd_price = (3000 * 10**8, 8)
        mocker.patch.object(
            adapter, "_get_eth_usd_price", return_value=mock_eth_usd_price
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        mock_validate = mocker.patch.object(adapter, "validate_prices")

        accumulator = PriceData(base_asset=eth_address, prices={})
        usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        await adapter.fetch_prices([usdc], accumulator)

        mock_validate.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_chainlink_integration(config, eth_address, usdc_address):
    """Integration test with real Chainlink feed."""
    adapter = ChainlinkAdapter(config)

    accumulator = PriceData(base_asset=eth_address, prices={})
    result = await adapter.fetch_prices([usdc_address], accumulator)

    assert usdc_address in result.prices
    price = result.prices[usdc_address]
    assert isinstance(price, int)
    assert price > 0
    # USD/ETH should be roughly 0.0003 ETH at typical prices
    # So price should be around 300000000000000 (3e14)
    assert 1e13 < price < 1e16  # Sanity check
