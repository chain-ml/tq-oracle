from __future__ import annotations

from .aave_v3 import AaveV3Adapter
from .base import BaseAssetAdapter
from .idle_balances import IdleBalancesAdapter
from .morpho_blue import MorphoBlueAdapter
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
    "morpho_blue": MorphoBlueAdapter,
    "pendle": PendleAdapter,
    "erc4626": ERC4626VaultAdapter,
    "snusd": SNUSDAdapter,
    "uniswap_v3": UniswapV3Adapter,
    "uniswap_v4": UniswapV4Adapter,
}

# Adapters that support named instances (e.g., "aave_v3.spark")
MULTI_INSTANCE_ADAPTERS: set[str] = {"aave_v3"}

ASSET_ADAPTERS: list[type[BaseAssetAdapter]] = list(ADAPTER_REGISTRY.values())


def parse_adapter_name(adapter_name: str) -> tuple[str, str | None]:
    """Parse adapter name into base name and optional instance name.

    Args:
        adapter_name: Adapter name, optionally with instance suffix (e.g., "aave_v3.spark")

    Returns:
        Tuple of (base_name, instance_name). instance_name is None for simple names.

    Examples:
        "aave_v3" -> ("aave_v3", None)
        "aave_v3.spark" -> ("aave_v3", "spark")
        "pendle" -> ("pendle", None)
    """
    if "." in adapter_name:
        parts = adapter_name.split(".", 1)
        return parts[0].lower(), parts[1]
    return adapter_name.lower(), None


def get_adapter_class(adapter_name: str) -> type[BaseAssetAdapter]:
    """Get adapter class by name.

    Supports both simple names ("aave_v3") and instance names ("aave_v3.spark").
    Instance names are used for adapters that support multiple configurations
    (e.g., Aave V3 and Spark both use the aave_v3 adapter with different configs).

    Args:
        adapter_name: Name of the adapter (case-insensitive), optionally with
                     instance suffix (e.g., "aave_v3.spark")

    Returns:
        Adapter class

    Raises:
        ValueError: If adapter_name is not recognized
    """
    base_name, instance_name = parse_adapter_name(adapter_name)

    if base_name not in ADAPTER_REGISTRY:
        raise ValueError(
            f"Unknown adapter '{adapter_name}'. "
            f"Available: {', '.join(ADAPTER_REGISTRY.keys())}"
        )

    if instance_name and base_name not in MULTI_INSTANCE_ADAPTERS:
        raise ValueError(
            f"Adapter '{base_name}' does not support named instances. "
            f"Use '{base_name}' instead of '{adapter_name}'."
        )

    return ADAPTER_REGISTRY[base_name]


__all__ = [
    "ASSET_ADAPTERS",
    "ADAPTER_REGISTRY",
    "MULTI_INSTANCE_ADAPTERS",
    "AaveV3Adapter",
    "ERC4626VaultAdapter",
    "IdleBalancesAdapter",
    "MorphoBlueAdapter",
    "PendleAdapter",
    "SNUSDAdapter",
    "StrETHAdapter",
    "StakeWiseAdapter",
    "UniswapV3Adapter",
    "UniswapV4Adapter",
    "get_adapter_class",
    "parse_adapter_name",
]
