from __future__ import annotations

from .cow_swap import CowSwapAdapter
from .eth import ETHAdapter
from .pyth import PythAdapter
PRICE_ADAPTERS = [
    CowSwapAdapter,
    ETHAdapter,
]

__all__ = ["PRICE_ADAPTERS", "PythAdapter", "ETHAdapter"]
