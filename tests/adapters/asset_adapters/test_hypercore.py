"""Tests for HyperCore adapter."""

import pytest
import time
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from tq_oracle.adapters.asset_adapters.hypercore import (
    HyperCoreAdapter,
    USDC_DECIMALS,
    DECIMAL_MULTIPLIER,
)
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import (
    OracleSettings,
    ChainConfig,
    HyperCoreVaultConfig,
    HyperCoreSubAccountConfig,
)
from tq_oracle.constants import HL_MAX_PORTFOLIO_STALENESS_SECONDS


@pytest.fixture
def config():
    return OracleSettings(
        vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        safe_address=None,
        dry_run=False,
        private_key=None,
        safe_txn_srvc_api_key=None,
    )


@pytest.fixture
def chain_config():
    return ChainConfig(
        name="hypercore",
        network="hypercore",
        api_url="https://api.hyperliquid.xyz",
        role="satellite",
        required=False,
        vaults=[
            HyperCoreVaultConfig(
                vault_address="0xVaultAddress1",
                name="perp_strategy_1",
            )
        ],
        subaccounts=[
            HyperCoreSubAccountConfig(
                master_address="0xMasterAddress",
                subaccount_name="subaccount_0",
            )
        ],
    )


@pytest.fixture
def config_with_chain(config, chain_config):
    """Config with hypercore chain configured."""
    config.chains = [chain_config]
    return config


def test_usdc_decimals():
    """USDC should have 6 decimals."""
    assert USDC_DECIMALS == 6


def test_decimal_multiplier():
    """DECIMAL_MULTIPLIER should be 10^12 (18 - 6)."""
    assert DECIMAL_MULTIPLIER == 10**12


def test_staleness_threshold():
    """Staleness threshold should be 120 seconds."""
    assert HL_MAX_PORTFOLIO_STALENESS_SECONDS == 120


def test_adapter_init(config_with_chain):
    """Adapter should initialize with config and find chain config."""
    adapter = HyperCoreAdapter(config_with_chain)

    assert adapter.config == config_with_chain
    assert adapter._chain_config is not None
    assert adapter.api_url == "https://api.hyperliquid.xyz"


def test_adapter_init_custom_api_url(config):
    """Adapter should accept custom API URL."""
    adapter = HyperCoreAdapter(config, api_url="https://custom.api.com")

    assert adapter.api_url == "https://custom.api.com"


def test_adapter_name(config):
    """Adapter should report correct name."""
    adapter = HyperCoreAdapter(config)
    assert adapter.adapter_name == "hypercore"


@pytest.mark.asyncio
async def test_fetch_portfolio_nav_parses_response(config_with_chain):
    """Should parse portfolio response correctly."""
    adapter = HyperCoreAdapter(config_with_chain)

    # Mock fresh API response matching actual API format
    current_time_ms = int(time.time() * 1000)
    mock_response = [
        ("day", {
            "accountValueHistory": [
                [current_time_ms - 1000, "1000000.0"],  # 1 second ago, $1M
            ]
        })
    ]

    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = AsyncMock(return_value=mock_response)
        mock_response_obj.__aenter__ = AsyncMock(return_value=mock_response_obj)
        mock_response_obj.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response_obj)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session

        nav = await adapter._fetch_portfolio_nav("0xTestAddress")

    # $1M USDC = 1,000,000 * 10^6 * 10^12 = 10^24 wei
    expected = 1_000_000 * (10**USDC_DECIMALS) * DECIMAL_MULTIPLIER
    assert nav == expected


@pytest.mark.asyncio
async def test_fetch_portfolio_nav_rejects_stale(config_with_chain):
    """Should reject stale portfolio data."""
    adapter = HyperCoreAdapter(config_with_chain)

    # Mock stale API response (5 minutes old)
    stale_time_ms = int(time.time() * 1000) - (5 * 60 * 1000)
    mock_response = [
        ("day", {
            "accountValueHistory": [
                [stale_time_ms, "1000000.0"],
            ]
        })
    ]

    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = AsyncMock(return_value=mock_response)
        mock_response_obj.__aenter__ = AsyncMock(return_value=mock_response_obj)
        mock_response_obj.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response_obj)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session

        with pytest.raises(ValueError, match="stale"):
            await adapter._fetch_portfolio_nav("0xTestAddress")


@pytest.mark.asyncio
async def test_fetch_vault_nav_parses_response(config_with_chain):
    """Should fetch vault NAV from API."""
    adapter = HyperCoreAdapter(config_with_chain)

    mock_response = {
        "portfolio": {
            "equity": "500000.0",
        }
    }

    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = AsyncMock(return_value=mock_response)
        mock_response_obj.__aenter__ = AsyncMock(return_value=mock_response_obj)
        mock_response_obj.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response_obj)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session

        nav = await adapter._fetch_vault_nav("0xVaultAddress")

    # $500K USDC = 500,000 * 10^6 * 10^12
    expected = 500_000 * (10**USDC_DECIMALS) * DECIMAL_MULTIPLIER
    assert nav == expected


@pytest.mark.asyncio
async def test_fetch_all_assets_combines_vaults_and_subaccounts(config_with_chain):
    """Should fetch assets from all configured vaults and subaccounts."""
    adapter = HyperCoreAdapter(config_with_chain)

    # Mock vault NAV
    async def mock_fetch_vault_nav(address):
        return 100_000 * (10**USDC_DECIMALS) * DECIMAL_MULTIPLIER  # $100K in wei

    # Mock subaccount NAV
    async def mock_fetch_portfolio_nav(address):
        return 50_000 * (10**USDC_DECIMALS) * DECIMAL_MULTIPLIER  # $50K in wei

    adapter._fetch_vault_nav = mock_fetch_vault_nav
    adapter._fetch_portfolio_nav = mock_fetch_portfolio_nav

    assets = await adapter.fetch_all_assets()

    # Should have 2 assets (1 vault + 1 subaccount)
    assert len(assets) == 2

    # Check total value
    total_nav = sum(a.amount for a in assets)
    expected_wei = (100_000 + 50_000) * (10**USDC_DECIMALS) * DECIMAL_MULTIPLIER
    assert total_nav == expected_wei


@pytest.mark.asyncio
async def test_fetch_assets_single_address(config):
    """fetch_assets should work for a single address."""
    adapter = HyperCoreAdapter(config)

    # Mock the _fetch_portfolio_nav method
    expected_nav_wei = 75_000 * (10**USDC_DECIMALS) * DECIMAL_MULTIPLIER

    with patch.object(adapter, "_fetch_portfolio_nav", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = expected_nav_wei

        assets = await adapter.fetch_assets("0xSingleAddress")

    assert len(assets) == 1
    assert assets[0].amount == expected_nav_wei


def test_asset_data_uses_usdc_address(config):
    """AssetData should use USDC HyperEVM address."""
    adapter = HyperCoreAdapter(config)

    # The adapter should use USDC_HYPEREVM_MAINNET for asset address
    from tq_oracle.constants import USDC_HYPEREVM_MAINNET

    assert adapter.usdc_address.lower() == USDC_HYPEREVM_MAINNET.lower()


@pytest.mark.asyncio
async def test_fetch_all_assets_empty_config(config):
    """Should return empty list if no vaults/subaccounts configured."""
    # Config without hypercore chain
    adapter = HyperCoreAdapter(config)

    assets = await adapter.fetch_all_assets()

    assert assets == []


@pytest.mark.asyncio
async def test_fetch_assets_handles_zero_nav(config):
    """Should return empty list for zero NAV."""
    adapter = HyperCoreAdapter(config)

    with patch.object(adapter, "_fetch_portfolio_nav", new_callable=AsyncMock) as mock_fetch:
        mock_fetch.return_value = 0

        assets = await adapter.fetch_assets("0xZeroBalance")

    assert assets == []


@pytest.mark.asyncio
async def test_parse_history_point_valid():
    """Should parse valid history points as Decimal."""
    config = OracleSettings(
        vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        dry_run=False,
    )
    adapter = HyperCoreAdapter(config)

    result = adapter._parse_history_point(1234567890000, "1000.50")

    assert result == (1234567890000, Decimal("1000.50"))


@pytest.mark.asyncio
async def test_parse_history_point_invalid():
    """Should return None for invalid history points."""
    config = OracleSettings(
        vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        dry_run=False,
    )
    adapter = HyperCoreAdapter(config)

    # Invalid string
    result = adapter._parse_history_point(1234567890000, "invalid")
    assert result is None

    # Negative value
    result = adapter._parse_history_point(1234567890000, "-100")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_vault_nav_rejects_stale(config_with_chain):
    """Should reject stale vault data when lastUpdateTime is present."""
    adapter = HyperCoreAdapter(config_with_chain)

    stale_time_ms = int(time.time() * 1000) - (5 * 60 * 1000)  # 5 minutes ago
    mock_response = {
        "portfolio": {"equity": "500000.0"},
        "lastUpdateTime": stale_time_ms,
    }

    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = AsyncMock(return_value=mock_response)
        mock_response_obj.__aenter__ = AsyncMock(return_value=mock_response_obj)
        mock_response_obj.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response_obj)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session

        with pytest.raises(ValueError, match="stale"):
            await adapter._fetch_vault_nav("0xVaultAddress")


@pytest.mark.asyncio
async def test_fetch_vault_nav_accepts_fresh(config_with_chain):
    """Should accept fresh vault data when lastUpdateTime is recent."""
    adapter = HyperCoreAdapter(config_with_chain)

    fresh_time_ms = int(time.time() * 1000) - 5000  # 5 seconds ago
    mock_response = {
        "portfolio": {"equity": "500000.0"},
        "lastUpdateTime": fresh_time_ms,
    }

    with patch("aiohttp.ClientSession") as mock_session_class:
        mock_session = MagicMock()
        mock_response_obj = MagicMock()
        mock_response_obj.status = 200
        mock_response_obj.json = AsyncMock(return_value=mock_response)
        mock_response_obj.__aenter__ = AsyncMock(return_value=mock_response_obj)
        mock_response_obj.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_response_obj)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        mock_session_class.return_value = mock_session

        nav = await adapter._fetch_vault_nav("0xVaultAddress")

    expected = 500_000 * (10**USDC_DECIMALS) * DECIMAL_MULTIPLIER
    assert nav == expected


def test_decimal_precision_large_nav(config):
    """Decimal conversion should be exact for large NAV values.

    Float would lose precision for values like $1,234,567,890.123456
    (16 significant digits exceeds float64's ~15.9 digit precision).
    """
    adapter = HyperCoreAdapter(config)

    current_time_ms = int(time.time() * 1000)
    # $1,234,567,890.123456 — 16 significant digits
    large_value = "1234567890.123456"

    data = [
        ("day", {
            "accountValueHistory": [
                [current_time_ms - 1000, large_value],
            ]
        })
    ]

    nav = adapter._parse_portfolio_response(data, "0xTest")

    # With Decimal: int(Decimal("1234567890.123456") * 10^6) = 1234567890123456 (exact)
    # With float:   int(1234567890.123456 * 10^6)   could be 1234567890123455 or ...457
    expected_usdc_6 = int(Decimal(large_value) * Decimal(10**USDC_DECIMALS))
    expected_wei = expected_usdc_6 * DECIMAL_MULTIPLIER
    assert nav == expected_wei


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_portfolio_nav_integration(config):
    """Integration test for portfolio NAV fetch."""
    adapter = HyperCoreAdapter(config)

    # Use a known address (this may fail if API is down)
    try:
        nav = await adapter._fetch_portfolio_nav("0x0000000000000000000000000000000000000000")
    except ValueError as e:
        # Empty response is acceptable for zero address
        assert "Empty" in str(e) or "stale" in str(e) or "No" in str(e)
