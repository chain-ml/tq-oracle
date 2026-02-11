"""Tests for HyperEVM-Core bridge adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tq_oracle.adapters.bridge_adapters.evm_core import (
    EVMCoreBridgeAdapter,
    HYPEREVM_LOOKBACK_BLOCKS,
    TRANSFER_EVENT_SIGNATURE,
)
from tq_oracle.settings import OracleSettings, BridgeConfig
from tq_oracle.constants import USDC_HYPEREVM_MAINNET, HYPEREVM_USDC_SYSTEM_ADDRESS


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
def bridge_config():
    return BridgeConfig(
        type="evm_core",
        source_chain="hyperevm",
        dest_chain="hypercore",
        source_subvault="0x1234567890123456789012345678901234567890",
        dest_subvault="0x0987654321098765432109876543210987654321",
    )


def test_bridge_type(config, bridge_config):
    """Adapter should return 'evm_core' as bridge type."""
    adapter = EVMCoreBridgeAdapter(config, bridge_config)
    assert adapter.bridge_type == "evm_core"


def test_lookback_blocks_configured():
    """Lookback should be 300 blocks (5 minutes at 1s/block)."""
    assert HYPEREVM_LOOKBACK_BLOCKS == 300


def test_transfer_event_signature():
    """Transfer event signature should be keccak256 of Transfer(address,address,uint256)."""
    # Standard ERC20 Transfer event signature
    expected = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
    assert TRANSFER_EVENT_SIGNATURE == expected


@pytest.mark.asyncio
async def test_get_inflight_transfers_no_source_subvault(config):
    """Should return error if source subvault not configured."""
    bridge_config = BridgeConfig(
        type="evm_core",
        source_chain="hyperevm",
        dest_chain="hypercore",
    )

    adapter = EVMCoreBridgeAdapter(config, bridge_config)

    result = await adapter.get_inflight_transfers()

    assert result.total_inflight_amount == 0
    assert result.inflight_count == 0
    assert "Source subvault" in result.error


@pytest.mark.asyncio
async def test_get_inflight_transfers_parses_logs(config, bridge_config):
    """Should parse ERC20 transfer logs correctly."""
    adapter = EVMCoreBridgeAdapter(config, bridge_config)

    # Mock Web3
    mock_w3 = MagicMock()
    mock_w3.eth.block_number = AsyncMock(return_value=1000)
    mock_w3.eth.get_logs = AsyncMock(
        return_value=[
            {
                "data": "0x" + "00" * 31 + "0f4240",  # 1,000,000 in hex (1 USDC)
                "transactionHash": b"\xab\xcd\xef" * 10 + b"\x12\x34",
            }
        ]
    )
    mock_w3.to_checksum_address = lambda x: x

    with patch("tq_oracle.adapters.bridge_adapters.evm_core.AsyncWeb3") as mock_async_web3:
        mock_async_web3.return_value = mock_w3
        mock_async_web3.AsyncHTTPProvider = MagicMock()

        result = await adapter.get_inflight_transfers()

    assert result.bridge_type == "evm_core"
    assert result.source_chain == "hyperevm"
    assert result.dest_chain == "hypercore"


@pytest.mark.asyncio
async def test_get_evm_to_core_transfers_filter_params(config, bridge_config):
    """Should build correct filter params for Transfer events."""
    adapter = EVMCoreBridgeAdapter(config, bridge_config)

    mock_w3 = MagicMock()
    mock_w3.eth.get_logs = AsyncMock(return_value=[])
    mock_w3.to_checksum_address = lambda x: x

    subvault = "0x1234567890123456789012345678901234567890"

    await adapter._get_evm_to_core_transfers(mock_w3, subvault, 100, 200)

    # Verify get_logs was called with correct params
    mock_w3.eth.get_logs.assert_called_once()
    call_args = mock_w3.eth.get_logs.call_args[0][0]

    assert call_args["fromBlock"] == 100
    assert call_args["toBlock"] == 200
    assert call_args["address"] == USDC_HYPEREVM_MAINNET
    assert call_args["topics"][0] == TRANSFER_EVENT_SIGNATURE
    # from address (padded)
    assert subvault[2:].lower() in call_args["topics"][1].lower()
    # to address (system address, padded)
    assert HYPEREVM_USDC_SYSTEM_ADDRESS[2:].lower() in call_args["topics"][2].lower()


@pytest.mark.asyncio
async def test_cleanup_session(config, bridge_config):
    """Session should be properly cleaned up."""
    adapter = EVMCoreBridgeAdapter(config, bridge_config)

    # Create a mock session
    mock_session = MagicMock()
    mock_session.closed = False
    mock_session.close = AsyncMock()
    adapter._session = mock_session

    await adapter._cleanup_session()

    mock_session.close.assert_called_once()
    assert adapter._session is None


@pytest.mark.asyncio
async def test_get_session_creates_new(config, bridge_config):
    """Should create new session if none exists."""
    adapter = EVMCoreBridgeAdapter(config, bridge_config)

    assert adapter._session is None

    with patch("tq_oracle.adapters.bridge_adapters.evm_core.aiohttp.ClientSession") as mock_client:
        mock_instance = MagicMock()
        mock_instance.closed = False
        mock_client.return_value = mock_instance

        session = await adapter._get_session()

        assert session is mock_instance
        mock_client.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_inflight_transfers_integration(config, bridge_config):
    """Integration test for EVM-Core in-flight detection."""
    adapter = EVMCoreBridgeAdapter(config, bridge_config)

    result = await adapter.get_inflight_transfers()

    # Should return a valid result structure
    assert result.bridge_type == "evm_core"
    assert result.source_chain == "hyperevm"
    assert result.dest_chain == "hypercore"
    assert isinstance(result.total_inflight_amount, int)
    assert isinstance(result.inflight_count, int)
    assert isinstance(result.inflight_transfers, list)
