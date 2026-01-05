from __future__ import annotations

from .chainlink import ChainlinkAdapter
from .coingecko import CoinGeckoAdapter
from .cow_swap import CowSwapAdapter
from .eth import ETHAdapter

# Price adapters run in order:
# 1. ChainlinkAdapter - On-chain oracle for configured stablecoins (most stable)
# 2. CoinGeckoAdapter - API-based pricing for configured tokens (batched, stable)
# 3. CowSwapAdapter - DEX pricing for remaining assets
# 4. ETHAdapter - ETH/WETH/osETH prices
PRICE_ADAPTERS = [
    ChainlinkAdapter,
    CoinGeckoAdapter,
    CowSwapAdapter,
    ETHAdapter,
]

__all__ = [
    "PRICE_ADAPTERS",
    "ChainlinkAdapter",
    "CoinGeckoAdapter",
    "CowSwapAdapter",
    "ETHAdapter",
]
