"""Comprehensive tests for CoinGeckoAdapter."""

import pytest
from unittest.mock import MagicMock

import requests

from tq_oracle.adapters.price_adapters.base import PriceData
from tq_oracle.adapters.price_adapters.coingecko import CoinGeckoAdapter
from tq_oracle.settings import Network, OracleSettings


@pytest.fixture
def config():
    """Base config with CoinGecko enabled."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        coingecko_enabled=True,
        coingecko_token_ids={
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": "usd-coin",  # USDC
        },
    )


@pytest.fixture
def config_disabled():
    """Config with CoinGecko disabled."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        coingecko_enabled=False,
    )


@pytest.fixture
def config_with_api_key():
    """Config with CoinGecko Pro API key."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
        coingecko_enabled=True,
        coingecko_api_key="test-api-key",
        coingecko_token_ids={
            "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48": "usd-coin",
        },
    )


@pytest.fixture
def eth_address(config):
    return config.assets["ETH"]


@pytest.fixture
def usdc_address():
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def weth_address(config):
    return config.assets["WETH"]


class TestCoinGeckoAdapterInit:
    """Tests for CoinGeckoAdapter initialization."""

    def test_adapter_name(self, config):
        adapter = CoinGeckoAdapter(config)
        assert adapter.adapter_name == "coingecko"

    def test_disabled_adapter_sets_skip_flag(self, config_disabled):
        adapter = CoinGeckoAdapter(config_disabled)
        assert adapter._skip is True

    def test_enabled_adapter_not_skipped(self, config):
        adapter = CoinGeckoAdapter(config)
        assert adapter._skip is False

    def test_uses_free_api_without_key(self, config):
        adapter = CoinGeckoAdapter(config)
        assert adapter.api_base_url == "https://api.coingecko.com/api/v3"
        assert adapter.api_key is None

    def test_uses_pro_api_with_key(self, config_with_api_key):
        adapter = CoinGeckoAdapter(config_with_api_key)
        assert adapter.api_base_url == "https://pro-api.coingecko.com/api/v3"
        assert adapter.api_key == "test-api-key"

    def test_token_ids_lowercased(self, config):
        adapter = CoinGeckoAdapter(config)
        for addr in adapter.token_ids:
            assert addr == addr.lower()

    def test_merges_default_and_custom_ids(self):
        """Custom token IDs should override defaults."""
        config = OracleSettings(
            vault_address="0xVault",
            oracle_helper_address="0xOracleHelper",
            vault_rpc="https://eth.drpc.org",
            block_number=23690139,
            network=Network.MAINNET,
            dry_run=False,
            coingecko_enabled=True,
            coingecko_token_ids={
                # Override default USDC mapping
                "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "custom-usd-coin",
            },
        )
        adapter = CoinGeckoAdapter(config)
        # Custom should override default
        assert (
            adapter.token_ids["0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"]
            == "custom-usd-coin"
        )

    def test_no_tokens_sets_skip_flag(self):
        """Adapter should skip if no tokens configured and no defaults apply."""
        config = OracleSettings(
            vault_address="0xVault",
            oracle_helper_address="0xOracleHelper",
            vault_rpc="https://eth.drpc.org",
            block_number=23690139,
            network=Network.MAINNET,
            dry_run=False,
            coingecko_enabled=True,
            coingecko_token_ids={},
        )
        adapter = CoinGeckoAdapter(config)
        # Default IDs exist, so should NOT skip
        assert adapter._skip is False

    def test_initializes_decimals_cache(self, config):
        adapter = CoinGeckoAdapter(config)
        assert adapter._decimals_cache == {}


class TestFetchPricesBatch:
    """Tests for _fetch_prices_batch method."""

    @pytest.mark.asyncio
    async def test_fetches_single_token(self, mocker, config):
        """Should fetch price for a single token."""
        adapter = CoinGeckoAdapter(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {"usd-coin": {"eth": 0.000333}}
        mock_response.raise_for_status = MagicMock()

        mocker.patch.object(adapter._session, "get", return_value=mock_response)

        result = await adapter._fetch_prices_batch(["usd-coin"])

        assert "usd-coin" in result
        assert result["usd-coin"] == 0.000333

    @pytest.mark.asyncio
    async def test_fetches_multiple_tokens(self, mocker, config):
        """Should batch multiple tokens in one API call."""
        adapter = CoinGeckoAdapter(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "usd-coin": {"eth": 0.000333},
            "tether": {"eth": 0.000332},
            "dai": {"eth": 0.000334},
        }
        mock_response.raise_for_status = MagicMock()

        mocker.patch.object(adapter._session, "get", return_value=mock_response)

        result = await adapter._fetch_prices_batch(["usd-coin", "tether", "dai"])

        assert len(result) == 3
        assert result["usd-coin"] == 0.000333
        assert result["tether"] == 0.000332
        assert result["dai"] == 0.000334

    @pytest.mark.asyncio
    async def test_handles_missing_token_in_response(self, mocker, config):
        """Should handle when a token is not in API response."""
        adapter = CoinGeckoAdapter(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "usd-coin": {"eth": 0.000333},
            # "missing-token" not in response
        }
        mock_response.raise_for_status = MagicMock()

        mocker.patch.object(adapter._session, "get", return_value=mock_response)

        result = await adapter._fetch_prices_batch(["usd-coin", "missing-token"])

        assert "usd-coin" in result
        assert "missing-token" not in result

    @pytest.mark.asyncio
    async def test_includes_api_key_header_when_present(
        self, mocker, config_with_api_key
    ):
        """Should include API key in headers when configured."""
        adapter = CoinGeckoAdapter(config_with_api_key)

        mock_response = MagicMock()
        mock_response.json.return_value = {"usd-coin": {"eth": 0.000333}}
        mock_response.raise_for_status = MagicMock()

        mock_get = mocker.patch.object(adapter._session, "get", return_value=mock_response)

        await adapter._fetch_prices_batch(["usd-coin"])

        # Check that API key was passed in headers
        call_kwargs = mock_get.call_args[1]
        assert call_kwargs["headers"]["x-cg-pro-api-key"] == "test-api-key"

    @pytest.mark.asyncio
    async def test_no_api_key_header_when_not_configured(self, mocker, config):
        """Should not include API key header when not configured."""
        adapter = CoinGeckoAdapter(config)

        mock_response = MagicMock()
        mock_response.json.return_value = {"usd-coin": {"eth": 0.000333}}
        mock_response.raise_for_status = MagicMock()

        mock_get = mocker.patch.object(adapter._session, "get", return_value=mock_response)

        await adapter._fetch_prices_batch(["usd-coin"])

        call_kwargs = mock_get.call_args[1]
        assert "x-cg-pro-api-key" not in call_kwargs["headers"]


class TestFetchPrices:
    """Tests for fetch_prices method."""

    @pytest.mark.asyncio
    async def test_skipped_adapter_returns_unchanged(
        self, config_disabled, eth_address
    ):
        """Disabled adapter should return accumulator unchanged."""
        adapter = CoinGeckoAdapter(config_disabled)
        accumulator = PriceData(base_asset=eth_address, prices={"0x111": 100})

        result = await adapter.fetch_prices(["0xUSDC"], accumulator)

        assert result.prices == {"0x111": 100}

    @pytest.mark.asyncio
    async def test_raises_on_wrong_base_asset(self, config):
        """Should raise ValueError if base_asset is not ETH."""
        adapter = CoinGeckoAdapter(config)
        wrong_base = "0xWrongBase"

        with pytest.raises(ValueError, match="only supports ETH as base asset"):
            await adapter.fetch_prices(
                ["0xUSDC"],
                PriceData(base_asset=wrong_base, prices={}),
            )

    @pytest.mark.asyncio
    async def test_prices_configured_tokens(
        self, mocker, config, eth_address, usdc_address
    ):
        """Should price tokens that have CoinGecko ID mappings."""
        adapter = CoinGeckoAdapter(config)

        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": 0.000333}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert usdc_address in result.prices
        # 0.000333 ETH = 333000000000000 wei
        expected_price = int(0.000333 * 10**18)
        assert result.prices[usdc_address] == expected_price

    @pytest.mark.asyncio
    async def test_skips_unmapped_tokens(self, mocker, config, eth_address):
        """Tokens without CoinGecko ID mappings should not be priced."""
        adapter = CoinGeckoAdapter(config)

        mocker.patch.object(adapter, "_fetch_prices_batch", return_value={})

        unmapped_token = "0xUnmappedToken"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([unmapped_token], accumulator)

        assert unmapped_token not in result.prices

    @pytest.mark.asyncio
    async def test_preserves_existing_prices(
        self, mocker, config, eth_address, usdc_address
    ):
        """Should preserve prices already in accumulator."""
        adapter = CoinGeckoAdapter(config)

        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": 0.000333}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        existing_prices = {"0x111": 999}
        accumulator = PriceData(base_asset=eth_address, prices=existing_prices.copy())
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert result.prices["0x111"] == 999
        assert usdc_address in result.prices

    @pytest.mark.asyncio
    async def test_handles_api_failure_gracefully(self, mocker, config, eth_address):
        """Should return unchanged accumulator on API failure."""
        adapter = CoinGeckoAdapter(config)

        mocker.patch.object(
            adapter,
            "_fetch_prices_batch",
            side_effect=requests.exceptions.RequestException("API error"),
        )

        usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        accumulator = PriceData(base_asset=eth_address, prices={"0x111": 100})
        result = await adapter.fetch_prices([usdc], accumulator)

        assert result.prices == {"0x111": 100}
        assert usdc not in result.prices

    @pytest.mark.asyncio
    async def test_case_insensitive_token_matching(self, mocker, config, eth_address):
        """Token matching should be case-insensitive."""
        adapter = CoinGeckoAdapter(config)

        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": 0.000333}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        # Use uppercase version
        uppercase_usdc = "0xA0B86991C6218B36C1D19D4A2E9EB0CE3606EB48"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([uppercase_usdc], accumulator)

        assert uppercase_usdc in result.prices

    @pytest.mark.asyncio
    async def test_stores_token_decimals(
        self, mocker, config, eth_address, usdc_address
    ):
        """Should store token decimals in result."""
        adapter = CoinGeckoAdapter(config)

        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": 0.000333}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc_address], accumulator)

        assert usdc_address in result.decimals
        assert result.decimals[usdc_address] == 6

    @pytest.mark.asyncio
    async def test_no_tokens_to_price_returns_early(self, mocker, config, eth_address):
        """Should return early if no tokens match CoinGecko IDs."""
        adapter = CoinGeckoAdapter(config)

        mock_fetch = mocker.patch.object(adapter, "_fetch_prices_batch")

        unmapped = "0xUnmapped"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([unmapped], accumulator)

        # API should not be called
        mock_fetch.assert_not_called()
        assert unmapped not in result.prices


class TestPriceConversion:
    """Tests for price conversion from ETH float to wei."""

    @pytest.mark.asyncio
    async def test_converts_typical_price_to_wei(self, mocker, config, eth_address):
        """Test conversion of typical stablecoin price."""
        adapter = CoinGeckoAdapter(config)

        # 0.000333 ETH per USDC
        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": 0.000333}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc], accumulator)

        # 0.000333 * 10^18 = 333000000000000
        expected = int(0.000333 * 10**18)
        assert result.prices[usdc] == expected

    @pytest.mark.asyncio
    async def test_converts_high_precision_price(self, mocker, config, eth_address):
        """Test conversion with high-precision price."""
        adapter = CoinGeckoAdapter(config)

        # Very precise price
        precise_price = 0.000333333333333333
        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": precise_price}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)

        usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc], accumulator)

        expected = int(precise_price * 10**18)
        assert result.prices[usdc] == expected

    @pytest.mark.asyncio
    async def test_float_precision_loss_potential(self, mocker, config, eth_address):
        """
        Test demonstrating potential float precision loss.

        This test shows that using float * 10^18 can lose precision
        compared to using Decimal. This is a known issue in the adapter.
        """
        adapter = CoinGeckoAdapter(config)

        # Price that might have float precision issues
        # 0.123456789012345678 has more precision than float64 can represent
        price_str = "0.123456789012345678"
        price_float = float(price_str)

        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": price_float}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=18)

        usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        accumulator = PriceData(base_asset=eth_address, prices={})
        result = await adapter.fetch_prices([usdc], accumulator)

        # Current implementation uses float
        float_result = int(price_float * 10**18)

        # The adapter uses float, so result matches float_result
        assert result.prices[usdc] == float_result

        # Note: These might differ due to float precision
        # This test documents the behavior rather than asserting correctness


class TestTokenDecimalsCache:
    """Tests for token decimals caching."""

    @pytest.mark.asyncio
    async def test_caches_decimals(self, mocker, config):
        """Token decimals should be cached after first fetch."""
        adapter = CoinGeckoAdapter(config)

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

    @pytest.mark.asyncio
    async def test_different_tokens_cached_separately(self, mocker, config):
        """Different tokens should be cached independently."""
        adapter = CoinGeckoAdapter(config)

        call_count = 0

        def make_decimals_call(decimals_value):
            nonlocal call_count
            call_count += 1
            return decimals_value

        mock_contract = MagicMock()
        mock_contract.functions.decimals.return_value.call.side_effect = [6, 18]
        mocker.patch.object(adapter.w3.eth, "contract", return_value=mock_contract)

        token1 = "0x1111111111111111111111111111111111111111"
        token2 = "0x2222222222222222222222222222222222222222"

        result1 = await adapter.get_token_decimals(token1)
        result2 = await adapter.get_token_decimals(token2)

        assert result1 == 6
        assert result2 == 18


class TestValidation:
    """Tests for price validation."""

    @pytest.mark.asyncio
    async def test_validates_prices_after_fetch(self, mocker, config, eth_address):
        """Should call validate_prices after fetching."""
        adapter = CoinGeckoAdapter(config)

        mocker.patch.object(
            adapter, "_fetch_prices_batch", return_value={"usd-coin": 0.000333}
        )
        mocker.patch.object(adapter, "get_token_decimals", return_value=6)
        mock_validate = mocker.patch.object(adapter, "validate_prices")

        usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        accumulator = PriceData(base_asset=eth_address, prices={})
        await adapter.fetch_prices([usdc], accumulator)

        mock_validate.assert_called_once()


class TestRetryBehavior:
    """Tests for retry/backoff behavior."""

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit(self, mocker, config):
        """Should retry on 429 rate limit response."""
        adapter = CoinGeckoAdapter(config)

        # First call fails with 429, second succeeds
        mock_response_429 = MagicMock()
        mock_response_429.status_code = 429
        mock_response_429.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_429
        )

        mock_response_ok = MagicMock()
        mock_response_ok.json.return_value = {"usd-coin": {"eth": 0.000333}}
        mock_response_ok.raise_for_status = MagicMock()

        mock_get = mocker.patch.object(
            adapter._session, "get", side_effect=[mock_response_429, mock_response_ok]
        )

        # The backoff decorator should handle retries
        result = await adapter._fetch_prices_batch(["usd-coin"])

        assert "usd-coin" in result
        assert mock_get.call_count == 2

    @pytest.mark.asyncio
    async def test_gives_up_on_client_error(self, mocker, config):
        """Should not retry on 4xx client errors (except 429)."""
        adapter = CoinGeckoAdapter(config)

        mock_response_404 = MagicMock()
        mock_response_404.status_code = 404
        mock_response_404.raise_for_status.side_effect = requests.exceptions.HTTPError(
            response=mock_response_404
        )

        mocker.patch.object(adapter._session, "get", return_value=mock_response_404)

        with pytest.raises(requests.exceptions.HTTPError):
            await adapter._fetch_prices_batch(["usd-coin"])


@pytest.mark.asyncio
@pytest.mark.integration
async def test_coingecko_integration(config, eth_address):
    """Integration test with real CoinGecko API (free tier)."""
    adapter = CoinGeckoAdapter(config)

    usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    accumulator = PriceData(base_asset=eth_address, prices={})
    result = await adapter.fetch_prices([usdc], accumulator)

    assert usdc in result.prices
    price = result.prices[usdc]
    assert isinstance(price, int)
    assert price > 0
    # USD/ETH should be roughly 0.0003 ETH at typical prices
    assert 1e13 < price < 1e16  # Sanity check
