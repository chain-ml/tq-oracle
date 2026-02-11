"""Base class for bridge adapters that track in-flight cross-chain transfers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tq_oracle.settings import BridgeConfig, OracleSettings


@dataclass
class InFlightTransfer:
    """Represents an in-flight cross-chain transfer."""

    amount: int  # Amount in native token units (e.g., USDC with 6 decimals)
    token_address: str  # Token address on source chain
    source_chain: str  # Source chain name
    dest_chain: str  # Destination chain name
    recipient: str  # Recipient address on destination chain
    tx_hash: str | None = None  # Optional source transaction hash


@dataclass
class BridgeReconciliationResult:
    """Result of bridge reconciliation check."""

    bridge_type: str  # Type of bridge (cctp, native, evm_core)
    source_chain: str
    dest_chain: str
    total_inflight_amount: int  # Total amount in-flight (native units)
    inflight_count: int  # Number of in-flight transactions
    inflight_transfers: list[InFlightTransfer]  # Detailed list of in-flight transfers
    error: str | None = None  # Error message if reconciliation failed


class BaseBridgeAdapter(ABC):
    """Base class for bridge adapters.

    Bridge adapters track in-flight transfers between chains by:
    1. Querying source chain for outbound transfers (burns, deposits)
    2. Querying destination chain for inbound transfers (mints, withdrawals)
    3. Matching transfers by (amount, recipient) to find unmatched (in-flight) ones
    """

    def __init__(self, config: OracleSettings, bridge_config: BridgeConfig):
        """Initialize bridge adapter.

        Args:
            config: Oracle settings
            bridge_config: Bridge-specific configuration
        """
        self.config = config
        self.bridge_config = bridge_config

    @property
    @abstractmethod
    def bridge_type(self) -> str:
        """Return bridge type identifier (e.g., 'cctp', 'native', 'evm_core')."""
        ...

    @abstractmethod
    async def get_inflight_transfers(self) -> BridgeReconciliationResult:
        """Get all in-flight transfers for this bridge.

        Returns:
            BridgeReconciliationResult with in-flight transfer details
        """
        ...

    async def get_inflight_amount(self) -> int:
        """Get total in-flight amount for this bridge.

        Convenience method that returns just the total amount.

        Returns:
            Total in-flight amount in native token units
        """
        result = await self.get_inflight_transfers()
        return result.total_inflight_amount
