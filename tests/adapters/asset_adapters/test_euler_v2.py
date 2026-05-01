"""Tests for Euler V2 adapter."""

import pytest

from tq_oracle.adapters.asset_adapters.euler_v2 import EulerV2Adapter
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import Network, OracleSettings


USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
WETH_ADDRESS = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
EUSDC_VAULT = "0x797DD80692c3b2dAdabCe8e30C07fDE5307D48a9"
EWETH_VAULT = "0xD8b27CF359b7D15710a5BE299AF6e7Bf904984C2"


@pytest.fixture
def config():
    """Base mainnet config for Euler V2 tests."""
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
    """Sepolia config - adapter should skip."""
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


class TestEulerV2AdapterInit:
    """Tests for adapter initialization."""

    def test_adapter_name(self, config):
        adapter = EulerV2Adapter(config)
        assert adapter.adapter_name == "euler_v2"

    def test_works_on_non_mainnet(self, config_sepolia):
        """Adapter should initialize on non-mainnet networks (no skip)."""
        adapter = EulerV2Adapter(config_sepolia)
        assert adapter.adapter_name == "euler_v2"

    def test_works_on_mainnet(self, config):
        adapter = EulerV2Adapter(config)
        assert adapter.adapter_name == "euler_v2"

    def test_default_empty_supply_vaults(self, config):
        adapter = EulerV2Adapter(config)
        assert adapter.supply_vaults == {}

    def test_default_empty_borrow_vaults(self, config):
        adapter = EulerV2Adapter(config)
        assert adapter.borrow_vaults == {}

    def test_override_supply_vaults(self, config):
        vaults = {"eUSDC": EUSDC_VAULT}
        adapter = EulerV2Adapter(config, supply_vaults=vaults)
        assert adapter.supply_vaults == vaults

    def test_override_borrow_vaults(self, config):
        vaults = {"eUSDC": EUSDC_VAULT}
        adapter = EulerV2Adapter(config, borrow_vaults=vaults)
        assert adapter.borrow_vaults == vaults

    def test_stores_block_number(self, config):
        adapter = EulerV2Adapter(config)
        assert adapter.block_number == 23690139

    def test_validates_duplicate_supply_vaults(self, config):
        with pytest.raises(ValueError, match="Duplicate supply vault"):
            EulerV2Adapter(
                config,
                supply_vaults={
                    "eUSDC": EUSDC_VAULT,
                    "eUSDC_dup": EUSDC_VAULT,
                },
            )

    def test_validates_duplicate_borrow_vaults(self, config):
        with pytest.raises(ValueError, match="Duplicate borrow vault"):
            EulerV2Adapter(
                config,
                borrow_vaults={
                    "eUSDC": EUSDC_VAULT,
                    "eUSDC_dup": EUSDC_VAULT,
                },
            )

    def test_same_vault_in_supply_and_borrow_is_valid(self, config):
        """Same vault address in both supply and borrow is allowed."""
        adapter = EulerV2Adapter(
            config,
            supply_vaults={"eUSDC": EUSDC_VAULT},
            borrow_vaults={"eUSDC": EUSDC_VAULT},
        )
        assert EUSDC_VAULT in adapter.supply_vaults.values()
        assert EUSDC_VAULT in adapter.borrow_vaults.values()

    def test_config_from_settings(self, config):
        """Test loading config from adapter settings."""
        config.adapters.euler_v2.supply_vaults = {"eUSDC": EUSDC_VAULT}
        config.adapters.euler_v2.borrow_vaults = {"eUSDC": EUSDC_VAULT}
        adapter = EulerV2Adapter(config)
        assert adapter.supply_vaults == {"eUSDC": EUSDC_VAULT}
        assert adapter.borrow_vaults == {"eUSDC": EUSDC_VAULT}

    def test_overrides_take_precedence(self, config):
        """Overrides should take precedence over config."""
        config.adapters.euler_v2.supply_vaults = {"eUSDC": EUSDC_VAULT}
        override_vaults = {"eWETH": EWETH_VAULT}
        adapter = EulerV2Adapter(config, supply_vaults=override_vaults)
        assert adapter.supply_vaults == override_vaults


class TestFetchAssets:
    """Tests for fetch_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_vaults(self, config, subvault_address):
        """Adapter with no configured vaults should return empty list."""
        adapter = EulerV2Adapter(config)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_mainnet_no_config(
        self, config_sepolia, subvault_address
    ):
        """Non-mainnet adapter with no configured vaults should return empty list."""
        adapter = EulerV2Adapter(config_sepolia)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_supply_positions_are_positive(
        self, mocker, config, subvault_address
    ):
        """Supply positions should be positive amounts."""
        adapter = EulerV2Adapter(
            config, supply_vaults={"eUSDC": EUSDC_VAULT}, borrow_vaults={}
        )

        mocker.patch.object(adapter, "_balance_of", return_value=1000000)
        mocker.patch.object(adapter, "_convert_to_assets", return_value=1050000)
        mocker.patch.object(adapter, "_get_vault_asset", return_value=USDC_ADDRESS)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].amount == 1050000
        assert result[0].amount > 0

    @pytest.mark.asyncio
    async def test_borrow_positions_are_negative(
        self, mocker, config, subvault_address
    ):
        """Borrow positions should be negative amounts."""
        adapter = EulerV2Adapter(
            config, supply_vaults={}, borrow_vaults={"eUSDC": EUSDC_VAULT}
        )

        mocker.patch.object(adapter, "_debt_of", return_value=500000)
        mocker.patch.object(adapter, "_get_vault_asset", return_value=USDC_ADDRESS)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].amount == -500000
        assert result[0].amount < 0

    @pytest.mark.asyncio
    async def test_zero_balance_excluded(self, mocker, config, subvault_address):
        """Zero balance positions should not be in results."""
        adapter = EulerV2Adapter(
            config, supply_vaults={"eUSDC": EUSDC_VAULT}, borrow_vaults={}
        )

        mocker.patch.object(adapter, "_balance_of", return_value=0)
        mocker.patch.object(adapter, "_get_vault_asset", return_value=USDC_ADDRESS)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_zero_debt_excluded(self, mocker, config, subvault_address):
        """Zero debt positions should not be in results."""
        adapter = EulerV2Adapter(
            config, supply_vaults={}, borrow_vaults={"eUSDC": EUSDC_VAULT}
        )

        mocker.patch.object(adapter, "_debt_of", return_value=0)
        mocker.patch.object(adapter, "_get_vault_asset", return_value=USDC_ADDRESS)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_returns_underlying_asset_address(
        self, mocker, config, subvault_address
    ):
        """Should return underlying asset address, not vault address."""
        adapter = EulerV2Adapter(
            config, supply_vaults={"eUSDC": EUSDC_VAULT}, borrow_vaults={}
        )

        mocker.patch.object(adapter, "_balance_of", return_value=1000)
        mocker.patch.object(adapter, "_convert_to_assets", return_value=1050)
        mocker.patch.object(adapter, "_get_vault_asset", return_value=USDC_ADDRESS)

        result = await adapter.fetch_assets(subvault_address)

        assert result[0].asset_address == USDC_ADDRESS

    @pytest.mark.asyncio
    async def test_mixed_supply_and_borrow(self, mocker, config, subvault_address):
        """Should handle both supply and borrow positions."""
        adapter = EulerV2Adapter(
            config,
            supply_vaults={"eUSDC": EUSDC_VAULT},
            borrow_vaults={"eWETH": EWETH_VAULT},
        )

        async def mock_balance(vault_addr, account):
            return 1000000 if vault_addr == EUSDC_VAULT else 0

        async def mock_convert(vault_addr, shares):
            return 1050000

        async def mock_debt(vault_addr, account):
            return 500000 if vault_addr == EWETH_VAULT else 0

        async def mock_asset(vault_addr):
            if vault_addr.lower() == EUSDC_VAULT.lower():
                return USDC_ADDRESS
            return WETH_ADDRESS

        mocker.patch.object(adapter, "_balance_of", side_effect=mock_balance)
        mocker.patch.object(adapter, "_convert_to_assets", side_effect=mock_convert)
        mocker.patch.object(adapter, "_debt_of", side_effect=mock_debt)
        mocker.patch.object(adapter, "_get_vault_asset", side_effect=mock_asset)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 2

        usdc_result = next(r for r in result if r.asset_address == USDC_ADDRESS)
        weth_result = next(r for r in result if r.asset_address == WETH_ADDRESS)

        assert usdc_result.amount == 1050000  # Positive (supply)
        assert weth_result.amount == -500000  # Negative (borrow)

    @pytest.mark.asyncio
    async def test_merges_previous_assets(self, mocker, config, subvault_address):
        """Should merge previous adapter assets when chaining."""
        adapter = EulerV2Adapter(
            config, supply_vaults={"eUSDC": EUSDC_VAULT}, borrow_vaults={}
        )

        mocker.patch.object(adapter, "_balance_of", return_value=1000000)
        mocker.patch.object(adapter, "_convert_to_assets", return_value=1050000)
        mocker.patch.object(adapter, "_get_vault_asset", return_value=USDC_ADDRESS)

        previous_assets = [
            AssetData(asset_address=WETH_ADDRESS, amount=2000000),
        ]

        result = await adapter.fetch_assets(subvault_address, previous_assets)

        assert len(result) == 2

        weth_result = next(r for r in result if r.asset_address == WETH_ADDRESS)
        assert weth_result.amount == 2000000

        usdc_result = next(r for r in result if r.asset_address == USDC_ADDRESS)
        assert usdc_result.amount == 1050000

    @pytest.mark.asyncio
    async def test_empty_vaults_config(self, config, subvault_address):
        """Should return empty list when no vaults configured."""
        adapter = EulerV2Adapter(config, supply_vaults={}, borrow_vaults={})
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_supply_vaults(self, mocker, config, subvault_address):
        """Should handle multiple supply vaults."""
        adapter = EulerV2Adapter(
            config,
            supply_vaults={"eUSDC": EUSDC_VAULT, "eWETH": EWETH_VAULT},
            borrow_vaults={},
        )

        balances = [1000000, 2000000000000000000]  # 1 USDC (6d), 2 WETH (18d)
        assets = [USDC_ADDRESS, WETH_ADDRESS]
        converted = [1050000, 2100000000000000000]

        mocker.patch.object(adapter, "_balance_of", side_effect=balances)
        mocker.patch.object(adapter, "_get_vault_asset", side_effect=assets)
        mocker.patch.object(adapter, "_convert_to_assets", side_effect=converted)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 2
        addresses = [r.asset_address for r in result]
        assert USDC_ADDRESS in addresses
        assert WETH_ADDRESS in addresses


class TestFetchAllAssets:
    """Tests for fetch_all_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty(self, config):
        """fetch_all_assets should return empty list."""
        adapter = EulerV2Adapter(config)
        result = await adapter.fetch_all_assets()
        assert result == []


class TestAssetCaching:
    """Tests for vault asset address caching."""

    @pytest.mark.asyncio
    async def test_asset_address_cached(self, mocker, config, subvault_address):
        """Same vault in supply and borrow should only query asset() once."""
        adapter = EulerV2Adapter(
            config,
            supply_vaults={"eUSDC": EUSDC_VAULT},
            borrow_vaults={"eUSDC": EUSDC_VAULT},
        )

        # Pre-populate cache
        adapter._asset_cache[EUSDC_VAULT.lower()] = USDC_ADDRESS

        mocker.patch.object(adapter, "_balance_of", return_value=1000000)
        mocker.patch.object(adapter, "_convert_to_assets", return_value=1050000)
        mocker.patch.object(adapter, "_debt_of", return_value=500000)

        # _get_vault_asset should use cache and not make RPC calls for asset()
        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 2
        # Both should use USDC as the underlying
        for r in result:
            assert r.asset_address == USDC_ADDRESS


class TestSettingsIntegration:
    """Tests for settings integration."""

    def test_euler_v2_in_adapter_settings(self):
        """EulerV2AdapterSettings should be in AdapterSettings."""
        from tq_oracle.settings import AdapterSettings

        settings = AdapterSettings()
        assert hasattr(settings, "euler_v2")
        assert settings.euler_v2.supply_vaults == {}
        assert settings.euler_v2.borrow_vaults == {}

    def test_euler_v2_in_adapter_registry(self):
        """EulerV2Adapter should be in ADAPTER_REGISTRY."""
        from tq_oracle.adapters.asset_adapters import ADAPTER_REGISTRY

        assert "euler_v2" in ADAPTER_REGISTRY
        assert ADAPTER_REGISTRY["euler_v2"] is EulerV2Adapter

    def test_get_adapter_class(self):
        """get_adapter_class should return EulerV2Adapter."""
        from tq_oracle.adapters.asset_adapters import get_adapter_class

        cls = get_adapter_class("euler_v2")
        assert cls is EulerV2Adapter
