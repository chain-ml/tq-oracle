# New Adapters Implementation Guide

This document describes the newly added adapters for the TQ Oracle system.

## Overview

Three new components have been added to support additional DeFi protocols:

1. **Aave V3 Adapter** - Tracks supply (aTokens) and borrow (debt tokens) positions
2. **Pendle Adapter** - Marks PT and LP positions to market using Pendle oracle
3. **Asset Conversion Utilities** - Helper functions for RWA and ERC-4626 conversions

---

## 1. Aave V3 Adapter

### Purpose
Tracks Aave V3 lending positions (and forks like Spark) by querying aToken balances (supply) and variable debt token balances (borrows).

### How It Works
- Queries balances for configured aTokens (supply positions)
- Queries balances for configured variable debt tokens (borrow positions)
- Returns supply as positive amounts and borrows as **negative amounts**
- aTokens and debt tokens inherit pricing from their underlying assets
- Supports **multiple pools/forks** via named instances (e.g., Aave + Spark simultaneously)

### Configuration

#### Single Pool Configuration (Simple)
```toml
[adapters.aave_v3]
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
base_asset_type = "eth"  # 'eth' or 'usd'
supply_tokens = { WETH = "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8" }
borrow_tokens = { WETH = "0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE" }
```

Usage:
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvaultAddress"
additional_adapters = ["aave_v3"]
```

#### Multi-Pool Configuration (Aave + Spark)

**NEW:** Track multiple Aave V3 pools or forks using named instances:

```toml
# Aave V3 Official
[[adapters.aave_v3]]
name = "aave"
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
base_asset_type = "eth"
supply_tokens = { WETH = "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8" }
borrow_tokens = { WETH = "0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE" }

# Spark (Aave V3 fork)
[[adapters.aave_v3]]
name = "spark"
pool_address = "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"
supply_tokens = { WETH = "0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB" }
borrow_tokens = { WETH = "0x2e7576042566f8D6990e07A1B61Ad1efd86Ae70d" }
```

Usage (reference by instance name):
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvaultAddress"
additional_adapters = ["aave_v3.aave", "aave_v3.spark"]  # Track both!
```

> **📘 See [AAVE_MULTI_POOL_GUIDE.md](AAVE_MULTI_POOL_GUIDE.md) for complete multi-pool documentation including Spark token addresses and migration guide.**

#### Per-Subvault Override
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvaultAddress"
additional_adapters = ["aave_v3"]
adapter_overrides = { aave_v3 = {
    supply_tokens = { USDC = "0x..." },
    borrow_tokens = { USDT = "0x..." }
} }
```

### Key Features
- **Multi-pool support** - Track Aave V3 + Spark + any fork simultaneously via named instances
- **Mainnet only** (for now) - automatically skips on other networks
- **Default token lists** - Comes with common mainnet tokens (WETH, wstETH, USDC, USDT, USDe)
- **Configurable** - Can override tokens per chain or per subvault
- **Negative borrows** - Borrow positions are returned as negative amounts for proper accounting
- **Backwards compatible** - Single pool configuration still works without changes

### Files
- Implementation: [src/tq_oracle/adapters/asset_adapters/aave_v3.py](src/tq_oracle/adapters/asset_adapters/aave_v3.py)
- Settings: [src/tq_oracle/settings.py](src/tq_oracle/settings.py) (AaveV3AdapterSettings)
- Constants: [src/tq_oracle/constants.py](src/tq_oracle/constants.py) (AAVE_V3_*)

---

## 2. Pendle Adapter

### Purpose
Marks Pendle Principal Token (PT) and LP positions to market using the Pendle oracle.

### How It Works
1. Queries PT token balances for configured markets
2. Queries LP token balances (market address = LP token)
3. Calls Pendle oracle's `getPtToAssetRate()` and `getLpToAssetRate()`
4. Returns positions valued in the configured accounting asset (e.g., USDC, WETH)

### Configuration

#### Global Configuration (tq-oracle.toml)
```toml
[adapters.pendle]
oracle_address = "0x9a9Fa8338dd5E5B2188006f1Cd2Ef26d921650C2"

# Define markets to track
[adapters.pendle.markets.usde_jul2025]
market = "0xYourPendleMarketAddress"
accounting_asset = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"  # USDC

[adapters.pendle.markets.wsteth_dec2025]
market = "0xAnotherMarketAddress"
accounting_asset = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"  # WETH
```

#### Per-Subvault Usage
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvaultAddress"
additional_adapters = ["pendle"]
```

### Key Features
- **Mainnet only** (for now)
- **TWAP pricing** - Uses 15-minute TWAP by default (configurable)
- **PT and LP support** - Tracks both Principal Tokens and LP positions
- **Flexible accounting** - Each market can have its own accounting asset
- **Oracle-based MTM** - Uses Pendle's official oracle for accurate pricing

### Oracle Functions Used
- `getPtToAssetRate(market, duration)` - Converts PT → SY → Asset internally
- `getLpToAssetRate(market, duration)` - Converts LP → SY → Asset internally
- Both functions return rates in 18 decimals

### Files
- Implementation: [src/tq_oracle/adapters/asset_adapters/pendle.py](src/tq_oracle/adapters/asset_adapters/pendle.py)
- Settings: [src/tq_oracle/settings.py](src/tq_oracle/settings.py) (PendleAdapterSettings)
- Constants: [src/tq_oracle/constants.py](src/tq_oracle/constants.py) (PENDLE_ORACLE_MAINNET)

---

## 3. Asset Conversion Utilities

### Purpose
Provides flexible conversion utilities for future RWA and custom asset integrations.

### Available Utilities

#### USD to ETH Conversion
```python
from tq_oracle.utils.asset_conversion import convert_usd_to_eth

eth_value = await convert_usd_to_eth(
    usd_amount=1000_000000,  # 1000 USDC (6 decimals)
    usd_asset_address="0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    prices=price_data,
)
```

#### ERC-4626 Vault Conversion
```python
from tq_oracle.utils.asset_conversion import convert_shares_to_assets_erc4626

underlying_addr, underlying_amount = await convert_shares_to_assets_erc4626(
    vault_address="0xRWAVaultAddress",
    shares=100_000000000000000000,  # 100 shares (18 decimals)
    config=oracle_settings,
)
```

#### RWA Converter Class
For more complex RWA workflows:
```python
from tq_oracle.utils.asset_conversion import RWAConverter

converter = RWAConverter(config)

# Convert vault shares to underlying asset
underlying_addr, amount = await converter.convert_to_underlying(vault_addr, shares)

# Convert vault shares directly to ETH value
eth_value = await converter.convert_to_eth(vault_addr, shares, prices)

# Convert vault shares to USD (if underlying is USD stablecoin)
usd_addr, usd_amount = await converter.convert_to_usd(vault_addr, shares)
```

### Use Cases
1. **Current**: Supporting Aave V3 and Pendle integrations
2. **Future**: RWA vaults with ERC-4626 standard
3. **Future**: Custom Pendle strategies that need post-processing
4. **Future**: Synthetic assets and wrapped tokens

### Files
- Implementation: [src/tq_oracle/utils/asset_conversion.py](src/tq_oracle/utils/asset_conversion.py)

---

## Integration Flow

### How Assets Flow Through the System

```
1. Adapter Collection (Per Subvault)
   ├─ IdleBalancesAdapter → Direct token balances
   ├─ AaveV3Adapter → aToken balances (+) and debt token balances (-)
   ├─ PendleAdapter → PT and LP valued in accounting assets
   └─ Other adapters...

2. Asset Aggregation
   └─ All AssetData combined into single dict[asset_address, total_amount]

3. Price Fetching
   ├─ CowSwapAdapter → Fetch prices for all assets in ETH
   ├─ PythAdapter → Alternative price source
   └─ ETHAdapter → Set ETH/WETH prices

4. Total Assets Calculation
   └─ sum(amount * price / 10^18) for all assets

5. Final Price Derivation
   └─ OracleHelper.getPricesD18(vault, total_assets, asset_prices)
```

### Key Points

1. **Aave V3 Positions**:
   - aTokens are priced at their underlying asset price (1:1)
   - Debt tokens are negative amounts
   - Net position = supply - borrows

2. **Pendle Positions**:
   - PT and LP are converted to accounting assets
   - Accounting assets are then priced by normal price adapters
   - Final value in ETH after price conversion

3. **Asset Conversion**:
   - RWA utilities available for future use
   - ERC-4626 vaults can be converted to underlying
   - Underlying then priced normally

---

## Example Configurations

### Simple Aave V3 Only
```toml
[[subvault_adapters]]
subvault_address = "0xYourLeveragedWstETHVault"
additional_adapters = ["aave_v3"]
```

### Pendle + Aave V3 Combined
```toml
# Global Pendle config
[adapters.pendle.markets.usde_jul2025]
market = "0xPendleMarket"
accounting_asset = "0xUSDC"

# Subvault using both
[[subvault_adapters]]
subvault_address = "0xComplexStrategy"
additional_adapters = ["aave_v3", "pendle"]
```

### Custom Aave V3 Tokens
```toml
[[subvault_adapters]]
subvault_address = "0xSpecialVault"
additional_adapters = ["aave_v3"]
adapter_overrides = { aave_v3 = {
    supply_tokens = { DAI = "0xCustomAToken" },
    borrow_tokens = { USDC = "0xCustomDebtToken" }
} }
```

---

## Testing Checklist

Before deploying, verify:

- [ ] Aave V3 adapter correctly identifies supply and borrow positions
- [ ] Borrow amounts are properly negated
- [ ] Pendle oracle rates are fetched correctly
- [ ] PT and LP balances are detected
- [ ] Accounting assets are properly configured
- [ ] Price adapters can price the accounting assets
- [ ] Total assets calculation includes all positions
- [ ] Net positions (supply - borrow) are correct

---

## Future Enhancements

### Potential Extensions

1. **Multi-chain Support**:
   - Add Aave V3 on Arbitrum, Optimism, Base
   - Add Pendle on supported chains

2. **RWA Direct Integration**:
   - Create dedicated RWA adapter using conversion utilities
   - Support custom oracle calls for RWA pricing

3. **Additional Pendle Features**:
   - YT (Yield Token) support
   - SY (Standardized Yield) token support
   - Custom TWAP duration per market

4. **Aave Enhancements**:
   - Stable debt token support
   - E-Mode category tracking
   - Isolation mode support

---

## 4. Chainlink Price Adapter

### Purpose
Provides stable, oracle-based pricing for USD stablecoins instead of relying on potentially noisy DEX bid-ask spreads.

### Why Use Chainlink for Stablecoins?

**Problem with CoW Swap**:
- CoW Swap API uses bid-ask spreads from orderbook
- Can be noisy with large spreads during volatile periods
- Stablecoin prices can fluctuate unnecessarily

**Chainlink Solution**:
- Uses Chainlink ETH/USD oracle (highly reliable, manipulation-resistant)
- Inverts price to get USD in ETH
- Applies consistent pricing to all configured stablecoins
- Much more stable than DEX pricing

### How It Works

1. **Fetch ETH/USD Price**: Queries Chainlink oracle (e.g., 1 ETH = $3000, 8 decimals)
2. **Invert to USD/ETH**: Calculates 1 USD = X ETH (18 decimals)
   - Formula: `USD_in_ETH = (10^18 * 10^feed_decimals) / ETH_USD_price`
   - Example: `(10^18 * 10^8) / 300000000000 = 333333333333333` (0.000333... ETH)
3. **Apply to Stablecoins**: Uses this price for all configured stablecoins
4. **Normalize for Decimals**: Adjusts price based on token decimals (6 for USDC/USDT, 18 for DAI)

### Configuration

#### Enable Chainlink (tq-oracle.toml)
```toml
# Chainlink settings
chainlink_enabled = true
chainlink_eth_usd_feed = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"  # Mainnet

# List stablecoins to price via Chainlink
chainlink_stablecoins = [
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
    "0x6B175474E89094C44Da98b954EedeAC495271d0F",  # DAI
]
```

#### Auto-Detection by Network
If you don't specify `chainlink_eth_usd_feed`, it will use the default for your network:
- Mainnet: `0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419`
- Sepolia: `0x694AA1769357215DE4FAC081bf1f309aDC325306`
- Base: `0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70`

### Adapter Priority

Price adapters run in this order:
1. **ChainlinkAdapter** - Prices configured stablecoins only
2. **CowSwapAdapter** - Prices all other assets
3. **ETHAdapter** - Prices ETH, WETH, osETH

If a stablecoin is in `chainlink_stablecoins`, Chainlink prices it. Otherwise, CoW Swap prices it.

### Mathematical Example

**Scenario**: Price 1000 USDC in ETH using Chainlink

**Step 1**: Chainlink says 1 ETH = $3000 (8 decimals: `300000000000`)

**Step 2**: Invert to get USD in ETH:
```
USD_in_ETH = (10^18 * 10^8) / 300000000000
           = 10^26 / 300000000000
           = 333333333333333 wei per USD (18 decimals)
```

**Step 3**: USDC has 6 decimals, so normalize:
```
price_normalized = 333333333333333 // (10^(18-6))
                 = 333333333333333 // 10^12
                 = 333333
```

**Step 4**: Calculate ETH value of 1000 USDC:
```
1000 USDC = 1000000000 units (6 decimals)
ETH_value = (1000000000 * 333333) / 10^18
          = 333333000000 / 10^18
          = 0.000333333 ETH
```

### Key Features
- **Optional** - Disabled by default, enable with `chainlink_enabled = true`
- **Selective** - Only prices configured stablecoins
- **Multi-chain** - Supports Mainnet, Sepolia, Base (with appropriate feeds)
- **Stable pricing** - Eliminates bid-ask spread noise
- **Automatic decimals** - Handles both 6-decimal (USDC, USDT) and 18-decimal (DAI) stablecoins

### Files
- Implementation: [src/tq_oracle/adapters/price_adapters/chainlink.py](src/tq_oracle/adapters/price_adapters/chainlink.py)
- Settings: [src/tq_oracle/settings.py](src/tq_oracle/settings.py) (chainlink_* fields)
- Constants: [src/tq_oracle/constants.py](src/tq_oracle/constants.py) (CHAINLINK_FEEDS)

---

## Support

For issues or questions:
- Review adapter implementations in `src/tq_oracle/adapters/asset_adapters/`
- Check configuration examples in `tq-oracle.toml.example`
- Refer to existing adapter patterns (StakeWise, StrETH)
