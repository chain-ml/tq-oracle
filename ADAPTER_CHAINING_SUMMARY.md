# Adapter Chaining Implementation Summary

## Problem Solved

When a subvault holds Aave positions with PT tokens or ERC4626 vault tokens as collateral, the oracle needs to convert these "wrapped" tokens to their underlying assets before pricing.

**Example scenario:**
```
Aave Subvault holds:
  → aToken (aPT-jrUSDe) backed by PT-jrUSDe
  → PT-jrUSDe needs conversion → USDe
  → USDe can be priced via Chainlink → ETH
```

## Solution: Adapter Chaining

Adapters now run **sequentially per subvault** and can transform outputs from previous adapters.

### Flow:
```
Subvault with additional_adapters = ["aave_v3", "pendle", "erc4626"]

1. aave_v3 runs:
   → Returns [PT-jrUSDe: 1000, sNUSD: 500, WETH: 2]

2. pendle runs with previous_assets=[PT-jrUSDe: 1000, sNUSD: 500, WETH: 2]:
   → Detects PT-jrUSDe is a PT token
   → Converts PT-jrUSDe 1000 → USDe 980 (via Pendle oracle)
   → Returns [USDe: 980, sNUSD: 500, WETH: 2]

3. erc4626 runs with previous_assets=[USDe: 980, sNUSD: 500, WETH: 2]:
   → Detects sNUSD is an ERC4626 vault token
   → Converts sNUSD 500 → nUSD 510 (via convertToAssets)
   → Returns [USDe: 980, nUSD: 510, WETH: 2]

Final result: [USDe: 980, nUSD: 510, WETH: 2] → Price Adapters → ETH
```

---

## Changes Made

### 1. Base Adapter Signature (src/tq_oracle/adapters/asset_adapters/base.py)

Added optional `previous_assets` parameter:

```python
@abstractmethod
async def fetch_assets(
    self,
    subvault_address: str,
    previous_assets: list[AssetData] | None = None,  # NEW
) -> list[AssetData]:
    """Fetch asset data for the given subvault.

    Args:
        subvault_address: Subvault to query
        previous_assets: Optional results from previous adapters in chain.
                        Allows adapters to transform/convert wrapped tokens.
    """
```

**Backwards compatible**: Existing adapters ignore this parameter.

---

### 2. Pendle Adapter (src/tq_oracle/adapters/asset_adapters/pendle.py)

**Added PT token conversion from previous adapters:**

```python
async def fetch_assets(
    self,
    subvault_address: str,
    previous_assets: list[AssetData] | None = None,
) -> list[AssetData]:
    # ... discover own PT/LP positions ...

    # NEW: Convert any PT tokens from previous adapters
    if previous_assets:
        for asset in previous_assets:
            converted = await self._try_convert_pt_token(asset)
            results.append(converted if converted else asset)

    return results

async def _try_convert_pt_token(self, asset: AssetData) -> AssetData | None:
    """Try to convert asset if it's a PT token from our configured markets."""
    pt_address = asset.asset_address.lower()

    for market_name, market_config in self.markets.items():
        market_pt_address = await self._get_pt_address(market_config["market"])

        if market_pt_address.lower() == pt_address:
            # This IS a PT token we can convert!
            pt_rate = await self._get_pt_to_asset_rate(market_config["market"])
            asset_value = (asset.amount * pt_rate) // (10**18)

            return AssetData(
                asset_address=market_config["accounting_asset"],
                amount=asset_value,
                tvl_only=asset.tvl_only,
            )

    return None  # Not a PT token - pass through
```

**Logging:**
```
INFO Pendle: detected PT token 0x... from previous adapter, converting to 0x...
INFO Pendle: converted PT pt_jrusde_27mar2025: balance=1000, rate=980000000000000000, value=980 USDe
```

---

### 3. ERC4626 Adapter (src/tq_oracle/adapters/asset_adapters/rwa/erc4626_vault.py)

**Added vault token conversion from previous adapters:**

```python
async def fetch_assets(
    self,
    subvault_address: str,
    previous_assets: list[AssetData] | None = None,
) -> list[AssetData]:
    # ... discover own vault positions ...

    # NEW: Convert any ERC4626 vault tokens from previous adapters
    if previous_assets:
        for asset in previous_assets:
            converted = await self._try_convert_vault_token(asset)
            results.append(converted if converted else asset)

    return results

async def _try_convert_vault_token(self, asset: AssetData) -> AssetData | None:
    """Try to convert asset if it's an ERC4626 vault token we know about."""
    vault_address = asset.asset_address.lower()

    for vault_name, vault_config in self.vaults.items():
        if vault_config["vault_token"].lower() == vault_address:
            # This IS a vault token we can convert!
            underlying_amount = await self._convert_to_assets(
                vault_config["vault_token"],
                asset.amount
            )

            return AssetData(
                asset_address=vault_config["underlying_asset"],
                amount=underlying_amount,
                tvl_only=asset.tvl_only,
            )

    return None  # Not a vault token - pass through
```

**Logging:**
```
INFO ERC4626: detected vault token 0x... from previous adapter, converting to underlying
INFO ERC4626: converted vault token snusd: shares=500, underlying=510 (0x...)
```

---

### 4. Pipeline Changes (src/tq_oracle/pipeline/assets.py)

**Sequential execution with chaining:**

```python
# Group adapter tasks by subvault for sequential execution
subvault_adapter_chains: dict[str, list[tuple[Any, str]]] = {}
for subvault_addr, adapter, adapter_name in adapter_tasks:
    key = subvault_addr.lower()
    if key not in subvault_adapter_chains:
        subvault_adapter_chains[key] = []
    subvault_adapter_chains[key].append((adapter, adapter_name))

async def run_adapter_chain(subvault_addr: str, adapters: list[tuple[Any, str]]):
    """Run adapters sequentially for a subvault, passing results forward."""
    accumulated_assets: list[AssetData] | None = None

    for adapter, adapter_name in adapters:
        # Pass previous adapter results to this adapter
        new_assets = await adapter.fetch_assets(subvault_addr, accumulated_assets)
        accumulated_assets = new_assets  # Chain forward

    return accumulated_assets or []

# Run adapter chains (sequential within subvault, parallel across subvaults)
per_subvault_results = await asyncio.gather(
    *[
        run_adapter_chain(subvault_addr, adapters)
        for subvault_addr, adapters in subvault_adapter_chains.items()
    ],
    return_exceptions=True,
)
```

**Key insight**: Adapters run **sequentially within each subvault** but different subvaults still run **in parallel** for performance.

---

### 5. Configuration (tq-oracle.toml.pre-prod)

**Pendle subvault updated with adapter chain:**

```toml
# Pendle subvault - Tracks PT tokens + sNUSD/jrUSDe vault tokens + Aave positions
# Order matters: aave_v3 discovers positions (may include PT/vault tokens)
#                pendle converts any PT tokens to underlying assets
#                erc4626 converts any vault tokens to underlying assets
[[subvault_adapters]]
subvault_address = "0xf37D9264099Fc67448e56e60BC33095F2b2a95d3"
additional_adapters = ["aave_v3", "pendle", "erc4626"]
```

**Order is critical!** Adapters run left-to-right, each transforming the results from the previous.

---

### 6. All Other Adapters Updated

Updated these adapters to accept (but ignore) the new parameter:
- ✅ aave_v3.py
- ✅ idle_balances.py
- ✅ stakewise.py
- ✅ streth.py
- ✅ rwa/snusd.py
- ✅ uniswap/uniswap_v3.py
- ✅ uniswap/uniswap_v4.py

**All backwards compatible** - they don't use `previous_assets`, just accept it.

---

## Usage Examples

### Example 1: Aave with PT Collateral

```toml
[[subvault_adapters]]
subvault_address = "0xYourAaveSubvault"
additional_adapters = ["aave_v3", "pendle"]
```

**Flow:**
1. Aave discovers: [aPT-jrUSDe: 1000]
2. Pendle converts: PT-jrUSDe 1000 → USDe 980
3. Price adapters: USDe → ETH

### Example 2: Aave with ERC4626 Collateral

```toml
[[subvault_adapters]]
subvault_address = "0xYourAaveSubvault"
additional_adapters = ["aave_v3", "erc4626"]
```

**Flow:**
1. Aave discovers: [a-sNUSD: 500]
2. ERC4626 converts: sNUSD 500 → nUSD 510
3. Price adapters: nUSD (via CoinGecko) → ETH

### Example 3: Full Chain (Aave + Both Conversions)

```toml
[[subvault_adapters]]
subvault_address = "0xMultiAssetSubvault"
additional_adapters = ["aave_v3", "pendle", "erc4626"]
```

**Flow:**
1. Aave discovers: [PT-jrUSDe: 1000, sNUSD: 500, WETH: 2]
2. Pendle converts: PT-jrUSDe → USDe 980
3. ERC4626 converts: sNUSD → nUSD 510
4. Final: [USDe: 980, nUSD: 510, WETH: 2] → ETH

---

## Key Benefits

1. **Minimal changes**: Just added optional parameter + conversion logic
2. **Backwards compatible**: Existing adapters work without modification
3. **Flexible**: Order matters - you control conversion sequence
4. **Composable**: Any adapter can transform any other adapter's output
5. **Efficient**: Still parallel across subvaults, sequential only within

---

## Important Notes

### Order Matters!

```toml
# CORRECT: Aave discovers, Pendle converts PT tokens
additional_adapters = ["aave_v3", "pendle", "erc4626"]

# WRONG: Pendle runs first, sees nothing to convert
additional_adapters = ["pendle", "aave_v3", "erc4626"]
```

### Adapters Only Convert What They Know

- Pendle only converts PT tokens for markets in its config
- ERC4626 only converts vault tokens in its config
- Unknown tokens pass through unchanged

### No Duplication

When `aave_v3` returns PT-jrUSDe, and then `pendle` converts it to USDe, the **PT-jrUSDe is replaced** with USDe. You don't get both - you get the final converted result.

---

## Testing

1. **Check adapter signatures updated:**
   ```bash
   grep -r "async def fetch_assets.*previous_assets" src/tq_oracle/adapters/asset_adapters/
   ```

2. **Test with Aave subvault:**
   ```toml
   [[subvault_adapters]]
   subvault_address = "0xf37D9264099Fc67448e56e60BC33095F2b2a95d3"
   additional_adapters = ["aave_v3", "pendle", "erc4626"]
   ```

3. **Look for conversion logs:**
   ```
   INFO Pendle: detected PT token ... from previous adapter
   INFO Pendle: converted PT ... balance=X, rate=Y, value=Z
   INFO ERC4626: detected vault token ... from previous adapter
   INFO ERC4626: converted vault token ... shares=X, underlying=Y
   ```

---

## Future Extensions

This pattern enables many other use cases:

- **Wrapped tokens**: ETH → WETH conversion
- **Rebasing tokens**: stETH → WETH conversion
- **Synthetic assets**: Convert synthetics to underlying collateral
- **Leverage unwinding**: Decompose leveraged positions into base assets
- **Cross-protocol composition**: Any adapter can transform any other's output

The infrastructure is now in place for arbitrary asset transformations!
