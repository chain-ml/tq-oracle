"""Tests for CCTP bridge adapter."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from web3 import Web3

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


RECIPIENT_HEX = "0987654321098765432109876543210987654321"
RECIPIENT_ADDR = "0x0987654321098765432109876543210987654321"
RECIPIENT_BYTES = bytes.fromhex("000000000000000000000000" + RECIPIENT_HEX)

SOURCE_SUBVAULT = "0x1234567890123456789012345678901234567890"
USDC_ADDR = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"


def _make_deposit_event(amount: int, recipient_bytes: bytes = RECIPIENT_BYTES) -> dict:
    """Helper to create a mock DepositForBurn event."""
    return {
        "args": {
            "amount": amount,
            "mintRecipient": recipient_bytes,
            "burnToken": USDC_ADDR,
        },
        "transactionHash": b"\x12\x34\x56\x78" * 8,
    }


def _make_mint_event(
    amount: int, fee: int = 0, recipient: str = RECIPIENT_ADDR
) -> dict:
    """Helper to create a mock MintAndWithdraw event."""
    return {
        "args": {
            "amount": amount,
            "mintRecipient": recipient,
            "feeCollected": fee,
        },
    }


def _make_mock_w3_pair():
    """Create a pair of mock Web3 instances with real checksum address handling."""
    source = MagicMock()
    dest = MagicMock()
    # Use real Web3.to_checksum_address to correctly handle bytes -> hex string
    source.to_checksum_address = Web3.to_checksum_address
    dest.to_checksum_address = Web3.to_checksum_address
    return source, dest


async def _run_check_direction(
    adapter, deposit_events, mint_events, source_to_block=1000, dest_to_block=12000
):
    """Helper to run _check_direction with standard mocks."""
    mock_source_w3, mock_dest_w3 = _make_mock_w3_pair()

    mock_source_messenger = MagicMock()
    mock_source_messenger.events.DepositForBurn.get_logs = AsyncMock(
        return_value=deposit_events
    )

    mock_dest_messenger = MagicMock()
    mock_dest_messenger.events.MintAndWithdraw.get_logs = AsyncMock(
        return_value=mint_events
    )

    with patch.object(adapter, "_delay", new_callable=AsyncMock):
        return await adapter._check_direction(
            source_w3=mock_source_w3,
            dest_w3=mock_dest_w3,
            source_messenger=mock_source_messenger,
            dest_messenger=mock_dest_messenger,
            source_subvault=SOURCE_SUBVAULT,
            dest_subvault=RECIPIENT_ADDR,
            source_chain="mainnet",
            dest_chain="hyperevm",
            source_block_time=12,
            dest_block_time=1,
            source_to_block=source_to_block,
            dest_to_block=dest_to_block,
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


def test_default_lookback_is_300():
    """Default lookback_blocks should be 300 (~60min on L1)."""
    bc = BridgeConfig(type="cctp", source_chain="mainnet", dest_chain="hyperevm")
    assert bc.lookback_blocks == 300


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
    assert result.error is not None
    assert "not configured" in result.error


@pytest.mark.asyncio
async def test_check_direction_finds_inflight(config, bridge_config):
    """Should detect in-flight transfers (deposited but not minted)."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    inflight = await _run_check_direction(
        adapter,
        deposit_events=[_make_deposit_event(1000000)],
        mint_events=[],
    )

    assert len(inflight) == 1
    assert inflight[0].amount == 1000000
    assert inflight[0].source_chain == "mainnet"
    assert inflight[0].dest_chain == "hyperevm"


@pytest.mark.asyncio
async def test_check_direction_matches_completed(config, bridge_config):
    """Completed transfers (deposited and minted) should not be in-flight."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    inflight = await _run_check_direction(
        adapter,
        deposit_events=[_make_deposit_event(1000000)],
        mint_events=[_make_mint_event(1000000)],
    )

    # Should be empty - transfer is complete
    assert len(inflight) == 0


@pytest.mark.asyncio
async def test_check_direction_matches_with_fee(config, bridge_config):
    """Mint with fee should match deposit (amount + feeCollected = deposit amount)."""
    adapter = CCTPBridgeAdapter(config, bridge_config)

    inflight = await _run_check_direction(
        adapter,
        deposit_events=[_make_deposit_event(1000000)],
        # Mint: 999000 received + 1000 fee = 1000000 original deposit
        mint_events=[_make_mint_event(999000, fee=1000)],
    )

    assert len(inflight) == 0


class TestDuplicateHandling:
    """Tests for counter-based matching of duplicate (amount, recipient) pairs."""

    @pytest.mark.asyncio
    async def test_two_deposits_one_mint_shows_one_inflight(self, config, bridge_config):
        """Two identical deposits with one mint should show 1 in-flight."""
        adapter = CCTPBridgeAdapter(config, bridge_config)

        inflight = await _run_check_direction(
            adapter,
            deposit_events=[
                _make_deposit_event(1000000),
                _make_deposit_event(1000000),
            ],
            mint_events=[_make_mint_event(1000000)],
        )

        assert len(inflight) == 1
        assert inflight[0].amount == 1000000

    @pytest.mark.asyncio
    async def test_two_deposits_two_mints_shows_none_inflight(
        self, config, bridge_config
    ):
        """Two identical deposits with two mints should show 0 in-flight."""
        adapter = CCTPBridgeAdapter(config, bridge_config)

        inflight = await _run_check_direction(
            adapter,
            deposit_events=[
                _make_deposit_event(1000000),
                _make_deposit_event(1000000),
            ],
            mint_events=[
                _make_mint_event(1000000),
                _make_mint_event(1000000),
            ],
        )

        assert len(inflight) == 0

    @pytest.mark.asyncio
    async def test_three_deposits_one_mint_shows_two_inflight(
        self, config, bridge_config
    ):
        """Three identical deposits with one mint should show 2 in-flight."""
        adapter = CCTPBridgeAdapter(config, bridge_config)

        inflight = await _run_check_direction(
            adapter,
            deposit_events=[
                _make_deposit_event(1000000),
                _make_deposit_event(1000000),
                _make_deposit_event(1000000),
            ],
            mint_events=[_make_mint_event(1000000)],
        )

        assert len(inflight) == 2

    @pytest.mark.asyncio
    async def test_mixed_amounts_tracked_separately(self, config, bridge_config):
        """Different amounts should be tracked independently."""
        adapter = CCTPBridgeAdapter(config, bridge_config)

        inflight = await _run_check_direction(
            adapter,
            deposit_events=[
                _make_deposit_event(1000000),  # 1 USDC
                _make_deposit_event(2000000),  # 2 USDC
            ],
            # Only the 1 USDC one was minted
            mint_events=[_make_mint_event(1000000)],
        )

        assert len(inflight) == 1
        assert inflight[0].amount == 2000000


class TestBidirectional:
    """Tests for bidirectional in-flight detection."""

    @pytest.mark.asyncio
    async def test_get_inflight_checks_both_directions(self, config, bridge_config):
        """get_inflight_transfers should check both source->dest and dest->source."""
        from tq_oracle.settings import ChainConfig

        # Provide chain block numbers so we skip the async w3.eth.block_number call
        config.chains = [
            ChainConfig(name="mainnet", network="mainnet", block_number=1000),
            ChainConfig(name="hyperevm", network="hyperevm", rpc="https://mock", block_number=12000),
        ]

        adapter = CCTPBridgeAdapter(config, bridge_config)

        directions_checked = []

        async def mock_check_direction(**kwargs):
            direction = f"{kwargs['source_chain']}->{kwargs['dest_chain']}"
            directions_checked.append(direction)
            if kwargs["source_chain"] == "mainnet":
                return [
                    InFlightTransfer(
                        amount=1000000,
                        token_address=USDC_ADDR,
                        source_chain="mainnet",
                        dest_chain="hyperevm",
                        recipient=RECIPIENT_ADDR,
                    )
                ]
            else:
                return [
                    InFlightTransfer(
                        amount=2000000,
                        token_address=USDC_ADDR,
                        source_chain="hyperevm",
                        dest_chain="mainnet",
                        recipient=SOURCE_SUBVAULT,
                    )
                ]

        mock_w3 = MagicMock()
        mock_w3.to_checksum_address = Web3.to_checksum_address

        with patch.object(adapter, "_check_direction", side_effect=mock_check_direction), \
             patch.object(adapter, "_cleanup_providers", new_callable=AsyncMock), \
             patch("tq_oracle.adapters.bridge_adapters.cctp.AsyncWeb3", return_value=mock_w3), \
             patch("tq_oracle.adapters.bridge_adapters.cctp.load_abi", return_value=[]):
            result = await adapter.get_inflight_transfers()

        # Should have checked both directions
        assert len(directions_checked) == 2
        assert "mainnet->hyperevm" in directions_checked
        assert "hyperevm->mainnet" in directions_checked

        # Should combine results from both directions
        assert result.inflight_count == 2
        assert result.total_inflight_amount == 3000000


class TestConfigBlockNumber:
    """Tests for using config block numbers instead of live block."""

    def test_get_chain_block_number_returns_configured(self, config, bridge_config):
        """Should return block number from ChainConfig if configured."""
        from tq_oracle.settings import ChainConfig

        config.chains = [
            ChainConfig(name="mainnet", network="mainnet", block_number=12345678),
        ]

        adapter = CCTPBridgeAdapter(config, bridge_config)
        assert adapter._get_chain_block_number("mainnet") == 12345678

    def test_get_chain_block_number_returns_none_if_not_configured(
        self, config, bridge_config
    ):
        """Should return None if no ChainConfig or no block_number."""
        adapter = CCTPBridgeAdapter(config, bridge_config)
        assert adapter._get_chain_block_number("mainnet") is None

    @pytest.mark.asyncio
    async def test_check_direction_uses_provided_to_block(self, config, bridge_config):
        """_check_direction should use the provided to_block for event queries."""
        adapter = CCTPBridgeAdapter(config, bridge_config)

        mock_source_w3, mock_dest_w3 = _make_mock_w3_pair()

        mock_source_messenger = MagicMock()
        deposit_get_logs = AsyncMock(return_value=[])
        mock_source_messenger.events.DepositForBurn.get_logs = deposit_get_logs

        mock_dest_messenger = MagicMock()
        mint_get_logs = AsyncMock(return_value=[])
        mock_dest_messenger.events.MintAndWithdraw.get_logs = mint_get_logs

        with patch.object(adapter, "_delay", new_callable=AsyncMock):
            await adapter._check_direction(
                source_w3=mock_source_w3,
                dest_w3=mock_dest_w3,
                source_messenger=mock_source_messenger,
                dest_messenger=mock_dest_messenger,
                source_subvault=SOURCE_SUBVAULT,
                dest_subvault=RECIPIENT_ADDR,
                source_chain="mainnet",
                dest_chain="hyperevm",
                source_block_time=12,
                dest_block_time=1,
                source_to_block=5000,
                dest_to_block=60000,
            )

        # Verify to_block was passed to get_logs
        deposit_call_kwargs = deposit_get_logs.call_args
        assert deposit_call_kwargs.kwargs["to_block"] == 5000

        mint_call_kwargs = mint_get_logs.call_args
        assert mint_call_kwargs.kwargs["to_block"] == 60000


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
