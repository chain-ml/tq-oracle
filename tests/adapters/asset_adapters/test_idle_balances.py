import pytest

from tq_oracle.adapters.asset_adapters.idle_balances import IdleBalancesAdapter
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.settings import OracleSettings
from tq_oracle.constants import ETH_ASSET


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


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_subvault_addresses_integration(config):
    adapter = IdleBalancesAdapter(config)

    subvaults = await adapter._fetch_subvault_addresses()

    expected_subvaults = [
        "0x90c983DC732e65DB6177638f0125914787b8Cb78",
        "0x893aa69FBAA1ee81B536f0FbE3A3453e86290080",
        "0x181cB55f872450D16aE858D532B4e35e50eaA76D",
        "0x9938A09FeA37bA681A1Bd53D33ddDE2dEBEc1dA0",
    ]

    assert len(subvaults) == len(expected_subvaults)
    assert subvaults == expected_subvaults


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_supported_assets_integration(config):
    adapter = IdleBalancesAdapter(config)

    supported_assets = await adapter._fetch_supported_assets()

    expected_assets = [
        "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE",
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0",
        "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "0xdAC17F958D2ee523a2206206994597C13D831ec7",
        "0xdC035D45d973E3EC169d2276DDab16f1e407384F",
        "0xf1C9acDc66974dFB6dEcB12aA385b9cD01190E38",
    ]

    assert len(supported_assets) == len(expected_assets)
    assert supported_assets == expected_assets


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_asset_balance_integration(config):
    adapter = IdleBalancesAdapter(config)

    subvault_address = "0x90c983DC732e65DB6177638f0125914787b8Cb78"

    usdc_address = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
    usdc_asset = await adapter._fetch_asset_balance(
        adapter.w3, subvault_address, usdc_address
    )
    assert isinstance(usdc_asset, AssetData)
    assert usdc_asset.asset_address == usdc_address
    assert isinstance(usdc_asset.amount, int)
    assert usdc_asset.amount >= 0

    weth_address = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    weth_asset = await adapter._fetch_asset_balance(
        adapter.w3, subvault_address, weth_address
    )
    assert isinstance(weth_asset, AssetData)
    assert weth_asset.asset_address == weth_address
    assert isinstance(weth_asset.amount, int)
    assert weth_asset.amount >= 0

    usdt_address = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
    usdt_asset = await adapter._fetch_asset_balance(
        adapter.w3, subvault_address, usdt_address
    )
    assert isinstance(usdt_asset, AssetData)
    assert usdt_asset.asset_address == usdt_address
    assert isinstance(usdt_asset.amount, int)
    assert usdt_asset.amount >= 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_fetch_eth_balance_integration(config):
    adapter = IdleBalancesAdapter(config)

    subvault_address = "0x90c983DC732e65DB6177638f0125914787b8Cb78"

    eth_asset = await adapter._fetch_asset_balance(
        adapter.w3, subvault_address, ETH_ASSET
    )
    assert isinstance(eth_asset, AssetData)
    assert eth_asset.asset_address == ETH_ASSET
    assert isinstance(eth_asset.amount, int)
    assert eth_asset.amount >= 0


def test_merge_supported_assets_includes_configured_tokens(config):
    config.adapters.idle_balances.extra_tokens = {
        "TEST": "0x00000000000000000000000000000000000000AA"
    }
    adapter = IdleBalancesAdapter(config)

    base_asset = adapter.w3.to_checksum_address(
        "0x0000000000000000000000000000000000000001"
    )
    merged = adapter._merge_supported_assets([base_asset])

    assert merged[0] == base_asset
    assert any(
        addr.lower() == "0x00000000000000000000000000000000000000aa" for addr in merged
    )


@pytest.mark.asyncio
async def test_fetch_assets_marks_extra_tokens_tvl_only(config, monkeypatch):
    config.adapters.idle_balances.extra_tokens = {
        "osETH": "0xf1C9acDc66974dFB6dEcB12aA385b9cD01190E38"
    }
    adapter = IdleBalancesAdapter(config)

    extra_token = next(iter(adapter._additional_assets))
    base_token = adapter.eth_address

    async def fake_fetch_supported_assets():
        return adapter._merge_supported_assets([base_token])

    async def fake_fetch_asset_balance(_w3, _subvault, asset_address, tvl_only=False):
        return AssetData(asset_address=asset_address, amount=1, tvl_only=tvl_only)

    monkeypatch.setattr(adapter, "_fetch_supported_assets", fake_fetch_supported_assets)
    monkeypatch.setattr(adapter, "_fetch_asset_balance", fake_fetch_asset_balance)

    assets = await adapter.fetch_assets("0xSubvault")

    report_flags = {asset.asset_address: asset.tvl_only for asset in assets}
    assert report_flags[extra_token] is True
    assert report_flags[base_token] is False


@pytest.mark.asyncio
async def test_default_additional_tokens_tvl_only(config, monkeypatch):
    # osETH comes from DEFAULT_ADDITIONAL_ASSETS for mainnet
    adapter = IdleBalancesAdapter(config)

    default_additional_token = adapter._default_additional_assets[0]
    base_token = adapter.eth_address

    async def fake_fetch_supported_assets():
        return adapter._merge_supported_assets([base_token])

    async def fake_fetch_asset_balance(_w3, _subvault, asset_address, tvl_only=False):
        return AssetData(asset_address=asset_address, amount=1, tvl_only=tvl_only)

    monkeypatch.setattr(adapter, "_fetch_supported_assets", fake_fetch_supported_assets)
    monkeypatch.setattr(adapter, "_fetch_asset_balance", fake_fetch_asset_balance)

    assets = await adapter.fetch_assets("0xSubvault")

    report_flags = {asset.asset_address: asset.tvl_only for asset in assets}
    assert report_flags[default_additional_token] is True
    assert report_flags[base_token] is False


@pytest.mark.asyncio
async def test_fetch_all_assets_includes_extra_addresses(config, monkeypatch):
    extra_address = "0x0000000000000000000000000000000000000009"
    config.adapters.idle_balances.extra_addresses = [extra_address]
    # Skip validation since test address doesn't have .subvault()/.oracle() methods
    config.adapters.idle_balances.skip_extra_address_validation = True
    adapter = IdleBalancesAdapter(config)

    async def fake_fetch_subvault_addresses():
        return ["0x00000000000000000000000000000000000000AA"]

    recorded: list[str] = []

    async def fake_fetch_assets(address):
        recorded.append(adapter.w3.to_checksum_address(address))
        return [AssetData(asset_address="0xToken", amount=1)]

    monkeypatch.setattr(
        adapter,
        "_fetch_subvault_addresses",
        fake_fetch_subvault_addresses,
    )
    monkeypatch.setattr(adapter, "fetch_assets", fake_fetch_assets)

    await adapter.fetch_all_assets()

    expected = {
        adapter.w3.to_checksum_address(config.vault_address_required),
        adapter.w3.to_checksum_address("0x00000000000000000000000000000000000000AA"),
        adapter.w3.to_checksum_address(extra_address),
    }
    assert set(recorded) == expected


@pytest.mark.asyncio
async def test_extra_address_validation_fails_on_mismatched_subvault(
    config, monkeypatch
):
    """Test that validation fails when extra_address returns subvault not in list."""
    extra_address = "0x0000000000000000000000000000000000000009"
    config.adapters.idle_balances.extra_addresses = [extra_address]
    adapter = IdleBalancesAdapter(config)

    subvault_addresses = ["0x00000000000000000000000000000000000000AA"]

    # Mock _rpc to return subvault not in list
    async def fake_rpc(fn, *args, **kwargs):
        fn_name = str(fn)
        if "subvault" in fn_name:
            return "0x00000000000000000000000000000000000000DD"  # Not in subvault list
        return await fn(*args, **kwargs)

    monkeypatch.setattr(adapter, "_rpc", fake_rpc)

    with pytest.raises(ValueError, match=r"which is not in auto-discovered subvaults"):
        await adapter._validate_extra_addresses(subvault_addresses)


@pytest.mark.asyncio
async def test_extra_address_validation_passes_when_valid(config, monkeypatch):
    """Test that validation passes when extra_address returns correct subvault."""
    extra_address = "0x0000000000000000000000000000000000000009"
    config.adapters.idle_balances.extra_addresses = [extra_address]
    adapter = IdleBalancesAdapter(config)

    subvault_addresses = ["0x00000000000000000000000000000000000000AA"]

    # Mock _rpc to return correct subvault
    async def fake_rpc(fn, *args, **kwargs):
        fn_name = str(fn)
        if "subvault" in fn_name:
            return subvault_addresses[0]  # Valid subvault
        return await fn(*args, **kwargs)

    monkeypatch.setattr(adapter, "_rpc", fake_rpc)

    # Should not raise
    await adapter._validate_extra_addresses(subvault_addresses)


@pytest.mark.asyncio
async def test_extra_address_validation_skipped_when_flag_set(config):
    """Test that validation is skipped when skip_extra_address_validation is True."""
    extra_address = "0x0000000000000000000000000000000000000009"
    config.adapters.idle_balances.extra_addresses = [extra_address]
    config.adapters.idle_balances.skip_extra_address_validation = True
    adapter = IdleBalancesAdapter(config)

    # Should not make any RPC calls, just return
    await adapter._validate_extra_addresses(
        ["0x00000000000000000000000000000000000000AA"],
    )


@pytest.mark.asyncio
async def test_fetch_all_assets_fails_if_any_vault_fails(config, monkeypatch):
    adapter = IdleBalancesAdapter(config)

    subvault_addr = "0x00000000000000000000000000000000000000AA"
    failed_vault_addr = "0x00000000000000000000000000000000000000BB"

    async def fake_fetch_subvault_addresses():
        return [subvault_addr, failed_vault_addr]

    async def fake_fetch_assets(address):
        if adapter.w3.to_checksum_address(address).lower() == failed_vault_addr.lower():
            raise ConnectionError("RPC connection failed")
        return [AssetData(asset_address="0xToken", amount=1)]

    monkeypatch.setattr(
        adapter,
        "_fetch_subvault_addresses",
        fake_fetch_subvault_addresses,
    )
    monkeypatch.setattr(adapter, "fetch_assets", fake_fetch_assets)

    with pytest.raises(ValueError, match=r"Failed to fetch assets from 1 vault\(s\)"):
        await adapter.fetch_all_assets()


def test_additional_assets_can_be_disabled(config):
    config.additional_asset_support = False
    config.adapters.idle_balances.extra_tokens = {
        "TEST": "0x00000000000000000000000000000000000000AA"
    }
    config.adapters.idle_balances.extra_addresses = [
        "0x00000000000000000000000000000000000000BB"
    ]

    adapter = IdleBalancesAdapter(config)

    base_asset = adapter.w3.to_checksum_address(
        "0x0000000000000000000000000000000000000001"
    )
    merged = adapter._merge_supported_assets([base_asset])

    assert adapter._default_additional_assets == []
    assert adapter._extra_additional_assets_by_symbol == {}
    # extra_addresses is decoupled from additional_asset_support
    assert adapter._extra_addresses == [
        adapter.w3.to_checksum_address("0x00000000000000000000000000000000000000BB")
    ]
    assert merged == [base_asset]
