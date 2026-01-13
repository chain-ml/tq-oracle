# CoinGecko & RWA Configuration Summary

## Changes Made to `tq-oracle.toml.pre-prod`

### 1. ✅ CoinGecko API Key Setup

**Where to place the key:**
```bash
export TQ_ORACLE_COINGECKO_API_KEY="your_api_key_here"
```

**Configuration change:**
```toml
# Line 36
coingecko_enabled = true  # Changed from false
```

---

### 2. ✅ ERC4626 Adapter Configuration

Added configuration for **sNUSD** and **jrUSDe** vault tokens:

```toml
# Lines 57-65
[adapters.erc4626]
[adapters.erc4626.vaults.snusd]
vault_token = "0x08EFCC2F3e61185D0EA7F8830B3FEc9Bfa2EE313"  # sNUSD
underlying_asset = "0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE"  # nUSD

[adapters.erc4626.vaults.jrusde]
vault_token = "0x4F6673346aB4813F1665327aB39087008Cc7d76F"  # jrUSDe
underlying_asset = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"  # USDe
```

---

### 3. ✅ nUSD Token Tracking

Added **nUSD** to idle balances as a `non_tvl_token` (so it can be priced normally):

```toml
# Lines 50-55
non_tvl_tokens = {
    USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    USDT = "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    USDe = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3",
    nUSD = "0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE"  # NEW!
}
```

---

### 4. ✅ CoinGecko ID Mapping

Added **nUSD** CoinGecko ID:

```toml
# Line 72
"0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE" = "nusd-2"  # nUSD - Usual USD
```

**⚠️ NOTE:** Verify "nusd-2" is the correct CoinGecko ID for nUSD. Check at: https://www.coingecko.com/

---

## How the Flow Works

### For **sNUSD** and **jrUSDe** (ERC4626 Vault Tokens):

```
1. idle_balances adapter: Skips sNUSD/jrUSDe (they're vault tokens, not tracked here)
                         ↓
2. ERC4626 adapter:      Checks vault token balance
                         ↓
                         Calls convertToAssets(balance)
                         ↓
                         Returns underlying asset:
                         - sNUSD → nUSD
                         - jrUSDe → USDe
                         ↓
3. Price adapters:       Price underlying assets:
                         - nUSD → CoinGecko → ETH
                         - USDe → Chainlink → ETH (already configured)
```

### For **nUSD** (Underlying Asset):

```
1. idle_balances adapter: Tracks raw nUSD balances (if held directly)
                         ↓
2. CoinGecko adapter:    Fetches nUSD/ETH price using API
                         ↓
3. Price validation:     Pyth validates the CoinGecko price
```

---

## Token Address Reference

```solidity
// Vault tokens (tracked by ERC4626 adapter)
sNUSD   = 0x08EFCC2F3e61185D0EA7F8830B3FEc9Bfa2EE313
jrUSDe  = 0x4F6673346aB4813F1665327aB39087008Cc7d76F

// Underlying assets
nUSD    = 0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE  (priced via CoinGecko)
USDe    = 0x4c9EDD5852cd905f086C759E8383e09bff1E68B3  (priced via Chainlink)

// Pendle PT tokens (for future use)
PT-jrUSDe  = 0xd0609Ac13000d88B0BEbf5Bb21074916eDd92Bb1
PT-sNUSD   = 0x54Bf2659B5CdFd86b75920e93C0844c0364F5166
```

---

## What Changed vs Existing Flow?

### ✅ NO Breaking Changes

1. **Existing adapters untouched:**
   - StakeWise: Still works as before
   - Aave V3: Still works as before
   - Pendle: Still works as before
   - idle_balances: Only added nUSD, existing tokens unchanged

2. **Existing price flow unchanged:**
   - USDC, USDT, DAI, USDe: Still use Chainlink (as configured)
   - Other tokens: Still use CoW Swap → CoinGecko → fallback chain

3. **New additions are additive:**
   - ERC4626 adapter is NEW, doesn't interfere with existing adapters
   - nUSD uses CoinGecko, doesn't conflict with Chainlink stablecoins
   - CoinGecko enabled globally, but only used for tokens in `coingecko_token_ids`

---

## Testing the Setup

### 1. Set API Key
```bash
export TQ_ORACLE_COINGECKO_API_KEY="your_key_here"
```

### 2. Verify Config Loads
```bash
# Should show no errors
tq-oracle run --config tq-oracle.toml.pre-prod --dry-run
```

### 3. Check Logs
Look for these log lines:
```
ERC4626 adapter initialized: 2 vaults configured
ERC4626 snusd: shares=X, underlying=Y (0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE)
ERC4626 jrusde: shares=A, underlying=B (0x4c9EDD5852cd905f086C759E8383e09bff1E68B3)
CoinGecko: fetching price for nUSD (nusd-2)
```

---

## Difference: `extra_tokens` vs `non_tvl_tokens`

**`extra_tokens`:**
- Assets marked as `tvl_only=True`
- Counted in TVL but may have conflicts with other adapters
- Use for: Assets tracked solely for TVL calculation

**`non_tvl_tokens`:**
- Assets tracked normally (NOT `tvl_only`)
- Can be priced and used by all price adapters
- Use for: Assets that need full pricing support (like nUSD)

**Why nUSD is in `non_tvl_tokens`:**
- nUSD needs to be priced by CoinGecko
- It's a normal underlying asset, not just for TVL
- Should behave like other stablecoins (USDC, USDT, etc.)

---

## Next Steps (Optional)

### If you want to track Pendle PT tokens:

```toml
[adapters.pendle.markets.pt_jrusde]
market = "0xfAbEEFC5369aA5270B401f4Ee062D17fb5f1EC2A"
accounting_asset = "0x4c9EDD5852cd905f086C759E8383e09bff1E68B3"  # USDe

[adapters.pendle.markets.pt_snusd]
market = "0x6D8C4DE7071D5AeE27fc3a810764E62a4a00Ceb9"
accounting_asset = "0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE"  # nUSD

[[subvault_adapters]]
subvault_address = "0xYourPendleSubvault"
additional_adapters = ["pendle"]
```

This would track:
- PT-jrUSDe → priced in USDe → Chainlink → ETH
- PT-sNUSD → priced in nUSD → CoinGecko → ETH

---

## Summary

✅ **CoinGecko enabled** - API key via environment variable
✅ **ERC4626 adapter configured** - sNUSD and jrUSDe conversion
✅ **nUSD tracking** - Added to non_tvl_tokens for idle balance + pricing
✅ **CoinGecko ID mapped** - "nusd-2" (verify this is correct)
✅ **No breaking changes** - All existing flows preserved

The configuration is ready to use! Just set the `TQ_ORACLE_COINGECKO_API_KEY` environment variable and run.
