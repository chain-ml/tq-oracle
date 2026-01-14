# Uniswap V3 & V4 Adapters Implementation Summary

## What Was Created

I've built complete Uniswap V3 and V4 adapters for automatically discovering and pricing LP positions held as NFTs.

### File Structure

```
src/tq_oracle/adapters/asset_adapters/uniswap/
├── __init__.py                 # Exports for UniswapV3Adapter and UniswapV4Adapter
├── README.md                   # Complete documentation and usage guide
├── uniswap_v3.py              # V3 adapter with NFT enumeration and liquidity math
└── uniswap_v4.py              # V4 adapter with hook support and PoolManager integration
```

### Registered Adapters

Both adapters are now registered in the `ADAPTER_REGISTRY`:
- `uniswap_v3` - Uniswap V3 NFT position adapter
- `uniswap_v4` - Uniswap V4 position adapter with hooks

---

## How It Works

### Automatic NFT Discovery

**V3:** Uses ERC721Enumerable to automatically find all position NFTs on-chain:
```
1. balanceOf(subvault_address) → number of NFTs
2. tokenOfOwnerByIndex(subvault, 0..N) → enumerate all token IDs
3. For each token ID → get position details and calculate amounts
```

**V4:** Queries The Graph API subgraph to find position NFTs:
```
1. GraphQL query with owner filter → list of token IDs
2. For each token ID → get position details and calculate amounts
3. Requires TQ_ORACLE_GRAPH_API_KEY environment variable
```

**No manual position ID configuration needed!** Just specify the subvault address (and Graph API key for V4).

### UniswapV3Adapter

**Purpose:** Track Uniswap V3 concentrated liquidity positions

**Flow:**
```
Subvault → Enumerate NFTs → positions(tokenId) → token0, token1, fee, ticks, liquidity
                              ↓
                    Get pool from factory
                              ↓
                    pool.slot0() → current tick, sqrt price
                              ↓
                    Calculate token amounts using Uniswap V3 math
                              ↓
                    Add uncollected fees (tokensOwed0, tokensOwed1)
                              ↓
                    Return [token0_amount, token1_amount] → Price Adapters → ETH
```

**What it tracks:**
1. **Active liquidity** - Tokens in the position based on current pool state
2. **Uncollected fees** - Fees earned but not yet collected
3. **Tick range** - Position's price range (tickLower, tickUpper)
4. **Fee tier** - Pool fee (500, 3000, or 10000 bps)

**Configuration:**
```toml
# No adapter config needed - uses mainnet default

[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["uniswap_v3"]
```

**Logging Output:**
```
INFO  Uniswap V3: found 2 positions for subvault 0x...
INFO  Uniswap V3 position 123456: token0=0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 amount0=1500000000000000000,
      token1=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 amount1=3000000000,
      fee=3000, liquidity=50000000000000000, tick_range=[-887220, 887220], current_tick=201234
INFO  Uniswap V3: fetched 2 unique tokens from 2 positions for subvault 0x...
```

---

### UniswapV4Adapter

**Purpose:** Track Uniswap V4 positions with hook support

**Requirements:**
- **Graph API Key** - Required for querying Uniswap V4 subgraph to enumerate position NFTs
- Set via `TQ_ORACLE_GRAPH_API_KEY` environment variable

**Flow:**
```
Subvault → Query Graph API for tokenIds → getPositionInfo(tokenId) → PoolKey (currency0, currency1, fee, tickSpacing, hooks)
                              ↓
                    getPositionLiquidity(tokenId, poolKey, ticks)
                              ↓
                    poolManager.getSlot0(poolKey) → current tick, sqrt price
                              ↓
                    Calculate amounts using same math as V3
                              ↓
                    Return [currency0_amount, currency1_amount] → Price Adapters → ETH
```

**What it tracks:**
1. **Active liquidity** - Tokens in position based on pool state
2. **Hook address** - Custom hook contract (if any)
3. **Tick spacing** - Variable tick spacing
4. **Dynamic fees** - Pool-specific fee (can change dynamically)

**Configuration:**
```toml
[adapters.uniswap_v4]
position_manager = "0x..."  # Required when V4 is deployed

[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["uniswap_v4"]
```

**Environment Variable:**
```bash
export TQ_ORACLE_GRAPH_API_KEY="your-graph-api-key"
```

**Logging Output:**
```
INFO  Uniswap V4: found 1 position for subvault 0x...
INFO  Uniswap V4 position 555: currency0=0x... amount0=2000000000000000000,
      currency1=0x... amount1=4000000000, fee=3000, liquidity=60000000000000000,
      tick_range=[-200000, 200000], current_tick=150000, hook=0x1234567890abcdef...
```

---

## Key Differences: V3 vs V4

| Feature | V3 | V4 |
|---------|----|----|
| **Pool identification** | token0, token1, fee | PoolKey (currency0, currency1, fee, tickSpacing, hooks) |
| **Pool architecture** | Separate contract per pool | Single PoolManager for all pools |
| **Fee structure** | Fixed (500, 3000, 10000) | Dynamic (customizable per pool) |
| **Hooks** | No hooks | Hook contracts modify pool behavior |
| **Position manager** | 0xC36442b4a4522E871399CD717aBDD847Ab11FE88 (mainnet) | TBD when V4 fully deploys |
| **Liquidity math** | Concentrated liquidity | Same as V3 |
| **NFT enumeration** | ERC721Enumerable on-chain | Graph API subgraph query (requires API key) |
| **Requirements** | None (on-chain only) | TQ_ORACLE_GRAPH_API_KEY env var |

---

## Concentrated Liquidity Math

Both adapters use identical math to calculate token amounts from liquidity:

### Position States

A concentrated liquidity position can be in three states:

1. **Below range** (current_tick < tickLower):
   - Position is 100% token0
   - amount0 = f(liquidity, sqrt_prices)
   - amount1 = 0

2. **In range** (tickLower ≤ current_tick < tickUpper):
   - Position contains both tokens
   - amount0 = f(liquidity, current_sqrt_price, sqrt_price_upper)
   - amount1 = f(liquidity, sqrt_price_lower, current_sqrt_price)

3. **Above range** (current_tick ≥ tickUpper):
   - Position is 100% token1
   - amount0 = 0
   - amount1 = f(liquidity, sqrt_prices)

### Implementation

The adapters implement the full Uniswap tick math:
- `_get_sqrt_ratio_at_tick()` - Converts tick to sqrt price (Q96 format)
- `_get_amount0_delta()` - Calculates token0 from liquidity and price range
- `_get_amount1_delta()` - Calculates token1 from liquidity and price range
- `_calculate_amounts_from_liquidity()` - Main entry point that handles all three cases

This ensures accurate position valuation at any pool state.

---

## Configuration Examples

### Simple V3 Setup (Most Common):
```toml
# No adapter config needed - just add to subvault

[[subvault_adapters]]
subvault_address = "0xYourUniswapV3Subvault"
additional_adapters = ["uniswap_v3"]
```

### V4 Setup (When Available):
```bash
# Required: Set Graph API key
export TQ_ORACLE_GRAPH_API_KEY="your-graph-api-key"
```

```toml
[adapters.uniswap_v4]
position_manager = "0xV4PositionManagerAddress"

[[subvault_adapters]]
subvault_address = "0xYourUniswapV4Subvault"
additional_adapters = ["uniswap_v4"]
```

### Mixed Strategy (V3 + Other DeFi):
```toml
[[subvault_adapters]]
subvault_address = "0xMultiStrategySubvault"
additional_adapters = ["uniswap_v3", "aave_v3", "pendle"]
```

### Both V3 and V4:
```bash
# Required for V4
export TQ_ORACLE_GRAPH_API_KEY="your-graph-api-key"
```

```toml
[adapters.uniswap_v4]
position_manager = "0xV4PositionManager"

[[subvault_adapters]]
subvault_address = "0xSubvaultWithBothV3AndV4"
additional_adapters = ["uniswap_v3", "uniswap_v4"]
```

---

## Integration Points

### Settings Configuration

Added to `src/tq_oracle/settings.py`:
- `UniswapV3AdapterSettings` - Optional position_manager override
- `UniswapV4AdapterSettings` - position_manager, pool_manager, pools config

### Adapter Registry

Updated `src/tq_oracle/adapters/asset_adapters/__init__.py`:
```python
from .uniswap import UniswapV3Adapter, UniswapV4Adapter

ADAPTER_REGISTRY = {
    # ... existing adapters
    "uniswap_v3": UniswapV3Adapter,
    "uniswap_v4": UniswapV4Adapter,
}
```

### TOML Configuration

Updated `tq-oracle.toml.pre-prod` with:
- Adapter configuration sections (with defaults)
- Placeholder subvault examples (commented out)

---

## Advantages of This Design

### 1. Zero Manual Configuration

Unlike some DEX integrations that require listing each pool or position:
- **No position IDs needed**
  - V3: Auto-discovered via ERC721Enumerable on-chain
  - V4: Auto-discovered via Graph API subgraph query
- **No pool addresses needed** - Derived from position data + factory
- **No token lists needed** - Extracted from position details

Just point to the subvault and go! (V4 requires `TQ_ORACLE_GRAPH_API_KEY` env var)

### 2. Accurate Valuation

- Uses exact Uniswap math (not approximations)
- Accounts for current tick and price
- Includes uncollected fees (V3)
- Handles out-of-range positions correctly

### 3. Comprehensive Logging

Each position logs:
- Token addresses and amounts
- Fee tier / dynamic fee
- Liquidity amount
- Tick range and current tick
- Hook address (V4)

This makes debugging and monitoring straightforward.

### 4. Flexible Architecture

Works seamlessly with existing infrastructure:
- Token amounts go through price adapters (Chainlink, CoinGecko, etc.)
- Aggregates with other adapter results (Aave, Pendle, etc.)
- Supports multiple positions per subvault
- Supports multiple subvaults with Uniswap positions

---

## Testing the Adapters

### Quick Import Test:
```bash
source .venv/bin/activate
python -c "from src.tq_oracle.adapters.asset_adapters import ADAPTER_REGISTRY; print('uniswap_v3' in ADAPTER_REGISTRY, 'uniswap_v4' in ADAPTER_REGISTRY)"
# Should print: True True
```

### Check Registry:
```bash
python -c "from src.tq_oracle.adapters.asset_adapters import ADAPTER_REGISTRY; print(sorted(ADAPTER_REGISTRY.keys()))"
# Should include: 'uniswap_v3', 'uniswap_v4'
```

### Real Usage Test:
```toml
# In tq-oracle.toml
[[subvault_adapters]]
subvault_address = "0xYourSubvaultWithUniswapPositions"
additional_adapters = ["uniswap_v3"]
```

Then run:
```bash
tq-oracle run --config tq-oracle.toml.pre-prod
```

Look for logs:
```
INFO  Uniswap V3: found N positions for subvault 0x...
INFO  Uniswap V3 position <id>: token0=... amount0=..., token1=... amount1=...
```

---

## Architecture Alignment

Your original vision was:

> "basically for that adapter, I want it to be able to price how much of both assets returned on withdrawal...
> then those assets will typically be one of weth, usdc, usdt, et al..so then of course you'd get converted eth price from using price adapters..."

✅ **Exactly what these adapters do:**

```
Uniswap Position
    ↓
Adapter calculates → [WETH: 1.5 ETH, USDC: 3000]
    ↓
Price adapters:
    - WETH → already in ETH
    - USDC → Chainlink → ETH price → total ETH value
    ↓
Aggregate with other subvault positions
    ↓
Total vault value in ETH
```

> "Also, I need to be able, if possible to find all the nft position ids held by subvault address..."

✅ **Built into the adapters:**

```python
# Automatic NFT enumeration
balance = position_manager.balanceOf(subvault)
for i in range(balance):
    token_id = position_manager.tokenOfOwnerByIndex(subvault, i)
    # Process position token_id...
```

No manual configuration required!

---

## Summary

✅ **Uniswap V3 adapter** - Complete with NFT enumeration and liquidity math
✅ **Uniswap V4 adapter** - Supports hooks and dynamic fees
✅ **Automatic position discovery** - No manual position ID configuration
✅ **Accurate valuation** - Full Uniswap math implementation
✅ **Comprehensive logging** - Token amounts, ticks, liquidity, hooks
✅ **Fully integrated** - Registered, configured, documented
✅ **Ready to use** - Just add subvault address and adapter name

The adapters are production-ready! When you create a subvault that holds Uniswap positions:
1. Uncomment the placeholder config in `tq-oracle.toml.pre-prod`
2. Replace `0xYourUniswapV3Subvault` with your actual subvault address
3. Run the oracle - it will automatically discover and price all positions

For V4, set the `position_manager` address when it's deployed.

---

## Future Enhancements

Potential additions (not implemented yet):
- **jrUSDe withdrawal queue tracking** - You mentioned wanting this later
- **Position health monitoring** - Warn when positions go out of range
- **Yield tracking** - Historical fee collection analysis
- **Rebalancing suggestions** - Optimize tick ranges based on volatility
- **IL calculation** - Compare LP returns vs HODL strategy

Let me know when you want to tackle any of these!

---

## References

Sources used for implementation:
- [Uniswap V3 NonfungiblePositionManager](https://docs.uniswap.org/contracts/v3/reference/periphery/NonfungiblePositionManager)
- [Uniswap V4 PositionManager](https://docs.uniswap.org/contracts/v4/reference/periphery/PositionManager)
- [Uniswap V3 SDK - Fetching Positions](https://docs.uniswap.org/sdk/v3/guides/liquidity/fetching-positions)
- [Uniswap V4 Overview](https://docs.uniswap.org/contracts/v4/overview)
- [Uniswap V4 Hooks](https://docs.uniswap.org/contracts/v4/concepts/hooks)
