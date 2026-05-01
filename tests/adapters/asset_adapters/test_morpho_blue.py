"""Comprehensive tests for Morpho Blue adapter."""

import pytest

from tq_oracle.adapters.asset_adapters.morpho_blue import (
    MorphoBlueAdapter,
    MarketParams,
    MarketState,
    Position,
    DEFAULT_MORPHO_BLUE,
    WAD,
)
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import Network, OracleSettings


@pytest.fixture
def config():
    """Base mainnet config for Morpho Blue tests."""
    return OracleSettings(
        vault_address="0xVault",
        oracle_helper_address="0xOracleHelper",
        vault_rpc="https://eth.drpc.org",
        block_number=21500000,
        network=Network.MAINNET,
        dry_run=False,
    )


@pytest.fixture
def config_sepolia():
    """Sepolia config - Morpho Blue should skip."""
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
    return "0xf37D9264099Fc67448e56e60BC33095F2b2a95d3"


@pytest.fixture
def usdc_address():
    return "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


@pytest.fixture
def pt_susde_address():
    """PT-sUSDe collateral token."""
    return "0x5C8AA4beB7C58BD39D7E8Bca8eE7Dd5157aF7D4b"


@pytest.fixture
def market_id():
    """Sample market ID (bytes32)."""
    return "0xb8fc70e82bc5bb53e773626fcc6a23f7eefa036918d7ef216ecfb1950a94a85e"


@pytest.fixture
def sample_market_params(usdc_address, pt_susde_address):
    """Sample MarketParams for testing."""
    return MarketParams(
        loan_token=usdc_address,
        collateral_token=pt_susde_address,
        oracle="0xOracleAddress",
        irm="0xIRMAddress",
        lltv=860000000000000000,  # 86% LLTV
    )


@pytest.fixture
def sample_market_state():
    """Sample MarketState for testing."""
    return MarketState(
        total_supply_assets=1000000 * 10**6,  # 1M USDC
        total_supply_shares=1000000 * 10**6,  # 1:1 initially
        total_borrow_assets=500000 * 10**6,  # 500k USDC borrowed
        total_borrow_shares=500000 * 10**6,  # 1:1 initially
        last_update=1700000000,
        fee=100000000000000000,  # 10% fee
    )


@pytest.fixture
def sample_position():
    """Sample Position for testing."""
    return Position(
        supply_shares=100000 * 10**6,  # 100k shares
        borrow_shares=50000 * 10**6,  # 50k shares
        collateral=200 * 10**18,  # 200 PT-sUSDe
    )


class TestMorphoBlueAdapterInit:
    """Tests for adapter initialization."""

    def test_adapter_name(self, config):
        adapter = MorphoBlueAdapter(config)
        assert adapter.adapter_name == "morpho_blue"

    def test_works_on_non_mainnet(self, config_sepolia):
        """Adapter should initialize on non-mainnet networks (no skip)."""
        adapter = MorphoBlueAdapter(config_sepolia)
        assert adapter.adapter_name == "morpho_blue"
        assert adapter.morpho_address == DEFAULT_MORPHO_BLUE

    def test_works_on_mainnet(self, config):
        adapter = MorphoBlueAdapter(config)
        assert adapter.adapter_name == "morpho_blue"

    def test_uses_default_morpho_address(self, config):
        adapter = MorphoBlueAdapter(config)
        assert adapter.morpho_address == DEFAULT_MORPHO_BLUE

    def test_override_morpho_address(self, config):
        custom_address = "0x1111111111111111111111111111111111111111"
        adapter = MorphoBlueAdapter(config, morpho_address=custom_address)
        assert adapter.morpho_address == custom_address

    def test_empty_markets_by_default(self, config):
        adapter = MorphoBlueAdapter(config)
        assert adapter.markets == {}

    def test_override_markets(self, config, market_id):
        markets = {"test_market": {"market_id": market_id}}
        adapter = MorphoBlueAdapter(config, markets=markets)
        assert adapter.markets == markets

    def test_stores_block_number(self, config):
        adapter = MorphoBlueAdapter(config)
        assert adapter.block_number == 21500000

    def test_validates_duplicate_market_ids(self, config, market_id):
        """Should raise error if same market_id appears twice."""
        markets = {
            "market1": {"market_id": market_id},
            "market2": {"market_id": market_id},
        }
        with pytest.raises(ValueError, match="Duplicate market_id"):
            MorphoBlueAdapter(config, markets=markets)

    def test_validates_missing_market_id(self, config):
        """Should raise error if market_id is missing."""
        markets = {"market1": {"some_other_key": "value"}}
        with pytest.raises(ValueError, match="missing required 'market_id'"):
            MorphoBlueAdapter(config, markets=markets)


class TestInterestAccrual:
    """Tests for interest accrual calculations."""

    def test_no_accrual_when_no_time_elapsed(self, config, sample_market_state):
        """Interest should not accrue if no time has passed."""
        adapter = MorphoBlueAdapter(config)

        current_timestamp = sample_market_state.last_update  # Same as lastUpdate

        result = adapter._accrue_interest(
            sample_market_state,
            borrow_rate=int(0.05 * 10**18 // (365 * 24 * 3600)),  # 5% APR
            current_timestamp=current_timestamp,
        )

        assert result[0] == sample_market_state.total_supply_assets
        assert result[1] == sample_market_state.total_supply_shares
        assert result[2] == sample_market_state.total_borrow_assets
        assert result[3] == sample_market_state.total_borrow_shares

    def test_no_accrual_when_no_borrows(self, config):
        """Interest should not accrue if there are no borrows."""
        adapter = MorphoBlueAdapter(config)

        market_state = MarketState(
            total_supply_assets=1000000 * 10**6,
            total_supply_shares=1000000 * 10**6,
            total_borrow_assets=0,  # No borrows
            total_borrow_shares=0,
            last_update=1700000000,
            fee=100000000000000000,
        )

        current_timestamp = market_state.last_update + 3600  # 1 hour later

        result = adapter._accrue_interest(
            market_state,
            borrow_rate=int(0.05 * 10**18 // (365 * 24 * 3600)),
            current_timestamp=current_timestamp,
        )

        assert result[0] == market_state.total_supply_assets
        assert result[2] == market_state.total_borrow_assets

    def test_accrual_increases_totals(self, config, sample_market_state):
        """Interest accrual should increase both supply and borrow totals."""
        adapter = MorphoBlueAdapter(config)

        # 5% APR = 5e16 / (365 * 24 * 3600) per second
        borrow_rate = int(0.05 * 10**18 // (365 * 24 * 3600))
        elapsed = 3600  # 1 hour
        current_timestamp = sample_market_state.last_update + elapsed

        result = adapter._accrue_interest(
            sample_market_state,
            borrow_rate=borrow_rate,
            current_timestamp=current_timestamp,
        )

        new_supply_assets, new_supply_shares, new_borrow_assets, new_borrow_shares = (
            result
        )

        # Both should increase
        assert new_supply_assets > sample_market_state.total_supply_assets
        assert new_borrow_assets > sample_market_state.total_borrow_assets

        # Borrow shares should not change (only assets accrue)
        assert new_borrow_shares == sample_market_state.total_borrow_shares

    def test_accrual_is_taylor_compounded(self, config, sample_market_state):
        """Interest uses 3-term Taylor expansion, so 2h > 2x 1h (compounding)."""
        adapter = MorphoBlueAdapter(config)

        borrow_rate = int(0.05 * 10**18 // (365 * 24 * 3600))

        # 1 hour
        result_1h = adapter._accrue_interest(
            sample_market_state,
            borrow_rate=borrow_rate,
            current_timestamp=sample_market_state.last_update + 3600,
        )

        # 2 hours
        result_2h = adapter._accrue_interest(
            sample_market_state,
            borrow_rate=borrow_rate,
            current_timestamp=sample_market_state.last_update + 7200,
        )

        interest_1h = result_1h[2] - sample_market_state.total_borrow_assets
        interest_2h = result_2h[2] - sample_market_state.total_borrow_assets

        # With compounding, 2h interest should be slightly MORE than 2x 1h
        assert interest_2h > 2 * interest_1h
        # But still very close (quadratic term is tiny at normal rates)
        assert abs(interest_2h - 2 * interest_1h) < 100  # Allow for compounding effect


class TestShareToAssetConversion:
    """Tests for share to asset conversion."""

    def test_shares_to_assets_basic(self, config):
        """Basic 1:1 conversion."""
        adapter = MorphoBlueAdapter(config)

        result = adapter._shares_to_assets(
            shares=100 * 10**6,
            total_assets=1000 * 10**6,
            total_shares=1000 * 10**6,
        )

        assert result == 100 * 10**6

    def test_shares_to_assets_with_interest(self, config):
        """Conversion when total assets > total shares (interest accrued)."""
        adapter = MorphoBlueAdapter(config)

        # After interest: 1000 assets, 900 shares -> each share worth 1.111 assets
        result = adapter._shares_to_assets(
            shares=90 * 10**6,
            total_assets=1000 * 10**6,
            total_shares=900 * 10**6,
        )

        # 90 shares * (1000/900) = 100 assets
        assert result == 100 * 10**6

    def test_shares_to_assets_zero_shares(self, config):
        """Zero shares should return zero assets."""
        adapter = MorphoBlueAdapter(config)

        result = adapter._shares_to_assets(
            shares=0,
            total_assets=1000 * 10**6,
            total_shares=1000 * 10**6,
        )

        assert result == 0

    def test_shares_to_assets_zero_total_shares(self, config):
        """Zero total shares should return zero (avoid division by zero)."""
        adapter = MorphoBlueAdapter(config)

        result = adapter._shares_to_assets(
            shares=100 * 10**6,
            total_assets=1000 * 10**6,
            total_shares=0,
        )

        assert result == 0


class TestFetchAssets:
    """Tests for fetch_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_markets(self, config, subvault_address):
        """No configured markets should return empty list."""
        adapter = MorphoBlueAdapter(config)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_non_mainnet_no_markets(
        self, config_sepolia, subvault_address
    ):
        """Non-mainnet adapter with no markets should return empty list."""
        adapter = MorphoBlueAdapter(config_sepolia)
        result = await adapter.fetch_assets(subvault_address)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_asset_data_list(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
        sample_market_params,
        sample_market_state,
        sample_position,
    ):
        """Should return list of AssetData."""
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        # Mock all RPC calls
        mocker.patch.object(
            adapter, "_get_market_params", return_value=sample_market_params
        )
        mocker.patch.object(
            adapter, "_get_market_state", return_value=sample_market_state
        )
        mocker.patch.object(adapter, "_get_position", return_value=sample_position)
        mocker.patch.object(adapter, "_get_block_timestamp", return_value=1700003600)
        mocker.patch.object(
            adapter, "_get_borrow_rate", return_value=int(0.05 * 10**18 // 31536000)
        )

        result = await adapter.fetch_assets(subvault_address)

        assert isinstance(result, list)
        assert all(isinstance(item, AssetData) for item in result)

    @pytest.mark.asyncio
    async def test_supply_positions_are_positive(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
        usdc_address,
    ):
        """Supply positions should be positive amounts."""
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        market_params = MarketParams(
            loan_token=usdc_address,
            collateral_token="0xCollateral",
            oracle="0xOracle",
            irm="0xIRM",
            lltv=860000000000000000,
        )

        market_state = MarketState(
            total_supply_assets=1000000 * 10**6,
            total_supply_shares=1000000 * 10**6,
            total_borrow_assets=0,
            total_borrow_shares=0,
            last_update=1700000000,
            fee=0,
        )

        position = Position(
            supply_shares=100000 * 10**6,
            borrow_shares=0,
            collateral=0,
        )

        mocker.patch.object(adapter, "_get_market_params", return_value=market_params)
        mocker.patch.object(adapter, "_get_market_state", return_value=market_state)
        mocker.patch.object(adapter, "_get_position", return_value=position)
        mocker.patch.object(adapter, "_get_block_timestamp", return_value=1700000000)
        mocker.patch.object(adapter, "_get_borrow_rate", return_value=0)

        result = await adapter.fetch_assets(subvault_address)

        # Should have one supply position
        supply_results = [r for r in result if r.amount > 0]
        assert len(supply_results) == 1
        assert supply_results[0].amount == 100000 * 10**6

    @pytest.mark.asyncio
    async def test_borrow_positions_are_negative(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
        usdc_address,
    ):
        """Borrow positions should be negative amounts."""
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        market_params = MarketParams(
            loan_token=usdc_address,
            collateral_token="0xCollateral",
            oracle="0xOracle",
            irm="0xIRM",
            lltv=860000000000000000,
        )

        market_state = MarketState(
            total_supply_assets=1000000 * 10**6,
            total_supply_shares=1000000 * 10**6,
            total_borrow_assets=500000 * 10**6,
            total_borrow_shares=500000 * 10**6,
            last_update=1700000000,
            fee=0,
        )

        position = Position(
            supply_shares=0,
            borrow_shares=50000 * 10**6,
            collateral=0,
        )

        mocker.patch.object(adapter, "_get_market_params", return_value=market_params)
        mocker.patch.object(adapter, "_get_market_state", return_value=market_state)
        mocker.patch.object(adapter, "_get_position", return_value=position)
        mocker.patch.object(adapter, "_get_block_timestamp", return_value=1700000000)
        mocker.patch.object(adapter, "_get_borrow_rate", return_value=0)

        result = await adapter.fetch_assets(subvault_address)

        # Should have one borrow position (negative)
        borrow_results = [r for r in result if r.amount < 0]
        assert len(borrow_results) == 1
        assert borrow_results[0].amount == -50000 * 10**6

    @pytest.mark.asyncio
    async def test_collateral_is_raw_amount(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
        pt_susde_address,
    ):
        """Collateral should be returned as raw amount (no share conversion)."""
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        market_params = MarketParams(
            loan_token="0xLoanToken",
            collateral_token=pt_susde_address,
            oracle="0xOracle",
            irm="0xIRM",
            lltv=860000000000000000,
        )

        market_state = MarketState(
            total_supply_assets=0,
            total_supply_shares=0,
            total_borrow_assets=0,
            total_borrow_shares=0,
            last_update=1700000000,
            fee=0,
        )

        collateral_amount = 200 * 10**18
        position = Position(
            supply_shares=0,
            borrow_shares=0,
            collateral=collateral_amount,
        )

        mocker.patch.object(adapter, "_get_market_params", return_value=market_params)
        mocker.patch.object(adapter, "_get_market_state", return_value=market_state)
        mocker.patch.object(adapter, "_get_position", return_value=position)
        mocker.patch.object(adapter, "_get_block_timestamp", return_value=1700000000)

        result = await adapter.fetch_assets(subvault_address)

        assert len(result) == 1
        assert result[0].asset_address.lower() == pt_susde_address.lower()
        assert result[0].amount == collateral_amount

    @pytest.mark.asyncio
    async def test_empty_position_returns_empty(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
    ):
        """Position with all zeros should return empty list."""
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        market_params = MarketParams(
            loan_token="0xLoanToken",
            collateral_token="0xCollateral",
            oracle="0xOracle",
            irm="0xIRM",
            lltv=860000000000000000,
        )

        market_state = MarketState(
            total_supply_assets=1000000 * 10**6,
            total_supply_shares=1000000 * 10**6,
            total_borrow_assets=0,
            total_borrow_shares=0,
            last_update=1700000000,
            fee=0,
        )

        empty_position = Position(
            supply_shares=0,
            borrow_shares=0,
            collateral=0,
        )

        mocker.patch.object(adapter, "_get_market_params", return_value=market_params)
        mocker.patch.object(adapter, "_get_market_state", return_value=market_state)
        mocker.patch.object(adapter, "_get_position", return_value=empty_position)

        result = await adapter.fetch_assets(subvault_address)

        assert result == []

    @pytest.mark.asyncio
    async def test_merges_previous_assets(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
        usdc_address,
    ):
        """Should merge previous adapter assets when chaining adapters."""
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        market_params = MarketParams(
            loan_token=usdc_address,
            collateral_token="0xCollateral",
            oracle="0xOracle",
            irm="0xIRM",
            lltv=860000000000000000,
        )

        market_state = MarketState(
            total_supply_assets=1000000 * 10**6,
            total_supply_shares=1000000 * 10**6,
            total_borrow_assets=0,
            total_borrow_shares=0,
            last_update=1700000000,
            fee=0,
        )

        position = Position(
            supply_shares=100000 * 10**6,
            borrow_shares=0,
            collateral=0,
        )

        mocker.patch.object(adapter, "_get_market_params", return_value=market_params)
        mocker.patch.object(adapter, "_get_market_state", return_value=market_state)
        mocker.patch.object(adapter, "_get_position", return_value=position)
        mocker.patch.object(adapter, "_get_block_timestamp", return_value=1700000000)
        mocker.patch.object(adapter, "_get_borrow_rate", return_value=0)

        # Previous assets from another adapter
        previous_assets = [
            AssetData(asset_address="0xPreviousToken", amount=500000),
        ]

        result = await adapter.fetch_assets(subvault_address, previous_assets)

        # Should have previous + new
        assert len(result) == 2

        previous_result = next(
            r for r in result if r.asset_address == "0xPreviousToken"
        )
        assert previous_result.amount == 500000


class TestAddressChecksumming:
    """Tests for address handling."""

    @pytest.mark.asyncio
    async def test_returns_checksummed_addresses(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
    ):
        """Returned asset addresses should be checksummed."""
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        # Use lowercase address
        lowercase_loan_token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

        market_params = MarketParams(
            loan_token=lowercase_loan_token,
            collateral_token="0xCollateral",
            oracle="0xOracle",
            irm="0xIRM",
            lltv=860000000000000000,
        )

        market_state = MarketState(
            total_supply_assets=1000000 * 10**6,
            total_supply_shares=1000000 * 10**6,
            total_borrow_assets=0,
            total_borrow_shares=0,
            last_update=1700000000,
            fee=0,
        )

        position = Position(
            supply_shares=100000 * 10**6,
            borrow_shares=0,
            collateral=0,
        )

        mocker.patch.object(adapter, "_get_market_params", return_value=market_params)
        mocker.patch.object(adapter, "_get_market_state", return_value=market_state)
        mocker.patch.object(adapter, "_get_position", return_value=position)
        mocker.patch.object(adapter, "_get_block_timestamp", return_value=1700000000)
        mocker.patch.object(adapter, "_get_borrow_rate", return_value=0)

        result = await adapter.fetch_assets(subvault_address)

        # Should be checksummed
        expected_checksummed = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
        assert result[0].asset_address == expected_checksummed


class TestFetchAllAssets:
    """Tests for fetch_all_assets method."""

    @pytest.mark.asyncio
    async def test_returns_empty_list(self, config):
        """fetch_all_assets should return empty list (not supported)."""
        adapter = MorphoBlueAdapter(config)
        result = await adapter.fetch_all_assets()
        assert result == []


class TestNetValueCalculation:
    """Tests verifying net value when combining supply and borrow."""

    @pytest.mark.asyncio
    async def test_net_position_same_loan_token(
        self,
        mocker,
        config,
        subvault_address,
        market_id,
        usdc_address,
    ):
        """
        When supplying and borrowing same loan token, amounts should net out.
        Results should show both separately (not netted by adapter).
        """
        adapter = MorphoBlueAdapter(
            config, markets={"test": {"market_id": market_id}}
        )

        market_params = MarketParams(
            loan_token=usdc_address,
            collateral_token="0xCollateral",
            oracle="0xOracle",
            irm="0xIRM",
            lltv=860000000000000000,
        )

        market_state = MarketState(
            total_supply_assets=1000000 * 10**6,
            total_supply_shares=1000000 * 10**6,
            total_borrow_assets=500000 * 10**6,
            total_borrow_shares=500000 * 10**6,
            last_update=1700000000,
            fee=0,
        )

        # Supply 1000, borrow 400
        position = Position(
            supply_shares=1000 * 10**6,
            borrow_shares=400 * 10**6,
            collateral=0,
        )

        mocker.patch.object(adapter, "_get_market_params", return_value=market_params)
        mocker.patch.object(adapter, "_get_market_state", return_value=market_state)
        mocker.patch.object(adapter, "_get_position", return_value=position)
        mocker.patch.object(adapter, "_get_block_timestamp", return_value=1700000000)
        mocker.patch.object(adapter, "_get_borrow_rate", return_value=0)

        result = await adapter.fetch_assets(subvault_address)

        # Should have 2 entries (supply and borrow separately)
        assert len(result) == 2

        amounts = [r.amount for r in result]
        assert 1000 * 10**6 in amounts  # Supply (positive)
        assert -400 * 10**6 in amounts  # Borrow (negative)

        # Net should be 600 USDC when aggregated
        net = sum(amounts)
        assert net == 600 * 10**6


class TestSettingsIntegration:
    """Tests for settings/config integration."""

    def test_morpho_blue_settings_default(self):
        """MorphoBlueAdapterSettings should have correct defaults."""
        from tq_oracle.settings import MorphoBlueAdapterSettings

        settings = MorphoBlueAdapterSettings()
        assert settings.morpho_address is None
        assert settings.markets == {}

    def test_morpho_blue_settings_with_markets(self, market_id):
        """MorphoBlueAdapterSettings should accept markets."""
        from tq_oracle.settings import MorphoBlueAdapterSettings

        settings = MorphoBlueAdapterSettings(
            morpho_address="0xCustomMorpho",
            markets={"test_market": {"market_id": market_id}},
        )

        assert settings.morpho_address == "0xCustomMorpho"
        assert "test_market" in settings.markets

    def test_adapter_settings_includes_morpho_blue(self):
        """AdapterSettings should include morpho_blue."""
        from tq_oracle.settings import AdapterSettings

        settings = AdapterSettings()
        assert hasattr(settings, "morpho_blue")
        assert settings.morpho_blue.morpho_address is None

    def test_adapter_registry_includes_morpho_blue(self):
        """ADAPTER_REGISTRY should include morpho_blue."""
        from tq_oracle.adapters.asset_adapters import ADAPTER_REGISTRY

        assert "morpho_blue" in ADAPTER_REGISTRY
        assert ADAPTER_REGISTRY["morpho_blue"] == MorphoBlueAdapter


# Integration tests - require real RPC
class TestMorphoBlueIntegration:
    """Integration tests with real Morpho Blue contracts."""

    @pytest.fixture
    def real_market_id(self):
        """Real PT-sUSDe/USDC market ID on mainnet."""
        return "0xb8fc70e82bc5bb53e773626fcc6a23f7eefa036918d7ef216ecfb1950a94a85e"

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_market_params(self, config, real_market_id):
        """Integration: Fetch market params for a real market."""
        adapter = MorphoBlueAdapter(config)

        market_id_bytes = bytes.fromhex(real_market_id.replace("0x", ""))
        params = await adapter._get_market_params(market_id_bytes)

        assert isinstance(params, MarketParams)
        assert params.loan_token is not None
        assert params.collateral_token is not None
        assert params.lltv > 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_market_state(self, config, real_market_id):
        """Integration: Fetch market state for a real market."""
        adapter = MorphoBlueAdapter(config)

        market_id_bytes = bytes.fromhex(real_market_id.replace("0x", ""))
        state = await adapter._get_market_state(market_id_bytes)

        assert isinstance(state, MarketState)
        assert state.last_update > 0

    @pytest.mark.asyncio
    @pytest.mark.integration
    async def test_get_position(self, config, real_market_id, subvault_address):
        """Integration: Fetch position for a real market and address."""
        adapter = MorphoBlueAdapter(config)

        market_id_bytes = bytes.fromhex(real_market_id.replace("0x", ""))
        position = await adapter._get_position(market_id_bytes, subvault_address)

        assert isinstance(position, Position)
        # Position values can be 0 if address has no position
        assert position.supply_shares >= 0
        assert position.borrow_shares >= 0
        assert position.collateral >= 0
