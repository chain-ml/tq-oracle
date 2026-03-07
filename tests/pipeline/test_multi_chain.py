"""Tests for multi-chain orchestrator."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tq_oracle.pipeline.multi_chain import (
    collect_multi_chain_assets,
    collect_chain_assets,
    check_bridge_inflight,
    format_multi_chain_report,
    ChainAssetResult,
    MultiChainAssetResult,
    BRIDGE_ADAPTER_REGISTRY,
)
from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.adapters.bridge_adapters.base import (
    BridgeReconciliationResult,
    InFlightTransfer,
)
from tq_oracle.settings import OracleSettings, ChainConfig, BridgeConfig


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
def mainnet_chain_config():
    return ChainConfig(
        name="mainnet",
        network="mainnet",
        vault_address="0x277C6A642564A91ff78b008022D65683cEE5CCC5",
        rpc="https://eth.drpc.org",
        role="primary",
        required=True,
    )


@pytest.fixture
def hypercore_chain_config():
    return ChainConfig(
        name="hypercore",
        network="hypercore",
        api_url="https://api.hyperliquid.xyz",
        role="satellite",
        required=False,
    )


@pytest.fixture
def cctp_bridge_config():
    return BridgeConfig(
        type="cctp",
        source_chain="mainnet",
        dest_chain="hyperevm",
        lookback_blocks=80,
    )


def test_bridge_adapter_registry():
    """Bridge adapter registry should have expected adapters."""
    assert "cctp" in BRIDGE_ADAPTER_REGISTRY
    assert "evm_core" in BRIDGE_ADAPTER_REGISTRY


def test_chain_asset_result_dataclass():
    """ChainAssetResult should store chain results correctly."""
    result = ChainAssetResult(
        chain_name="mainnet",
        chain_config=MagicMock(),
        assets=[AssetData(asset_address="0xToken", amount=100)],
        total_amount=100,
    )

    assert result.chain_name == "mainnet"
    assert len(result.assets) == 1
    assert result.total_amount == 100
    assert result.success is True
    assert result.error is None


def test_multi_chain_asset_result_dataclass():
    """MultiChainAssetResult should aggregate correctly."""
    result = MultiChainAssetResult()

    assert result.gross_tvl == 0
    assert result.inflight_total == 0
    assert result.net_tvl == 0
    assert result.chain_results == []
    assert result.bridge_results == []
    assert result.all_assets == []
    assert result.errors == []


@pytest.mark.asyncio
async def test_collect_chain_assets_hypercore(config, hypercore_chain_config):
    """Should collect assets from HyperCore chain."""
    with patch("tq_oracle.pipeline.multi_chain.HyperCoreAdapter") as mock_adapter_class:
        mock_adapter = MagicMock()
        mock_adapter.fetch_all_assets = AsyncMock(
            return_value=[
                AssetData(asset_address="0xUSDC", amount=10**18),
            ]
        )
        mock_adapter_class.return_value = mock_adapter

        result = await collect_chain_assets(config, hypercore_chain_config)

        assert result.chain_name == "hypercore"
        assert result.success is True
        assert len(result.assets) == 1
        assert result.total_amount == 10**18


@pytest.mark.asyncio
async def test_collect_chain_assets_handles_error(config, hypercore_chain_config):
    """Should handle adapter errors gracefully."""
    with patch("tq_oracle.pipeline.multi_chain.HyperCoreAdapter") as mock_adapter_class:
        mock_adapter = MagicMock()
        mock_adapter.fetch_all_assets = AsyncMock(
            side_effect=ConnectionError("API unavailable")
        )
        mock_adapter_class.return_value = mock_adapter

        result = await collect_chain_assets(config, hypercore_chain_config)

        assert result.success is False
        assert result.error is not None
        assert "API unavailable" in result.error


@pytest.mark.asyncio
async def test_check_bridge_inflight_success(config, cctp_bridge_config):
    """Should check bridge for in-flight transfers."""
    mock_result = BridgeReconciliationResult(
        bridge_type="cctp",
        source_chain="mainnet",
        dest_chain="hyperevm",
        total_inflight_amount=1000000,
        inflight_count=1,
        inflight_transfers=[
            InFlightTransfer(
                amount=1000000,
                token_address="0xUSDC",
                source_chain="mainnet",
                dest_chain="hyperevm",
                recipient="0xRecipient",
            )
        ],
    )

    with patch.dict(
        BRIDGE_ADAPTER_REGISTRY,
        {"cctp": MagicMock(return_value=MagicMock(get_inflight_transfers=AsyncMock(return_value=mock_result)))},
    ):
        result = await check_bridge_inflight(config, cctp_bridge_config)

        assert result.bridge_type == "cctp"
        assert result.inflight_count == 1
        assert result.total_inflight_amount == 1000000


@pytest.mark.asyncio
async def test_check_bridge_inflight_unknown_type(config):
    """Should handle unknown bridge type."""
    unknown_bridge = BridgeConfig(
        type="unknown",
        source_chain="a",
        dest_chain="b",
    )

    result = await check_bridge_inflight(config, unknown_bridge)

    assert result.error is not None
    assert "Unknown bridge type" in result.error


@pytest.mark.asyncio
async def test_collect_multi_chain_assets_no_config(config):
    """Should return empty result if no chains configured."""
    # Config with no chains
    result = await collect_multi_chain_assets(config)

    assert result.gross_tvl == 0
    assert result.net_tvl == 0
    assert result.chain_results == []


@pytest.mark.asyncio
async def test_collect_multi_chain_assets_with_chains(config):
    """Should collect assets from all chains."""
    config.chains = [
        ChainConfig(
            name="hypercore",
            network="hypercore",
            api_url="https://api.hyperliquid.xyz",
            role="satellite",
            required=False,
        )
    ]

    with patch("tq_oracle.pipeline.multi_chain.collect_chain_assets") as mock_collect:
        mock_collect.return_value = ChainAssetResult(
            chain_name="hypercore",
            chain_config=config.chains[0],
            assets=[AssetData(asset_address="0xUSDC", amount=10**18)],
            total_amount=10**18,
        )

        result = await collect_multi_chain_assets(config)

        assert len(result.chain_results) == 1
        assert result.gross_tvl == 10**18
        assert result.net_tvl == 10**18


@pytest.mark.asyncio
async def test_collect_multi_chain_assets_with_bridges(config):
    """Should check bridges and deduct in-flight amounts."""
    config.chains = [
        ChainConfig(
            name="mainnet",
            network="mainnet",
            role="primary",
            required=True,
        )
    ]
    config.bridges = [
        BridgeConfig(
            type="cctp",
            source_chain="mainnet",
            dest_chain="hyperevm",
        )
    ]

    chain_result = ChainAssetResult(
        chain_name="mainnet",
        chain_config=config.chains[0],
        assets=[],
        total_amount=10**18,
    )

    bridge_result = BridgeReconciliationResult(
        bridge_type="cctp",
        source_chain="mainnet",
        dest_chain="hyperevm",
        total_inflight_amount=10**17,  # 0.1 ETH worth
        inflight_count=1,
        inflight_transfers=[],
    )

    with patch("tq_oracle.pipeline.multi_chain.collect_chain_assets", return_value=chain_result), \
         patch("tq_oracle.pipeline.multi_chain.check_bridge_inflight", return_value=bridge_result):

        result = await collect_multi_chain_assets(config)

        assert result.gross_tvl == 10**18
        assert result.inflight_total == 10**17
        assert result.net_tvl == 10**18 - 10**17


@pytest.mark.asyncio
async def test_collect_multi_chain_assets_required_chain_error(config):
    """Should record error for required chain failure."""
    config.chains = [
        ChainConfig(
            name="mainnet",
            network="mainnet",
            role="primary",
            required=True,
        )
    ]

    error_result = ChainAssetResult(
        chain_name="mainnet",
        chain_config=config.chains[0],
        error="RPC connection failed",
        success=False,
    )

    with patch("tq_oracle.pipeline.multi_chain.collect_chain_assets", return_value=error_result):
        result = await collect_multi_chain_assets(config)

        assert len(result.errors) == 1
        assert "mainnet" in result.errors[0]


def test_format_multi_chain_report():
    """Should format results as readable report."""
    chain_config = ChainConfig(
        name="mainnet",
        network="mainnet",
        role="primary",
        required=True,
    )

    result = MultiChainAssetResult(
        chain_results=[
            ChainAssetResult(
                chain_name="mainnet",
                chain_config=chain_config,
                assets=[],
                total_amount=10**18,
            )
        ],
        bridge_results=[
            BridgeReconciliationResult(
                bridge_type="cctp",
                source_chain="mainnet",
                dest_chain="hyperevm",
                total_inflight_amount=10**17,
                inflight_count=1,
                inflight_transfers=[],
            )
        ],
        gross_tvl=10**18,
        inflight_total=10**17,
        net_tvl=10**18 - 10**17,
    )

    report = format_multi_chain_report(result)

    assert "Cross-Chain TVL Report" in report
    assert "mainnet" in report
    assert "PRIMARY" in report
    assert "In-Flight" in report
    assert "Gross TVL" in report
    assert "Net TVL" in report


def test_format_multi_chain_report_with_error():
    """Should include error information in report."""
    chain_config = ChainConfig(
        name="hypercore",
        network="hypercore",
        role="satellite",
        required=False,
    )

    result = MultiChainAssetResult(
        chain_results=[
            ChainAssetResult(
                chain_name="hypercore",
                chain_config=chain_config,
                error="API timeout",
                success=False,
            )
        ],
        gross_tvl=0,
        net_tvl=0,
    )

    report = format_multi_chain_report(result)

    assert "ERROR" in report
    assert "API timeout" in report
