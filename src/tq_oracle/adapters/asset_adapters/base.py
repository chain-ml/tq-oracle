from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from ...settings import OracleSettings


@dataclass
class AssetData:
    """Raw asset data from a protocol adapter."""

    asset_address: str
    amount: int  # in native units
    tvl_only: bool = False


class BaseAssetAdapter(ABC):
    """Abstract base class for asset adapters."""

    def __init__(self, config: OracleSettings):
        """Initialize the adapter with configuration.

        Args:
            config: Oracle configuration
        """
        self.config = config

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Return the name of this adapter."""
        ...

    @abstractmethod
    async def fetch_assets(
        self,
        subvault_address: str,
        previous_assets: list[AssetData] | None = None,
    ) -> list[AssetData]:
        """Fetch asset data for the given subvault.

        Args:
            subvault_address: Subvault to query
            previous_assets: Optional results from previous adapters in chain.
                            Allows adapters to transform/convert wrapped tokens
                            from earlier adapters (e.g., PT tokens, ERC4626 vaults).

        Returns:
            List of assets discovered and/or transformed by this adapter
        """
        ...
