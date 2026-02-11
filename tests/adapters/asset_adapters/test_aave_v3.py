"""Comprehensive tests for Aave V3 adapter."""

import pytest

from tq_oracle.adapters.asset_adapters.aave_v3 import AaveV3Adapter
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import Network, OracleSettings
from tq_oracle.constants import (
    AAVE_V3_POOL_MAINNET,
    AAVE_V3_SUPPLY_TOKENS_MAINNET,
    AAVE_V3_BORROW_TOKENS_MAINNET,
)


@pytest.fixture
def config():
    """Base mainnet config for Aave V3 tests."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=23690139,
        network=Network.MAINNET,
        dry_run=False,
    )


@pytest.fixture
def config_sepolia():
    """Sepolia config - Aave V3 should skip."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://sepolia.drpc.org",
        block_number=9522842,
        network=Network.SEPOLIA,
        dry_run=False,
    )


@pytest.fixture
def subvault_address():
    return "0x90c983DC732e65DB6177638f0125914787b8Cb78"


# Underlying asset addresses
@pytest.fixture
def usdc_address():
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def usdt_address():
    return "0xdAC17F958D2ee523a2206206994597C13D831ec7"


@pytest.fixture
def usde_address():
    """USDe underlying asset address."""
    return "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"


@pytest.fixture
def weth_address():
    return "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"


class TestAaveV3AdapterInit:
    """Tests for adapter initialization."""

    def test_adapter_name(self, config):
        adapter = AaveV3Adapter(config)
        assert adapter.adapter_name == "aave_v3"

    def test_skips_on_non_mainnet(self, config_sepolia):
        adapter = AaveV3Adapter(config_sepolia)
        assert adapter._skip is True

    def test_not_skipped_on_mainnet(self, config):
        adapter = AaveV3Adapter(config)
        assert adapter._skip is False

    def test_uses_default_pool_address(self, config):
        adapter = AaveV3Adapter(config)
        assert adapter.pool_address == AAVE_V3_POOL_MAINNET

    def test_uses_default_supply_tokens(self, config):
        adapter = AaveV3Adapter(config)
        assert adapter.supply_tokens == AAVE_V3_SUPPLY_TOKENS_MAINNET

    def test_uses_default_borrow_tokens(self, config):
        adapter = AaveV3Adapter(config)
        assert adapter.borrow_tokens == AAVE_V3_BORROW_TOKENS_MAINNET

    def test_override_pool_address(self, config):
        custom_pool = "0xCustomPool"
        adapter = AaveV3Adapter(config, pool_address=custom_pool)
        assert adapter.pool_address == custom_pool

    def test_override_supply_tokens(self, config):
        custom_tokens = {"CUSTOM": "0xCustomToken"}
        adapter = AaveV3Adapter(config, supply_tokens=custom_tokens)
        assert adapter.supply_tokens == custom_tokens

    def test_override_borrow_tokens(self, config):
        custom_tokens = {"CUSTOM": "0xCustomDebt"}
        adapter = AaveV3Adapter(config, borrow_tokens=custom_tokens)
        assert adapter.borrow_tokens == custom_tokens

    def test_stores_block_number(self, config):
        adapter = AaveV3Adapter(config)
        assert adapter.block_number == 23690139

    def test_default_supply_tokens_include_usdc(self, config):
        adapter = AaveV3Adapter(config)
        assert "USDC" in adapter.supply_tokens

    def test_default_supply_tokens_include_usdt(self, config):
        adapter = AaveV3Adapter(config)
        assert "USDT" in adapter.supply_tokens

    def test_default_supply_tokens_include_usde(self, config):
        adapter = AaveV3Adapter(config)
        assert "USDe" in adapter.supply_tokens

    def test_default_borrow_tokens_include_usdc(self, config):
        adapter = AaveV3Adapter(config)
        assert "USDC" in adapter.borrow_tokens

    def test_default_borrow_tokens_include_usdt(self, config):
        adapter = AaveV3Adapter(config)
        assert "USDT" in adapter.borrow_tokens

    def test_default_borrow_tokens_include_usde(self, config):
        adapter = AaveV3Adapter(config)
        assert "USDe" in adapter.borrow_tokens


class TestFetchAssets:
    """Tests for fetch_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_skipped(self, config_sepolia, subvault_address):
        """Skipped adapter should return empty list."""
        adapter = AaveV3Adapter(config_sepolia)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_asset_data_list(
        self, mocker, config, subvault_address, usdc_address
    ):
        """Should return list of AssetData."""
        adapter = AaveV3Adapter(config)

        # Mock balance and underlying queries
        mocker.patch.object(adapter, "_balance_of", return_value=1000000)  # 1 USDC
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address)

        assert isinstance(result, list)
        assert all(isinstance(item, AssetData) for item in result)

    @pytest.mark.asyncio
    async def test_supply_positions_are_positive(
        self, mocker, config, subvault_address, usdc_address
    ):
        """Supply (aToken) balances should be positive."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {}

        mocker.patch.object(
            adapter, "_balance_of", return_value=1000000000
        )  # 1000 USDC
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].amount > 0
        assert result[0].amount == 1000000000

    @pytest.mark.asyncio
    async def test_borrow_positions_are_negative(
        self, mocker, config, subvault_address, usdc_address
    ):
        """Borrow (debt token) balances should be negative."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {}
        adapter.borrow_tokens = {"USDC": "0xDebtUSDC"}

        mocker.patch.object(
            adapter, "_balance_of", return_value=500000000
        )  # 500 USDC borrowed
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].amount < 0
        assert result[0].amount == -500000000

    @pytest.mark.asyncio
    async def test_zero_balance_not_included(
        self, mocker, config, subvault_address, usdc_address
    ):
        """Zero balance positions should not be in results."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {}

        mocker.patch.object(adapter, "_balance_of", return_value=0)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_returns_underlying_asset_address(
        self, mocker, config, subvault_address, usdc_address
    ):
        """Should return underlying asset address, not aToken address."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {}

        mocker.patch.object(adapter, "_balance_of", return_value=1000000)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address)

        assert result[0].asset_address == usdc_address
        assert result[0].asset_address != "0xaUSDC"

    @pytest.mark.asyncio
    async def test_handles_multiple_supply_tokens(
        self, mocker, config, subvault_address, usdc_address, usdt_address
    ):
        """Should handle multiple supply positions."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC", "USDT": "0xaUSDT"}
        adapter.borrow_tokens = {}

        # Return different balances for each token
        balances = [1000000, 2000000]  # 1 USDC, 2 USDT
        underlyings = [usdc_address, usdt_address]

        mocker.patch.object(adapter, "_balance_of", side_effect=balances)
        mocker.patch.object(adapter, "_get_underlying_asset", side_effect=underlyings)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 2
        addresses = [r.asset_address for r in result]
        assert usdc_address in addresses
        assert usdt_address in addresses

    @pytest.mark.asyncio
    async def test_handles_mixed_supply_and_borrow(
        self, mocker, config, subvault_address, usdc_address, usdt_address
    ):
        """Should handle both supply and borrow positions."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {"USDT": "0xDebtUSDT"}

        # Mock: supply 1000 USDC, borrow 500 USDT
        async def mock_balance(token, owner):
            if token == "0xaUSDC":
                return 1000000000
            elif token == "0xDebtUSDT":
                return 500000000
            return 0

        async def mock_underlying(token):
            if token == "0xaUSDC":
                return usdc_address
            elif token == "0xDebtUSDT":
                return usdt_address
            return "0x0"

        mocker.patch.object(adapter, "_balance_of", side_effect=mock_balance)
        mocker.patch.object(
            adapter, "_get_underlying_asset", side_effect=mock_underlying
        )

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 2

        usdc_result = next(r for r in result if r.asset_address == usdc_address)
        usdt_result = next(r for r in result if r.asset_address == usdt_address)

        assert usdc_result.amount == 1000000000  # Positive (supply)
        assert usdt_result.amount == -500000000  # Negative (borrow)

    @pytest.mark.asyncio
    async def test_merges_previous_assets(
        self, mocker, config, subvault_address, usdc_address, usdt_address
    ):
        """Should merge previous adapter assets when chaining adapters."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {}

        # Mock this adapter's own assets
        mocker.patch.object(adapter, "_balance_of", return_value=1000000)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        # Previous assets from another adapter in the chain
        previous_assets = [
            AssetData(asset_address=usdt_address, amount=2000000),
            AssetData(asset_address="0xSomeOtherToken", amount=500000),
        ]

        result = await adapter.fetch_assets(subvault_address, previous_assets)

        # Should have previous assets + this adapter's assets
        assert len(result) == 3

        # Previous assets should be preserved
        usdt_result = next(r for r in result if r.asset_address == usdt_address)
        assert usdt_result.amount == 2000000

        other_result = next(r for r in result if r.asset_address == "0xSomeOtherToken")
        assert other_result.amount == 500000

        # This adapter's assets should be added
        usdc_result = next(r for r in result if r.asset_address == usdc_address)
        assert usdc_result.amount == 1000000

    @pytest.mark.asyncio
    async def test_returns_only_own_assets_when_no_previous(
        self, mocker, config, subvault_address, usdc_address
    ):
        """Without previous_assets, should return only own assets."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {}

        mocker.patch.object(adapter, "_balance_of", return_value=1000000)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address, previous_assets=None)

        assert len(result) == 1
        assert result[0].asset_address == usdc_address


class TestDecimalHandling:
    """Tests verifying decimal handling for different assets."""

    @pytest.mark.asyncio
    async def test_usdc_6_decimals(
        self, mocker, config, subvault_address, usdc_address
    ):
        """USDC has 6 decimals - verify raw balance handling."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {}

        # 1000 USDC = 1000 * 10^6 = 1,000,000,000
        usdc_balance = 1000 * 10**6

        mocker.patch.object(adapter, "_balance_of", return_value=usdc_balance)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address)

        assert result[0].amount == usdc_balance
        # Verify the raw value matches expected 6-decimal representation
        assert result[0].amount == 1000000000

    @pytest.mark.asyncio
    async def test_usdt_6_decimals(
        self, mocker, config, subvault_address, usdt_address
    ):
        """USDT has 6 decimals - verify raw balance handling."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDT": "0xaUSDT"}
        adapter.borrow_tokens = {}

        # 500 USDT = 500 * 10^6 = 500,000,000
        usdt_balance = 500 * 10**6

        mocker.patch.object(adapter, "_balance_of", return_value=usdt_balance)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdt_address)

        result = await adapter.fetch_assets(subvault_address)

        assert result[0].amount == usdt_balance
        assert result[0].amount == 500000000

    @pytest.mark.asyncio
    async def test_usde_18_decimals(
        self, mocker, config, subvault_address, usde_address
    ):
        """USDe has 18 decimals - verify raw balance handling."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDe": "0xaUSDe"}
        adapter.borrow_tokens = {}

        # 100 USDe = 100 * 10^18
        usde_balance = 100 * 10**18

        mocker.patch.object(adapter, "_balance_of", return_value=usde_balance)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usde_address)

        result = await adapter.fetch_assets(subvault_address)

        assert result[0].amount == usde_balance
        assert result[0].amount == 100 * 10**18

    @pytest.mark.asyncio
    async def test_weth_18_decimals(
        self, mocker, config, subvault_address, weth_address
    ):
        """WETH has 18 decimals - verify raw balance handling."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"WETH": "0xaWETH"}
        adapter.borrow_tokens = {}

        # 10 WETH = 10 * 10^18
        weth_balance = 10 * 10**18

        mocker.patch.object(adapter, "_balance_of", return_value=weth_balance)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=weth_address)

        result = await adapter.fetch_assets(subvault_address)

        assert result[0].amount == weth_balance


class TestNetValueCalculation:
    """Tests verifying net value when combining supply and borrow."""

    @pytest.mark.asyncio
    async def test_net_position_same_asset(
        self, mocker, config, subvault_address, usdc_address
    ):
        """
        When supplying and borrowing same asset, amounts should net out.

        Example: Supply 1000 USDC, Borrow 400 USDC
        Results should show both separately (not netted by adapter).
        """
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"USDC": "0xaUSDC"}
        adapter.borrow_tokens = {"USDC": "0xDebtUSDC"}

        async def mock_balance(token, owner):
            if token == "0xaUSDC":
                return 1000 * 10**6  # 1000 USDC supplied
            elif token == "0xDebtUSDC":
                return 400 * 10**6  # 400 USDC borrowed
            return 0

        mocker.patch.object(adapter, "_balance_of", side_effect=mock_balance)
        mocker.patch.object(adapter, "_get_underlying_asset", return_value=usdc_address)

        result = await adapter.fetch_assets(subvault_address)

        # Should have 2 entries (supply and borrow separately)
        assert len(result) == 2

        amounts = [r.amount for r in result]
        assert 1000 * 10**6 in amounts  # Supply (positive)
        assert -400 * 10**6 in amounts  # Borrow (negative)

        # Net should be 600 USDC when aggregated
        net = sum(amounts)
        assert net == 600 * 10**6


class TestFetchAllAssets:
    """Tests for fetch_all_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, config):
        """fetch_all_assets should return empty list (not supported)."""
        adapter = AaveV3Adapter(config)
        result = await adapter.fetch_all_assets()
        assert result == []


class TestAddressChecksumming:
    """Tests for address handling."""

    @pytest.mark.asyncio
    async def test_returns_checksummed_addresses(
        self, mocker, config, subvault_address
    ):
        """Returned asset addresses should be checksummed."""
        adapter = AaveV3Adapter(config)
        adapter.supply_tokens = {"TEST": "0xTestToken"}
        adapter.borrow_tokens = {}

        # Return lowercase address from mock
        lowercase_addr = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

        mocker.patch.object(adapter, "_balance_of", return_value=1000000)
        mocker.patch.object(
            adapter, "_get_underlying_asset", return_value=lowercase_addr
        )

        result = await adapter.fetch_assets(subvault_address)

        # Should be checksummed
        expected_checksummed = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        assert result[0].asset_address == expected_checksummed


# Integration tests - require real RPC
class TestAaveV3Integration:
    """Integration tests with real Aave V3 contracts."""

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_fetch_usdc_supply_balance(
        self, config, subvault_address, usdc_address
    ):
        """Integration: Fetch aUSDC balance and verify it returns USDC underlying."""
        adapter = AaveV3Adapter(config)

        # Only test USDC supply
        adapter.supply_tokens = {"USDC": AAVE_V3_SUPPLY_TOKENS_MAINNET["USDC"]}
        adapter.borrow_tokens = {}

        result = await adapter.fetch_assets(subvault_address)

        # Result may be empty if subvault has no aUSDC
        for asset in result:
            assert isinstance(asset, AssetData)
            assert isinstance(asset.amount, int)
            # If there's a USDC position, verify it's the correct underlying
            if asset.amount != 0:
                assert asset.asset_address == usdc_address

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_underlying_asset_ausdc(self, config):
        """Integration: Verify aUSDC returns USDC underlying address."""
        adapter = AaveV3Adapter(config)

        ausdc_address = AAVE_V3_SUPPLY_TOKENS_MAINNET["USDC"]
        underlying = await adapter._get_underlying_asset(ausdc_address)

        expected_usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        assert underlying.lower() == expected_usdc.lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_underlying_asset_ausdt(self, config):
        """Integration: Verify aUSDT returns USDT underlying address."""
        adapter = AaveV3Adapter(config)

        ausdt_address = AAVE_V3_SUPPLY_TOKENS_MAINNET["USDT"]
        underlying = await adapter._get_underlying_asset(ausdt_address)

        expected_usdt = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
        assert underlying.lower() == expected_usdt.lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_underlying_asset_ausde(self, config):
        """Integration: Verify aUSDe returns USDe underlying address."""
        adapter = AaveV3Adapter(config)

        ausde_address = AAVE_V3_SUPPLY_TOKENS_MAINNET["USDe"]
        underlying = await adapter._get_underlying_asset(ausde_address)

        expected_usde = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"
        assert underlying.lower() == expected_usde.lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_underlying_debt_usdc(self, config):
        """Integration: Verify variable debt USDC returns USDC underlying."""
        adapter = AaveV3Adapter(config)

        debt_usdc_address = AAVE_V3_BORROW_TOKENS_MAINNET["USDC"]
        underlying = await adapter._get_underlying_asset(debt_usdc_address)

        expected_usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        assert underlying.lower() == expected_usdc.lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_balance_of_returns_integer(self, config, subvault_address):
        """Integration: Verify _balance_of returns an integer."""
        adapter = AaveV3Adapter(config)

        ausdc_address = AAVE_V3_SUPPLY_TOKENS_MAINNET["USDC"]
        balance = await adapter._balance_of(ausdc_address, subvault_address)

        assert isinstance(balance, int)
        assert balance >= 0


# =============================================================================
# Multi-Instance Config Tests (Aave + Spark)
# =============================================================================


class TestMultiInstanceConfig:
    """Tests for multi-instance Aave V3 config (Aave + Spark pattern)."""

    @pytest.fixture
    def aave_config(self):
        """Aave V3 mainnet config."""
        return {
            "name": "aave",
            "pool_address": "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2",
            "supply_tokens": {
                "WETH": "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8",
                "USDC": "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
            },
            "borrow_tokens": {
                "WETH": "0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE",
                "USDC": "0x72E95b8931767C79bA4EeE721354d6E99a61D004",
            },
        }

    @pytest.fixture
    def spark_config(self):
        """Spark Protocol config (Aave V3 fork)."""
        return {
            "name": "spark",
            "pool_address": "0xC13e21B648A5Ee794902342038FF3aDAB66BE987",
            "supply_tokens": {
                "WETH": "0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB",
                "DAI": "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B",
                "USDC": "0x377C3bd93f2a2984E1E7bE6A5C22c525eD4A4815",
            },
            "borrow_tokens": {
                "WETH": "0x2e7576042566f8D6990e07A1B61Ad1efd86Ae70d",
                "DAI": "0xf705d2B7e92B3F38e6ae7571e0F1533caDC5f10A",
                "USDC": "0x7B70D04099CB9cfb1Db7B6820baDAfB4C5C70A67",
            },
        }

    def test_single_config_backwards_compatible(self, config):
        """Single Aave config should work as before."""
        from tq_oracle.settings import AdapterSettings, AaveV3AdapterSettings

        settings = AdapterSettings(
            aave_v3=AaveV3AdapterSettings(
                pool_address="0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
            )
        )

        # get_aave_v3_config with no name should return the single config
        result = settings.get_aave_v3_config()
        assert result is not None
        assert result.pool_address == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

    def test_multi_config_list_parsing(self, aave_config, spark_config):
        """Multiple configs should be parsed as a list."""
        from tq_oracle.settings import AdapterSettings, AaveV3AdapterSettings

        settings = AdapterSettings(
            aave_v3=[
                AaveV3AdapterSettings(**aave_config),
                AaveV3AdapterSettings(**spark_config),
            ]
        )

        configs = settings.get_aave_v3_configs()
        assert len(configs) == 2
        assert configs[0].name == "aave"
        assert configs[1].name == "spark"

    def test_get_config_by_name_aave(self, aave_config, spark_config):
        """Should retrieve Aave config by name."""
        from tq_oracle.settings import AdapterSettings, AaveV3AdapterSettings

        settings = AdapterSettings(
            aave_v3=[
                AaveV3AdapterSettings(**aave_config),
                AaveV3AdapterSettings(**spark_config),
            ]
        )

        result = settings.get_aave_v3_config("aave")
        assert result is not None
        assert result.name == "aave"
        assert result.pool_address == "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

    def test_get_config_by_name_spark(self, aave_config, spark_config):
        """Should retrieve Spark config by name."""
        from tq_oracle.settings import AdapterSettings, AaveV3AdapterSettings

        settings = AdapterSettings(
            aave_v3=[
                AaveV3AdapterSettings(**aave_config),
                AaveV3AdapterSettings(**spark_config),
            ]
        )

        result = settings.get_aave_v3_config("spark")
        assert result is not None
        assert result.name == "spark"
        assert result.pool_address == "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"

    def test_get_config_by_name_not_found(self, aave_config, spark_config):
        """Should return None for non-existent config name."""
        from tq_oracle.settings import AdapterSettings, AaveV3AdapterSettings

        settings = AdapterSettings(
            aave_v3=[
                AaveV3AdapterSettings(**aave_config),
                AaveV3AdapterSettings(**spark_config),
            ]
        )

        result = settings.get_aave_v3_config("nonexistent")
        assert result is None

    def test_get_default_config_from_list(self, aave_config, spark_config):
        """No name should return first config in list."""
        from tq_oracle.settings import AdapterSettings, AaveV3AdapterSettings

        settings = AdapterSettings(
            aave_v3=[
                AaveV3AdapterSettings(**aave_config),
                AaveV3AdapterSettings(**spark_config),
            ]
        )

        result = settings.get_aave_v3_config(None)
        assert result is not None
        assert result.name == "aave"  # First in list

    def test_spark_has_different_tokens(self, aave_config, spark_config):
        """Spark config should have different supply/borrow tokens."""
        from tq_oracle.settings import AdapterSettings, AaveV3AdapterSettings

        settings = AdapterSettings(
            aave_v3=[
                AaveV3AdapterSettings(**aave_config),
                AaveV3AdapterSettings(**spark_config),
            ]
        )

        aave = settings.get_aave_v3_config("aave")
        spark = settings.get_aave_v3_config("spark")

        assert aave is not None
        assert spark is not None

        # Pool addresses should be different
        assert aave.pool_address != spark.pool_address

        # Supply tokens should be different (different aToken addresses)
        assert aave.supply_tokens["WETH"] != spark.supply_tokens["WETH"]

        # Borrow tokens should be different
        assert aave.borrow_tokens["WETH"] != spark.borrow_tokens["WETH"]

    def test_spark_has_dai_token(self, spark_config):
        """Spark config should include DAI tokens (not in Aave config)."""
        from tq_oracle.settings import AaveV3AdapterSettings

        spark = AaveV3AdapterSettings(**spark_config)

        assert "DAI" in spark.supply_tokens
        assert "DAI" in spark.borrow_tokens

    def test_adapter_with_spark_override(self, config, spark_config):
        """AaveV3Adapter should accept Spark config via overrides."""
        spark_pool = spark_config["pool_address"]
        spark_supply = spark_config["supply_tokens"]
        spark_borrow = spark_config["borrow_tokens"]

        adapter = AaveV3Adapter(
            config,
            pool_address=spark_pool,
            supply_tokens=spark_supply,
            borrow_tokens=spark_borrow,
        )

        assert adapter.pool_address == spark_pool
        assert adapter.supply_tokens == spark_supply
        assert adapter.borrow_tokens == spark_borrow
        assert "DAI" in adapter.supply_tokens


class TestSparkIntegration:
    """Integration tests for Spark Protocol (Aave V3 fork)."""

    @pytest.fixture
    def spark_adapter(self, config):
        """Create adapter configured for Spark Protocol."""
        return AaveV3Adapter(
            config,
            pool_address="0xC13e21B648A5Ee794902342038FF3aDAB66BE987",
            supply_tokens={
                "WETH": "0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB",
                "DAI": "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B",
                "USDC": "0x377C3bd93f2a2984E1E7bE6A5C22c525eD4A4815",
            },
            borrow_tokens={
                "WETH": "0x2e7576042566f8D6990e07A1B61Ad1efd86Ae70d",
                "DAI": "0xf705d2B7e92B3F38e6ae7571e0F1533caDC5f10A",
                "USDC": "0x7B70D04099CB9cfb1Db7B6820baDAfB4C5C70A67",
            },
        )

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spark_get_underlying_sp_weth(self, spark_adapter):
        """Integration: Verify spWETH returns WETH underlying address."""
        sp_weth = "0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB"
        underlying = await spark_adapter._get_underlying_asset(sp_weth)

        expected_weth = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
        assert underlying.lower() == expected_weth.lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spark_get_underlying_sp_dai(self, spark_adapter):
        """Integration: Verify spDAI returns DAI underlying address."""
        sp_dai = "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B"
        underlying = await spark_adapter._get_underlying_asset(sp_dai)

        expected_dai = "0x6B175474E89094C44Da98b954EedeAC495271d0F"
        assert underlying.lower() == expected_dai.lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spark_get_underlying_sp_usdc(self, spark_adapter):
        """Integration: Verify spUSDC returns USDC underlying address."""
        sp_usdc = "0x377C3bd93f2a2984E1E7bE6A5C22c525eD4A4815"
        underlying = await spark_adapter._get_underlying_asset(sp_usdc)

        expected_usdc = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        assert underlying.lower() == expected_usdc.lower()

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_spark_balance_of_returns_integer(
        self, spark_adapter, subvault_address
    ):
        """Integration: Verify balance_of works with Spark tokens."""
        sp_dai = "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B"
        balance = await spark_adapter._balance_of(sp_dai, subvault_address)

        assert isinstance(balance, int)
        assert balance >= 0
