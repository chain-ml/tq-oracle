"""CCTP bridge in-flight transaction detection adapter.

Tracks USDC transfers via Circle's Cross-Chain Transfer Protocol (CCTP V2)
by comparing DepositForBurn events on source chain with MintAndWithdraw
events on destination chain.

Supports:
- Mainnet <-> HyperEVM (CCTP V2)
- Mainnet <-> Arbitrum (CCTP V2)
- Mainnet <-> Base (CCTP V2)
- Bidirectional tracking (both directions)
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from web3 import AsyncWeb3, Web3

import tq_oracle
from tq_oracle.abi import load_abi
from tq_oracle.constants import (
    ARBITRUM_BLOCK_TIME,
    BASE_BLOCK_TIME,
    HYPEREVM_BLOCK_TIME,
    L1_BLOCK_TIME,
    RPC_RATE_LIMIT_DELAY,
    TOKEN_MESSENGER_V2_PROD,
)

from .base import BaseBridgeAdapter, BridgeReconciliationResult, InFlightTransfer

if TYPE_CHECKING:
    from web3.contract import AsyncContract

    from tq_oracle.settings import BridgeConfig, OracleSettings

logger = logging.getLogger(__name__)


class TransactionIdentity(NamedTuple):
    """Unique identifier for a CCTP bridge transaction.

    Used for matching deposit events on source chain with mint events
    on destination chain.
    """

    amount: int
    recipient: str


# Chain name to block time mapping
CHAIN_BLOCK_TIMES: dict[str, int] = {
    "mainnet": L1_BLOCK_TIME,
    "hyperevm": HYPEREVM_BLOCK_TIME,
    "hypercore": HYPEREVM_BLOCK_TIME,  # Same as HyperEVM
    "arbitrum": ARBITRUM_BLOCK_TIME,
    "base": BASE_BLOCK_TIME,
}


class CCTPBridgeAdapter(BaseBridgeAdapter):
    """Detects in-flight CCTP bridging transactions between chains.

    Supports CCTP V2 transfers between:
    - Mainnet <-> HyperEVM

    The adapter queries DepositForBurn events on the source chain and
    MintAndWithdraw events on the destination chain, then matches them
    by (amount, recipient) to find in-flight transfers.

    Checks both directions (source->dest and dest->source) to catch
    transfers in either direction.
    """

    def __init__(self, config: OracleSettings, bridge_config: BridgeConfig):
        """Initialize CCTP bridge adapter.

        Args:
            config: Oracle settings
            bridge_config: Bridge configuration with source/dest chain info
        """
        super().__init__(config, bridge_config)
        self._source_w3: AsyncWeb3 | None = None
        self._dest_w3: AsyncWeb3 | None = None

    @property
    def bridge_type(self) -> str:
        return "cctp"

    def _get_chain_rpc(self, chain_name: str) -> str:
        """Get RPC URL for a chain.

        Args:
            chain_name: Chain name (mainnet, hyperevm, arbitrum, base)

        Returns:
            RPC URL for the chain
        """
        # Check if we have chain configs
        for chain in self.config.chains:
            if chain.name.lower() == chain_name.lower():
                if chain.rpc:
                    return chain.rpc

        # Fallback to default RPCs
        from tq_oracle.constants import (
            DEFAULT_BASE_RPC_URL,
            DEFAULT_MAINNET_RPC_URL,
            HYPEREVM_MAINNET_RPC,
        )

        # Default Arbitrum RPC (not in constants yet)
        DEFAULT_ARBITRUM_RPC_URL = "https://arb1.arbitrum.io/rpc"

        chain_rpc_map = {
            "mainnet": self.config.vault_rpc or DEFAULT_MAINNET_RPC_URL,
            "hyperevm": HYPEREVM_MAINNET_RPC,
            "arbitrum": DEFAULT_ARBITRUM_RPC_URL,
            "base": DEFAULT_BASE_RPC_URL,
        }

        if chain_name.lower() not in chain_rpc_map:
            raise ValueError(f"Unknown chain for CCTP: {chain_name}")

        return chain_rpc_map[chain_name.lower()]

    def _get_subvault_address(self, chain_name: str) -> str | None:
        """Get subvault address for a chain.

        Args:
            chain_name: Chain name

        Returns:
            Subvault address or None if not configured
        """
        # Check bridge config first
        if chain_name.lower() == self.bridge_config.source_chain.lower():
            if self.bridge_config.source_subvault:
                return self.bridge_config.source_subvault
        elif chain_name.lower() == self.bridge_config.dest_chain.lower():
            if self.bridge_config.dest_subvault:
                return self.bridge_config.dest_subvault

        # Check chain configs
        for chain in self.config.chains:
            if chain.name.lower() == chain_name.lower():
                if chain.vault_address:
                    return chain.vault_address
                if chain.subvault_addresses:
                    return chain.subvault_addresses[0]

        return None

    def _get_chain_block_number(self, chain_name: str) -> int | None:
        """Get configured block number for a chain from ChainConfig.

        Args:
            chain_name: Chain name

        Returns:
            Block number if configured, None otherwise
        """
        for chain in self.config.chains:
            if chain.name.lower() == chain_name.lower():
                return chain.block_number
        return None

    def _get_token_messenger(self) -> str:
        """Get CCTP TokenMessenger address.

        Returns:
            TokenMessenger V2 contract address
        """
        if self.bridge_config.token_messenger:
            return self.bridge_config.token_messenger
        return TOKEN_MESSENGER_V2_PROD

    @staticmethod
    def _extract_address_from_bytes32(w3: Web3 | AsyncWeb3, bytes32_value: bytes) -> str:
        """Extract 20-byte Ethereum address from bytes32 CCTP field.

        CCTP stores addresses as bytes32 with the actual address in the
        last 20 bytes. This helper extracts and checksums the address.

        Args:
            w3: Web3 instance for address checksumming
            bytes32_value: The bytes32 value containing the address

        Returns:
            Lowercase checksummed Ethereum address
        """
        return w3.to_checksum_address(bytes32_value[-20:]).lower()

    async def _delay(self) -> None:
        """Rate limit delay for public RPCs."""
        logger.debug(f"Rate limit delay: {RPC_RATE_LIMIT_DELAY}s")
        await asyncio.sleep(RPC_RATE_LIMIT_DELAY)

    async def _cleanup_providers(self, *providers: AsyncWeb3 | None) -> None:
        """Safely disconnect Web3 providers."""
        for provider in providers:
            if provider:
                try:
                    await provider.provider.disconnect()  # type: ignore[union-attr]
                except AttributeError as e:
                    logger.debug(f"Provider disconnect (no method): {e}")

    def _calculate_scaled_blocks(
        self, base_blocks: int, source_block_time: int, dest_block_time: int
    ) -> tuple[int, int]:
        """Calculate scaled lookback blocks for source and destination chains.

        Ensures the same time window in seconds across both chains despite
        different block production rates.

        Args:
            base_blocks: Base lookback blocks (calibrated for L1)
            source_block_time: Block time of source chain in seconds
            dest_block_time: Block time of destination chain in seconds

        Returns:
            Tuple of (source_blocks, dest_blocks) ensuring same time window
        """
        if source_block_time == dest_block_time:
            return (base_blocks, base_blocks)

        # Calculate time window based on L1 blocks
        time_window_seconds = base_blocks * L1_BLOCK_TIME

        # Scale blocks for each chain to match the time window
        source_blocks = time_window_seconds // source_block_time
        dest_blocks = time_window_seconds // dest_block_time

        # Cap to prevent excessive lookback
        max_blocks = base_blocks * (L1_BLOCK_TIME // min(source_block_time, dest_block_time, 1))
        source_blocks = min(source_blocks, max_blocks)
        dest_blocks = min(dest_blocks, max_blocks)

        return (source_blocks, dest_blocks)

    async def get_inflight_transfers(self) -> BridgeReconciliationResult:
        """Get all in-flight CCTP transfers.

        Checks both directions (source->dest and dest->source) to detect
        any in-flight transfers regardless of which chain initiated them.

        Returns:
            BridgeReconciliationResult with in-flight transfer details
        """
        source_chain = self.bridge_config.source_chain
        dest_chain = self.bridge_config.dest_chain

        source_subvault = self._get_subvault_address(source_chain)
        dest_subvault = self._get_subvault_address(dest_chain)

        if not source_subvault or not dest_subvault:
            return BridgeReconciliationResult(
                bridge_type=self.bridge_type,
                source_chain=source_chain,
                dest_chain=dest_chain,
                total_inflight_amount=0,
                inflight_count=0,
                inflight_transfers=[],
                error="Subvault addresses not configured for both chains",
            )

        source_w3 = None
        dest_w3 = None

        try:
            # Connect to both chains
            source_rpc = self._get_chain_rpc(source_chain)
            dest_rpc = self._get_chain_rpc(dest_chain)

            logger.debug(f"CCTP: Connecting to source chain {source_chain}: {source_rpc}")
            source_w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(source_rpc))

            logger.debug(f"CCTP: Connecting to dest chain {dest_chain}: {dest_rpc}")
            dest_w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(dest_rpc))

            # Load TokenMessenger ABI
            abi_dir = Path(tq_oracle.__file__).parent / "abis"
            messenger_abi = load_abi(abi_dir / "TokenMessengerV2.json")

            messenger_addr = self._get_token_messenger()
            source_messenger = source_w3.eth.contract(
                address=source_w3.to_checksum_address(messenger_addr),
                abi=messenger_abi,
            )
            dest_messenger = dest_w3.eth.contract(
                address=dest_w3.to_checksum_address(messenger_addr),
                abi=messenger_abi,
            )

            # Get block times for scaling
            source_block_time = CHAIN_BLOCK_TIMES.get(source_chain.lower(), L1_BLOCK_TIME)
            dest_block_time = CHAIN_BLOCK_TIMES.get(dest_chain.lower(), L1_BLOCK_TIME)

            # Resolve to_block for each chain: use config block if pinned, else current
            source_to_block = self._get_chain_block_number(source_chain)
            dest_to_block = self._get_chain_block_number(dest_chain)

            if source_to_block is None:
                source_to_block = await source_w3.eth.block_number
            if dest_to_block is None:
                dest_to_block = await dest_w3.eth.block_number

            # Check source -> dest direction
            forward_transfers = await self._check_direction(
                source_w3=source_w3,
                dest_w3=dest_w3,
                source_messenger=source_messenger,
                dest_messenger=dest_messenger,
                source_subvault=source_w3.to_checksum_address(source_subvault),
                dest_subvault=dest_w3.to_checksum_address(dest_subvault),
                source_chain=source_chain,
                dest_chain=dest_chain,
                source_block_time=source_block_time,
                dest_block_time=dest_block_time,
                source_to_block=source_to_block,
                dest_to_block=dest_to_block,
            )

            # Check dest -> source direction (reverse)
            reverse_transfers = await self._check_direction(
                source_w3=dest_w3,
                dest_w3=source_w3,
                source_messenger=dest_messenger,
                dest_messenger=source_messenger,
                source_subvault=dest_w3.to_checksum_address(dest_subvault),
                dest_subvault=source_w3.to_checksum_address(source_subvault),
                source_chain=dest_chain,
                dest_chain=source_chain,
                source_block_time=dest_block_time,
                dest_block_time=source_block_time,
                source_to_block=dest_to_block,
                dest_to_block=source_to_block,
            )

            all_transfers = forward_transfers + reverse_transfers
            total_amount = sum(t.amount for t in all_transfers)

            return BridgeReconciliationResult(
                bridge_type=self.bridge_type,
                source_chain=source_chain,
                dest_chain=dest_chain,
                total_inflight_amount=total_amount,
                inflight_count=len(all_transfers),
                inflight_transfers=all_transfers,
            )

        except Exception as e:
            logger.error(f"CCTP bridge check failed: {e}")
            return BridgeReconciliationResult(
                bridge_type=self.bridge_type,
                source_chain=source_chain,
                dest_chain=dest_chain,
                total_inflight_amount=0,
                inflight_count=0,
                inflight_transfers=[],
                error=str(e),
            )

        finally:
            await self._cleanup_providers(source_w3, dest_w3)

    async def _check_direction(
        self,
        source_w3: AsyncWeb3,
        dest_w3: AsyncWeb3,
        source_messenger: AsyncContract,
        dest_messenger: AsyncContract,
        source_subvault: str,
        dest_subvault: str,
        source_chain: str,
        dest_chain: str,
        source_block_time: int,
        dest_block_time: int,
        source_to_block: int,
        dest_to_block: int,
    ) -> list[InFlightTransfer]:
        """Check for in-flight transactions in one direction.

        Uses counter-based matching to correctly handle duplicate
        (amount, recipient) pairs across multiple transfers.

        Args:
            source_w3: Web3 instance for source chain
            dest_w3: Web3 instance for destination chain
            source_messenger: TokenMessenger contract on source chain
            dest_messenger: TokenMessenger contract on destination chain
            source_subvault: Subvault address on source chain (depositor filter)
            dest_subvault: Subvault address on destination chain (mintRecipient filter)
            source_chain: Source chain name
            dest_chain: Destination chain name
            source_block_time: Block time of source chain in seconds
            dest_block_time: Block time of destination chain in seconds
            source_to_block: Upper bound block for source chain event queries
            dest_to_block: Upper bound block for dest chain event queries

        Returns:
            List of in-flight transfers detected
        """
        direction = f"{source_chain}->{dest_chain}"

        # Calculate scaled lookback blocks using bridge config
        source_lookback, dest_lookback = self._calculate_scaled_blocks(
            self.bridge_config.lookback_blocks, source_block_time, dest_block_time
        )

        source_from_block = max(0, source_to_block - source_lookback + 1)
        dest_from_block = max(0, dest_to_block - dest_lookback + 1)

        logger.debug(
            f"CCTP {direction}: Source blocks [{source_from_block}..{source_to_block}] "
            f"({source_lookback} blocks, {source_lookback * source_block_time}s), "
            f"Dest blocks [{dest_from_block}..{dest_to_block}] "
            f"({dest_lookback} blocks, {dest_lookback * dest_block_time}s)"
        )

        # Query DepositForBurn events on source chain
        logger.debug(
            f"CCTP {direction}: Querying DepositForBurn from block {source_from_block} "
            f"to {source_to_block} with depositor={source_subvault}"
        )
        deposit_events = await source_messenger.events.DepositForBurn.get_logs(
            from_block=source_from_block,
            to_block=source_to_block,
            argument_filters={"depositor": source_subvault},
        )

        if self.config.using_default_rpc:
            await self._delay()

        # Query MintAndWithdraw events on destination chain
        logger.debug(
            f"CCTP {direction}: Querying MintAndWithdraw from block {dest_from_block} "
            f"to {dest_to_block} with mintRecipient={dest_subvault}"
        )
        mint_events = await dest_messenger.events.MintAndWithdraw.get_logs(
            from_block=dest_from_block,
            to_block=dest_to_block,
            argument_filters={"mintRecipient": dest_subvault},
        )

        logger.info(
            f"CCTP {direction}: Found {len(deposit_events)} deposits, {len(mint_events)} mints"
        )

        # Collect all deposits preserving order and metadata
        deposits: list[tuple[TransactionIdentity, dict]] = []
        for event in deposit_events:
            identity = TransactionIdentity(
                amount=event["args"]["amount"],
                recipient=self._extract_address_from_bytes32(
                    source_w3, event["args"]["mintRecipient"]
                ),
            )
            deposits.append((identity, {
                "tx_hash": event.get("transactionHash", b"").hex() if event.get("transactionHash") else None,
                "token": event["args"].get("burnToken", ""),
            }))

        # Count mints by identity (handles duplicates correctly)
        mint_counts: dict[TransactionIdentity, int] = {}
        for event in mint_events:
            # Reconstruct deposit amount: mint amount + fee = original deposit
            identity = TransactionIdentity(
                amount=event["args"]["amount"] + event["args"].get("feeCollected", 0),
                recipient=event["args"]["mintRecipient"].lower(),
            )
            mint_counts[identity] = mint_counts.get(identity, 0) + 1

        # Match deposits against mints greedily
        # For each deposit, consume a mint if available; otherwise it's in-flight
        remaining_mints: dict[TransactionIdentity, int] = dict(mint_counts)
        inflight_transfers: list[InFlightTransfer] = []

        for identity, deposit_info in deposits:
            if remaining_mints.get(identity, 0) > 0:
                remaining_mints[identity] -= 1
            else:
                inflight_transfers.append(
                    InFlightTransfer(
                        amount=identity.amount,
                        token_address=str(deposit_info.get("token", "")),
                        source_chain=source_chain,
                        dest_chain=dest_chain,
                        recipient=identity.recipient,
                        tx_hash=deposit_info.get("tx_hash"),
                    )
                )

        if inflight_transfers:
            logger.warning(
                f"CCTP {direction}: {len(inflight_transfers)} in-flight transaction(s) detected"
            )
            for transfer in inflight_transfers:
                logger.debug(
                    f"  In-flight: {transfer.amount} to {transfer.recipient} "
                    f"(tx: {transfer.tx_hash})"
                )

        return inflight_transfers
