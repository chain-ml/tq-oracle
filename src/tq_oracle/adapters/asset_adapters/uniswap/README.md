# Uniswap V3 & V4 Adapters

This directory contains adapters for handling Uniswap V3 and V4 LP positions as NFTs.

## Overview

Both Uniswap V3 and V4 use concentrated liquidity positions represented as NFTs. These adapters:

1. **Auto-discover positions**: Use ERC721Enumerable to find all position NFTs owned by a subvault
2. **Calculate withdrawable amounts**: Use Uniswap math to determine how much of each token the position contains
3. **Return both tokens**: Provide token0 and token1 amounts for the pricing pipeline

## Available Adapters

### UniswapV3Adapter

Handles Uniswap V3 NFT positions.

**What it does:**
- Enumerates position NFT IDs via `balanceOf()` and `tokenOfOwnerByIndex()`
- Queries position details: token0, token1, fee tier, liquidity, tick range
- Calculates token amounts based on current pool tick and liquidity math
- Includes uncollected fees in the totals

**Configuration:**
```toml
[adapters.uniswap_v3]
# position_manager defaults to mainnet: 0xC36442b4a4522E871399CD717aBDD847Ab11FE88
# No additional configuration needed

[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["uniswap_v3"]
```

**Flow:**
```
Subvault Address
    ↓
balanceOf(subvault) → number of NFTs
    ↓
tokenOfOwnerByIndex(subvault, 0..N) → NFT IDs
    ↓
positions(tokenId) → token0, token1, fee, tickLower, tickUpper, liquidity, fees
    ↓
getPool(token0, token1, fee) → pool address
    ↓
pool.slot0() → current tick, sqrt price
    ↓
Calculate amounts using Uniswap V3 math
    ↓
Return [token0_amount, token1_amount] → Price Adapters → ETH
```

---

### UniswapV4Adapter

Handles Uniswap V4 positions with hooks.

**What it does:**
- Same as V3, but includes hook address in pool identification
- Uses PoolKey structure: `(currency0, currency1, fee, tickSpacing, hooks)`
- Queries PoolManager for pool state instead of individual pool contracts
- Supports dynamic fee pools and custom hooks

**Configuration:**
```toml
[adapters.uniswap_v4]
position_manager = "0x..."  # Required - set when V4 position manager is deployed
# pool_manager defaults to mainnet: 0x000000000004444c5dc75cb358380d2e3de08a90

[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["uniswap_v4"]
```

**Flow:**
```
Subvault Address
    ↓
balanceOf(subvault) → number of NFTs
    ↓
tokenOfOwnerByIndex(subvault, 0..N) → NFT IDs
    ↓
getPositionInfo(tokenId) → PoolKey (currency0, currency1, fee, tickSpacing, hooks), tickLower, tickUpper
    ↓
getPositionLiquidity(tokenId, poolKey, ...) → liquidity
    ↓
poolManager.getSlot0(poolKey) → current tick, sqrt price
    ↓
Calculate amounts using Uniswap V3/V4 math (same formula)
    ↓
Return [currency0_amount, currency1_amount] → Price Adapters → ETH
```

---

## Key Features

### Automatic NFT Discovery

No need to manually list position IDs in TOML! The adapters use ERC721Enumerable:

```python
# Get how many positions the subvault owns
balance = position_manager.balanceOf(subvault)

# Enumerate each position ID
for i in range(balance):
    token_id = position_manager.tokenOfOwnerByIndex(subvault, i)
    # Process position...
```

### Concentrated Liquidity Math

Both adapters use the same Uniswap concentrated liquidity formulas:

**Position states:**
- **Below range** (current_tick < tickLower): Position is 100% token0
- **In range** (tickLower ≤ current_tick < tickUpper): Position contains both tokens
- **Above range** (current_tick ≥ tickUpper): Position is 100% token1

**Calculation:**
```python
if current_tick < tick_lower:
    amount0 = calculate_from_liquidity(...)
    amount1 = 0
elif current_tick >= tick_upper:
    amount0 = 0
    amount1 = calculate_from_liquidity(...)
else:
    amount0 = calculate_partial(...)
    amount1 = calculate_partial(...)
```

### Fee Collection

For V3, the adapter includes `tokensOwed0` and `tokensOwed1` (uncollected fees) in the total amounts.

---

## Logging Output

### Uniswap V3 Example:
```
INFO  Uniswap V3: found 3 positions for subvault 0x...
DEBUG Uniswap V3: position index 0 = token ID 123456
DEBUG Uniswap V3: position index 1 = token ID 789012
DEBUG Uniswap V3: position index 2 = token ID 345678

INFO  Uniswap V3 position 123456: token0=0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2 amount0=1500000000000000000,
      token1=0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48 amount1=3000000000,
      fee=3000, liquidity=50000000000000000, tick_range=[-887220, 887220], current_tick=201234

INFO  Uniswap V3: fetched 2 unique tokens from 3 positions for subvault 0x...
```

### Uniswap V4 Example:
```
INFO  Uniswap V4: found 2 positions for subvault 0x...
INFO  Uniswap V4 position 555: currency0=0x... amount0=2000000000000000000,
      currency1=0x... amount1=4000000000, fee=3000, liquidity=60000000000000000,
      tick_range=[-200000, 200000], current_tick=150000, hook=0x1234567890abcdef...
```

---

## Differences: V3 vs V4

| Feature | V3 | V4 |
|---------|----|----|
| Pool identification | token0, token1, fee | PoolKey (adds tickSpacing, hooks) |
| Pool contracts | Separate contract per pool | Single PoolManager for all pools |
| Fee structure | Fixed fees (500, 3000, 10000) | Dynamic fees (customizable) |
| Hooks | No hooks | Hook contracts can modify behavior |
| Liquidity math | Concentrated liquidity | Same as V3 |
| NFT enumeration | ERC721Enumerable | ERC721Enumerable |

---

## Configuration Examples

### Simple V3 Setup (Most Common):
```toml
# No adapter config needed - uses mainnet defaults

[[subvault_adapters]]
subvault_address = "0xYourSubvaultWithUniV3Positions"
additional_adapters = ["uniswap_v3"]
```

### V4 Setup (When Available):
```toml
[adapters.uniswap_v4]
position_manager = "0xV4PositionManagerAddress"

[[subvault_adapters]]
subvault_address = "0xYourSubvaultWithUniV4Positions"
additional_adapters = ["uniswap_v4"]
```

### Mixed LP Strategies:
```toml
# Subvault with both V3 and other DeFi positions
[[subvault_adapters]]
subvault_address = "0xMultiStrategySubvault"
additional_adapters = ["uniswap_v3", "aave_v3", "pendle"]
```

---

## Implementation Notes

### Why No Pool Configuration?

Unlike Pendle or Aave adapters, Uniswap adapters don't need pool configuration because:
1. Position NFTs contain all pool information (token addresses, fee tier, ticks)
2. Pool addresses can be derived from the factory contract
3. ERC721Enumerable lets us discover all positions automatically

### Hook Handling in V4

V4 pools can have custom hooks that modify swap behavior, fee collection, etc. The adapter:
- Includes hook address in PoolKey for correct pool identification
- Logs hook address for transparency
- Uses standard liquidity math (hooks don't affect position value calculation)

### Tick Math

Both adapters implement the full Uniswap tick math:
- `getSqrtRatioAtTick()`: Converts tick to sqrt price
- `getAmount0Delta()`: Calculates token0 from liquidity
- `getAmount1Delta()`: Calculates token1 from liquidity

This ensures accurate position valuation at any tick.

---

## Testing

Quick test to verify adapters are registered:

```bash
python -c "from src.tq_oracle.adapters.asset_adapters import ADAPTER_REGISTRY; print('uniswap_v3' in ADAPTER_REGISTRY, 'uniswap_v4' in ADAPTER_REGISTRY)"
# Should print: True True
```

---

## Future Enhancements

Potential future additions:
- V3 fee tier optimization analysis
- V4 hook compatibility checks
- Position health monitoring (out-of-range warnings)
- Historical yield tracking
- Gas cost estimation for rebalancing

---

## References

- [Uniswap V3 NonfungiblePositionManager](https://docs.uniswap.org/contracts/v3/reference/periphery/NonfungiblePositionManager)
- [Uniswap V4 PositionManager](https://docs.uniswap.org/contracts/v4/reference/periphery/PositionManager)
- [Uniswap V3 Liquidity Math](https://docs.uniswap.org/sdk/v3/guides/liquidity/fetching-positions)
- [Uniswap V4 Hooks](https://docs.uniswap.org/contracts/v4/concepts/hooks)
