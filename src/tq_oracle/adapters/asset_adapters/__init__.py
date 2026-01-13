from __future__ import annotations

from .aave_v3 import AaveV3Adapter
from .base import BaseAssetAdapter
from .idle_balances import IdleBalancesAdapter
from .pendle import PendleAdapter
from .streth import StrETHAdapter
from .stakewise import StakeWiseAdapter
from .rwa import ERC4626VaultAdapter, SNUSDAdapter
from .uniswap import UniswapV3Adapter, UniswapV4Adapter

ADAPTER_REGISTRY: dict[str, type[BaseAssetAdapter]] = {
    "idle_balances": IdleBalancesAdapter,
    "streth": StrETHAdapter,
    "stakewise": StakeWiseAdapter,
    "aave_v3": AaveV3Adapter,
    "pendle": PendleAdapter,
    "erc4626": ERC4626VaultAdapter,
    "snusd": SNUSDAdapter,
    "uniswap_v3": UniswapV3Adapter,
    "uniswap_v4": UniswapV4Adapter,
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
    "ERC4626VaultAdapter",
    "IdleBalancesAdapter",
    "PendleAdapter",
    "SNUSDAdapter",
    "StrETHAdapter",
    "StakeWiseAdapter",
    "UniswapV3Adapter",
    "UniswapV4Adapter",
    "get_adapter_class",
]
