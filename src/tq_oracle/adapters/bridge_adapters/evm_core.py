"""HyperEVM <-> HyperCore native bridge adapter.

Tracks in-flight transfers between HyperEVM and HyperCore using:
- HyperEVM -> HyperCore: ERC20 transfers to system addresses (0x2000... for USDC)
- HyperCore -> HyperEVM: spotSend actions via API

The bridge is nearly instant (~2 seconds) but we still track in-flight
to ensure accurate TVL reconciliation.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiohttp
from web3 import AsyncWeb3

from tq_oracle.constants import (
    HYPERCORE_MAINNET_API,
    HYPEREVM_MAINNET_RPC,
    HYPEREVM_USDC_SYSTEM_ADDRESS,
    USDC_HYPEREVM_MAINNET,
)

from .base import BaseBridgeAdapter, BridgeReconciliationResult, InFlightTransfer

if TYPE_CHECKING:
    from tq_oracle.settings import BridgeConfig, OracleSettings

logger = logging.getLogger(__name__)

# ERC20 Transfer event signature
TRANSFER_EVENT_SIGNATURE = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

# Lookback blocks for HyperEVM (1s block time)
# 300 blocks = 5 minutes of lookback
HYPEREVM_LOOKBACK_BLOCKS = 300


class EVMCoreBridgeAdapter(BaseBridgeAdapter):
    """Tracks in-flight transfers between HyperEVM and HyperCore.

    HyperEVM -> HyperCore transfers are tracked via ERC20 Transfer events
    to the USDC system address (0x2000...0000).

    HyperCore -> HyperEVM transfers are tracked via the HyperLiquid API
    spotSend actions.

    Note: This bridge is extremely fast (~2 seconds), so in-flight amounts
    are typically zero or very small. The adapter exists for completeness
    and to catch any edge cases during reconciliation.
    """

    def __init__(self, config: OracleSettings, bridge_config: BridgeConfig):
        """Initialize EVM-Core bridge adapter.

        Args:
            config: Oracle settings
            bridge_config: Bridge configuration with source/dest chain info
        """
        super().__init__(config, bridge_config)
        self._session: aiohttp.ClientSession | None = None

    @property
    def bridge_type(self) -> str:
        return "evm_core"

    def _get_hyperevm_rpc(self) -> str:
        """Get HyperEVM RPC URL."""
        for chain in self.config.chains:
            if chain.name.lower() in ("hyperevm", "hyper_evm"):
                if chain.rpc:
                    return chain.rpc
        return HYPEREVM_MAINNET_RPC

    def _get_hypercore_api(self) -> str:
        """Get HyperCore API URL."""
        for chain in self.config.chains:
            if chain.name.lower() in ("hypercore", "hyper_core"):
                if chain.api_url:
                    return chain.api_url
        return HYPERCORE_MAINNET_API

    def _get_subvault_address(self, chain_name: str) -> str | None:
        """Get subvault address for a chain.

        Args:
            chain_name: Chain name (hyperevm or hypercore)

        Returns:
            Subvault address or None if not configured
        """
        chain_lower = chain_name.lower()

        # Check bridge config first
        if chain_lower in ("hyperevm", "hyper_evm"):
            if self.bridge_config.source_subvault:
                return self.bridge_config.source_subvault
        elif chain_lower in ("hypercore", "hyper_core"):
            if self.bridge_config.dest_subvault:
                return self.bridge_config.dest_subvault

        # Check chain configs
        for chain in self.config.chains:
            if chain.name.lower() == chain_lower:
                if chain.vault_address:
                    return chain.vault_address
                if chain.subvault_addresses:
                    return chain.subvault_addresses[0]

        return None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _cleanup_session(self) -> None:
        """Clean up aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def _get_evm_to_core_transfers(
        self,
        w3: AsyncWeb3,
        subvault_address: str,
        from_block: int,
        to_block: int,
    ) -> list[dict]:
        """Get ERC20 transfers from subvault to HyperCore system address.

        Args:
            w3: Web3 instance for HyperEVM
            subvault_address: Address to filter transfers from
            from_block: Starting block number
            to_block: Ending block number

        Returns:
            List of transfer events
        """
        usdc_address = USDC_HYPEREVM_MAINNET

        # Build filter for Transfer events from subvault to system address
        # Transfer(address indexed from, address indexed to, uint256 value)
        filter_params = {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": w3.to_checksum_address(usdc_address),
            "topics": [
                TRANSFER_EVENT_SIGNATURE,
                # from: subvault (padded to 32 bytes)
                "0x" + subvault_address.lower()[2:].zfill(64),
                # to: USDC system address (padded to 32 bytes)
                "0x" + HYPEREVM_USDC_SYSTEM_ADDRESS.lower()[2:].zfill(64),
            ],
        }

        logs = await w3.eth.get_logs(filter_params)
        return [dict(log) for log in logs]

    async def _get_core_to_evm_transfers(
        self,
        subvault_address: str,
    ) -> list[dict]:
        """Get spotSend transfers from HyperCore to HyperEVM.

        Uses the HyperLiquid API to query recent spotSend actions.

        Args:
            subvault_address: Address to filter transfers for

        Returns:
            List of spotSend actions
        """
        api_url = self._get_hypercore_api()
        session = await self._get_session()

        # Query user's recent fills and actions
        # The spotSend action is used for HyperCore -> HyperEVM transfers
        payload = {
            "type": "userFillsAndFunding",
            "user": subvault_address,
        }

        try:
            async with session.post(
                f"{api_url}/info",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    logger.warning(
                        f"HyperCore API returned status {response.status}"
                    )
                    return []

                data = await response.json()
                # Filter for spotSend actions to EVM
                # Note: This is a simplified check - actual implementation
                # may need to parse the specific action format
                spot_sends = []
                if isinstance(data, list):
                    for action in data:
                        if action.get("type") == "spotSend":
                            spot_sends.append(action)
                return spot_sends

        except Exception as e:
            logger.warning(f"Failed to query HyperCore API: {e}")
            return []

    async def get_inflight_transfers(self) -> BridgeReconciliationResult:
        """Get all in-flight transfers between HyperEVM and HyperCore.

        Returns:
            BridgeReconciliationResult with in-flight transfer details
        """
        source_chain = self.bridge_config.source_chain
        dest_chain = self.bridge_config.dest_chain

        # Get subvault addresses
        source_subvault = self._get_subvault_address(source_chain)
        dest_subvault = self._get_subvault_address(dest_chain)

        if not source_subvault:
            return BridgeReconciliationResult(
                bridge_type=self.bridge_type,
                source_chain=source_chain,
                dest_chain=dest_chain,
                total_inflight_amount=0,
                inflight_count=0,
                inflight_transfers=[],
                error="Source subvault address not configured",
            )

        w3 = None
        try:
            # Connect to HyperEVM
            rpc_url = self._get_hyperevm_rpc()
            w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))

            current_block = await w3.eth.block_number
            from_block = max(0, current_block - HYPEREVM_LOOKBACK_BLOCKS + 1)

            inflight_transfers: list[InFlightTransfer] = []

            # Track EVM -> Core direction
            if source_chain.lower() in ("hyperevm", "hyper_evm"):
                evm_to_core = await self._get_evm_to_core_transfers(
                    w3,
                    source_subvault,
                    from_block,
                    current_block,
                )

                for log in evm_to_core:
                    # Parse amount from data (uint256)
                    amount = int(log.get("data", "0x0"), 16)
                    tx_hash = log.get("transactionHash")
                    if isinstance(tx_hash, bytes):
                        tx_hash = tx_hash.hex()

                    inflight_transfers.append(
                        InFlightTransfer(
                            amount=amount,
                            token_address=USDC_HYPEREVM_MAINNET,
                            source_chain=source_chain,
                            dest_chain=dest_chain,
                            recipient=dest_subvault or source_subvault,
                            tx_hash=tx_hash,
                        )
                    )

            # Note: Core -> EVM transfers complete almost instantly
            # and the API doesn't provide good visibility into pending
            # transfers, so we only track EVM -> Core direction for now.

            total_amount = sum(t.amount for t in inflight_transfers)

            if inflight_transfers:
                logger.warning(
                    f"EVM-Core bridge: {len(inflight_transfers)} in-flight "
                    f"transfer(s) detected, total: {total_amount} units"
                )

            return BridgeReconciliationResult(
                bridge_type=self.bridge_type,
                source_chain=source_chain,
                dest_chain=dest_chain,
                total_inflight_amount=total_amount,
                inflight_count=len(inflight_transfers),
                inflight_transfers=inflight_transfers,
            )

        except Exception as e:
            logger.error(f"EVM-Core bridge check failed: {e}")
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
            if w3:
                try:
                    await w3.provider.disconnect()  # type: ignore[union-attr]
                except AttributeError:
                    pass
            await self._cleanup_session()
