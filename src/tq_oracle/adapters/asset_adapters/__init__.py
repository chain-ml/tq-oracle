from __future__ import annotations

from .aave_v3 import AaveV3Adapter
from .base import BaseAssetAdapter
from .idle_balances import IdleBalancesAdapter
from .pendle import PendleAdapter
from .streth import StrETHAdapter
from .stakewise import StakeWiseAdapter

ADAPTER_REGISTRY: dict[str, type[BaseAssetAdapter]] = {
    "idle_balances": IdleBalancesAdapter,
    "streth": StrETHAdapter,
    "stakewise": StakeWiseAdapter,
    "aave_v3": AaveV3Adapter,
    "pendle": PendleAdapter,
}

ASSET_ADAPTERS: list[type[BaseAssetAdapter]] = list(ADAPTER_REGISTRY.values())


def get_adapter_class(adapter_name: str) -> type[BaseAssetAdapter]:
    """Get adapter class by name.

    Args:
        adapter_name: Name of the adapter (case-insensitive)

    Returns:
        Adapter class

    Raises:
        ValueError: If adapter_name is not recognized
    """
    adapter_name_normalized = adapter_name.lower()
    if adapter_name_normalized not in ADAPTER_REGISTRY:
        raise ValueError(
            f"Unknown adapter '{adapter_name}'. "
            f"Available: {', '.join(ADAPTER_REGISTRY.keys())}"
        )
    return ADAPTER_REGISTRY[adapter_name_normalized]


__all__ = [
    "ASSET_ADAPTERS",
    "ADAPTER_REGISTRY",
    "AaveV3Adapter",
    "IdleBalancesAdapter",
    "PendleAdapter",
    "StrETHAdapter",
    "StakeWiseAdapter",
    "get_adapter_class",
]
