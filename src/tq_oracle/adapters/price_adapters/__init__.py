from __future__ import annotations

from .eth import ETHAdapter
from .pyth import PythAdapter

PRICE_ADAPTERS = [
    PythAdapter,
    ETHAdapter,
]

__all__ = ["PRICE_ADAPTERS", "PythAdapter", "ETHAdapter"]
