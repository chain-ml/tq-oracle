from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from ...settings import OracleSettings


@dataclass
class PriceData:
    """Price data from a price adapter."""

    base_asset: str
    prices: dict[str, int]  # asset_address -> price_wei (18 decimals)
    decimals: dict[str, int] = field(
        default_factory=dict
    )  # asset_address -> token_decimals


class BasePriceAdapter(ABC):
    """Abstract base class for price adapters."""

    def __init__(self, config: OracleSettings):
        """Initialize the adapter with configuration."""
        self.config = config

    @property
    @abstractmethod
    def adapter_name(self) -> str:
        """Return the name of this adapter."""
        ...

    @abstractmethod
    async def fetch_prices(
        self, asset_addresses: list[str], prices_accumulator: PriceData
    ) -> PriceData:
        """Fetch prices for the given asset addresses."""
        ...

    def validate_prices(self, price_data: PriceData) -> None:
        """Validate prices and raise an exception if any prices are non-positive.
        Called after fetching prices to ensure all prices are positive.

        Args:
            price_data: The accumulated price data from all price adapters

        Returns:
            None
        """

        invalid_prices = [
            (asset_address, price)
            for asset_address, price in price_data.prices.items()
            if price <= 0
        ]

        if invalid_prices:
            invalid_details = ", ".join(
                f"{addr}: {price}" for addr, price in invalid_prices
            )
            raise ValueError(
                f"Found {len(invalid_prices)} asset(s) with non-positive prices: {invalid_details}"
            )
