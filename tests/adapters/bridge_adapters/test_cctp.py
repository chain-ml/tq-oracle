"""Tests for CCTP bridge adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tq_oracle.adapters.bridge_adapters.cctp import (
    CCTPBridgeAdapter,
    TransactionIdentity,
    CHAIN_BLOCK_TIMES,
)
from tq_oracle.adapters.bridge_adapters.base import InFlightTransfer
from tq_oracle.settings import OracleSettings, BridgeConfig


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
        type="cctp",
        source_chain="mainnet",
        dest_chain="hyperevm",
        lookback_blocks=80,
        source_subvault="0x1234567890123456789012345678901234567890",
        dest_subvault="0x0987654321098765432109876543210987654321",
    )


def test_transaction_identity_equality():
    """TransactionIdentity should be hashable and comparable."""
    identity1 = TransactionIdentity(amount=1000000, recipient="0xabc")
    identity2 = TransactionIdentity(amount=1000000, recipient="0xabc")
    identity3 = TransactionIdentity(amount=1000000, recipient="0xdef")

    assert identity1 == identity2
    assert identity1 != identity3
    assert hash(identity1) == hash(identity2)


def test_chain_block_times():
    """Chain block times should be configured correctly."""
    assert CHAIN_BLOCK_TIMES["mainnet"] == 12
    assert CHAIN_BLOCK_TIMES["hyperevm"] == 1
    assert CHAIN_BLOCK_TIMES["hypercore"] == 1


def test_bridge_type(config, bridge_config):
    """Adapter should return 'cctp' as bridge type."""
    adapter = CCTPBridgeAdapter(config, bridge_config)
    assert adapter.bridge_type == "cctp"


def test_calculate_scaled_blocks_same_block_time(config, bridge_config):
    """Same block time should return same lookback for both chains."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    source_blocks, dest_blocks = adapter._calculate_scaled_blocks(100, 12, 12)

    assert source_blocks == 100
    assert dest_blocks == 100


def test_calculate_scaled_blocks_different_block_times(config, bridge_config):
    """Different block times should scale proportionally."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    # 80 blocks @ 12s/block = 960s window
    # For 1s/block chain: 960s / 1s = 960 blocks
    source_blocks, dest_blocks = adapter._calculate_scaled_blocks(80, 12, 1)

    # Source stays at 80 (12s blocks)
    # Dest scales to ~960 (1s blocks) but may be capped
    assert source_blocks == 80
    assert dest_blocks > source_blocks  # Faster chain needs more blocks


def test_extract_address_from_bytes32(config, bridge_config):
    """Should extract last 20 bytes as address."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    from web3 import Web3

    w3 = Web3()

    # Bytes32 with address in last 20 bytes
    bytes32_value = bytes.fromhex(
        "000000000000000000000000" + "1234567890abcdef1234567890abcdef12345678"
    )

    result = adapter._extract_address_from_bytes32(w3, bytes32_value)

    assert result.lower() == "0x1234567890abcdef1234567890abcdef12345678"


@pytest.mark.asyncio
async def test_get_inflight_transfers_no_subvaults(config):
    """Should return empty result if subvaults not configured."""
    bridge_config = BridgeConfig(
        type="cctp",
        source_chain="mainnet",
        dest_chain="hyperevm",
    )

    adapter = CCTPBridgeAdapter(config, bridge_config)

    result = await adapter.get_inflight_transfers()

    assert result.total_inflight_amount == 0
    assert result.inflight_count == 0
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_check_direction_finds_inflight(config, bridge_config):
    """Should detect in-flight transfers (deposited but not minted)."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    # Mock Web3 instances
    mock_source_w3 = MagicMock()
    mock_dest_w3 = MagicMock()
    mock_source_w3.to_checksum_address = lambda x: x
    mock_dest_w3.to_checksum_address = lambda x: x

    # Mock deposit event (on source chain)
    deposit_event = {
        "args": {
            "amount": 1000000,  # 1 USDC
            "mintRecipient": bytes.fromhex(
                "000000000000000000000000" + "0987654321098765432109876543210987654321"[2:]
            ),
            "burnToken": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        },
        "transactionHash": b"\x12\x34\x56\x78" * 8,
    }

    # No matching mint event (transfer is in-flight)
    mock_source_messenger = MagicMock()
    mock_source_messenger.events.DepositForBurn.get_logs = AsyncMock(
        return_value=[deposit_event]
    )

    mock_dest_messenger = MagicMock()
    mock_dest_messenger.events.MintAndWithdraw.get_logs = AsyncMock(return_value=[])

    with patch.object(adapter, "_delay", new_callable=AsyncMock):
        inflight = await adapter._check_direction(
            source_w3=mock_source_w3,
            dest_w3=mock_dest_w3,
            source_messenger=mock_source_messenger,
            dest_messenger=mock_dest_messenger,
            source_subvault="0x1234567890123456789012345678901234567890",
            dest_subvault="0x0987654321098765432109876543210987654321",
            source_chain="mainnet",
            dest_chain="hyperevm",
            source_block_time=12,
            dest_block_time=1,
        )

    assert len(inflight) == 1
    assert inflight[0].amount == 1000000
    assert inflight[0].source_chain == "mainnet"
    assert inflight[0].dest_chain == "hyperevm"


@pytest.mark.asyncio
async def test_check_direction_matches_completed(config, bridge_config):
    """Completed transfers (deposited and minted) should not be in-flight."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    mock_source_w3 = MagicMock()
    mock_dest_w3 = MagicMock()
    mock_source_w3.to_checksum_address = lambda x: x
    mock_dest_w3.to_checksum_address = lambda x: x

    recipient_bytes = bytes.fromhex(
        "000000000000000000000000" + "0987654321098765432109876543210987654321"[2:]
    )

    # Deposit event
    deposit_event = {
        "args": {
            "amount": 1000000,
            "mintRecipient": recipient_bytes,
            "burnToken": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        },
        "transactionHash": b"\x12\x34\x56\x78" * 8,
    }

    # Matching mint event (same amount + recipient)
    mint_event = {
        "args": {
            "amount": 1000000,
            "mintRecipient": "0x0987654321098765432109876543210987654321",
            "feeCollected": 0,
        },
    }

    mock_source_messenger = MagicMock()
    mock_source_messenger.events.DepositForBurn.get_logs = AsyncMock(
        return_value=[deposit_event]
    )

    mock_dest_messenger = MagicMock()
    mock_dest_messenger.events.MintAndWithdraw.get_logs = AsyncMock(
        return_value=[mint_event]
    )

    with patch.object(adapter, "_delay", new_callable=AsyncMock):
        inflight = await adapter._check_direction(
            source_w3=mock_source_w3,
            dest_w3=mock_dest_w3,
            source_messenger=mock_source_messenger,
            dest_messenger=mock_dest_messenger,
            source_subvault="0x1234567890123456789012345678901234567890",
            dest_subvault="0x0987654321098765432109876543210987654321",
            source_chain="mainnet",
            dest_chain="hyperevm",
            source_block_time=12,
            dest_block_time=1,
        )

    # Should be empty - transfer is complete
    assert len(inflight) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_get_inflight_transfers_integration(config, bridge_config):
    """Integration test for CCTP in-flight detection."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    result = await adapter.get_inflight_transfers()

    # Should return a valid result structure
    assert result.bridge_type == "cctp"
    assert result.source_chain == "mainnet"
    assert result.dest_chain == "hyperevm"
    assert isinstance(result.total_inflight_amount, int)
    assert isinstance(result.inflight_count, int)
    assert isinstance(result.inflight_transfers, list)
