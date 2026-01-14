# Aave V3 Multi-Pool/Fork Support

## Overview

The Aave V3 adapter now supports tracking multiple Aave V3 pools and forks (e.g., Aave V3 + Spark) simultaneously. This is achieved through **named instances** that allow you to configure multiple pool addresses and token lists.

## Use Cases

- **Track both Aave V3 and Spark**: Monitor positions across the official Aave V3 pool and Spark (MakerDAO's Aave fork)
- **Multiple networks**: Use different Aave deployments (though currently limited to mainnet)
- **Custom forks**: Track any Aave V3-compatible fork with its own pool address and tokens

## Configuration

### Single Pool (Backwards Compatible)

If you only need one Aave V3 pool, use the simple configuration:

```toml
[adapters.aave_v3]
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
base_asset_type = "eth"
supply_tokens = { WETH = "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8" }
borrow_tokens = { WETH = "0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE" }
```

Usage:
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["aave_v3"]
```

### Multiple Pools (Aave + Spark Example)

To track multiple pools, use the array-of-tables syntax `[[adapters.aave_v3]]` with unique `name` fields:

```toml
# Aave V3 (official pool)
[[adapters.aave_v3]]
name = "aave"
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
base_asset_type = "eth"
supply_tokens = {
    WETH = "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8",
    wstETH = "0x0B925eD163218f6662a35e0f0371Ac234f9E9371",
    USDC = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
    USDT = "0x23878914EFE38d27C4D67Ab83ed1b93A74D4086a",
    USDe = "0x4F5923Fc5FD4a93352581b38B7cD26943012DECF"
}
borrow_tokens = {
    WETH = "0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE",
    wstETH = "0xC96113eED8cAB59cD8A66813bCB0cEb29F06D2e4",
    USDC = "0x72E95b8931767C79bA4EeE721354d6E99a61D004"
}

# Spark (Aave V3 fork by MakerDAO)
[[adapters.aave_v3]]
name = "spark"
pool_address = "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"
base_asset_type = "eth"
supply_tokens = {
    WETH = "0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB",   # spWETH
    wstETH = "0x12B54025C112Aa61fAce2CDB7118740875A566E9",  # spwstETH
    DAI = "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B"     # spDAI
}
borrow_tokens = {
    WETH = "0x2e7576042566f8D6990e07A1B61Ad1efd86Ae70d",   # Spark variable debt WETH
    DAI = "0xf705d2B7e92B3F38e6ae7afaDAA2fEE110fE5914"     # Spark variable debt DAI
}
```

Usage (reference by instance name):
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["aave_v3.aave", "aave_v3.spark"]  # Track both!
```

## Instance Naming

The adapter name format is: `aave_v3.{name}`

- `aave_v3` → Uses first/default config (backwards compatible)
- `aave_v3.aave` → Uses instance with `name = "aave"`
- `aave_v3.spark` → Uses instance with `name = "spark"`
- `aave_v3.custom` → Uses instance with `name = "custom"`

**Important**: The `name` field in the TOML **must match** the suffix after the dot in `additional_adapters`.

## How It Works

### 1. Configuration Storage (`settings.py`)

```python
# Single config (backwards compatible)
aave_v3: AaveV3AdapterSettings

# OR multiple configs
aave_v3: list[AaveV3AdapterSettings]
```

Each `AaveV3AdapterSettings` has:
- `name`: Optional instance identifier
- `pool_address`: The Aave/Spark pool address
- `supply_tokens`: Dict of aToken addresses
- `borrow_tokens`: Dict of debt token addresses
- `base_asset_type`: "eth" or "usd"

### 2. Adapter Name Parsing (`__init__.py`)

```python
parse_adapter_name("aave_v3.spark")
# Returns: ("aave_v3", "spark")
```

### 3. Instance Config Lookup (`assets.py`)

```python
instance_config = s.adapters.get_aave_v3_config("spark")
# Returns the config where name == "spark"
```

### 4. Adapter Instantiation

The instance-specific config is passed as overrides to the adapter constructor:

```python
AaveV3Adapter(settings, **instance_config.model_dump())
```

## Token Address Discovery

### Aave V3 (Mainnet)

Common aTokens:
- **WETH**: `0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8`
- **wstETH**: `0x0B925eD163218f6662a35e0f0371Ac234f9E9371`
- **USDC**: `0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c`
- **USDT**: `0x23878914EFE38d27C4D67Ab83ed1b93A74D4086a`
- **USDe**: `0x4F5923Fc5FD4a93352581b38B7cD26943012DECF`

Variable Debt Tokens:
- **WETH**: `0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE`
- **wstETH**: `0xC96113eED8cAB59cD8A66813bCB0cEb29F06D2e4`
- **USDC**: `0x72E95b8931767C79bA4EeE721354d6E99a61D004`
- **USDT**: `0x6df1C1E379bC5a00a7b4C6e67A203333772f45A8`

### Spark (Mainnet)

Pool Address: `0xC13e21B648A5Ee794902342038FF3aDAB66BE987`

Common spTokens (aTokens):
- **spWETH**: `0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB`
- **spwstETH**: `0x12B54025C112Aa61fAce2CDB7118740875A566E9`
- **spDAI**: `0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B`
- **spUSDC**: `0x377C3bd93f2a2984E1E7bE6A5C22c525eD4A4815`
- **spUSDT**: `0x65096f7Bd5f4CE32bb6DD8A734F1964C67Dbf2E7`

Variable Debt Tokens:
- **WETH**: `0x2e7576042566f8D6990e07A1B61Ad1efd86Ae70d`
- **DAI**: `0xf705d2B7e92B3F38e6ae7afaDAA2fEE110fE5914`
- **USDC**: `0x7B70D04099CB9cfb1Db7B6820baDCFC9d1B7F775`

## Complete Example

```toml
# Aave V3 Official
[[adapters.aave_v3]]
name = "aave"
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
base_asset_type = "eth"
supply_tokens = {
    WETH = "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8",
    wstETH = "0x0B925eD163218f6662a35e0f0371Ac234f9E9371",
    USDC = "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c"
}
borrow_tokens = {
    WETH = "0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE",
    USDC = "0x72E95b8931767C79bA4EeE721354d6E99a61D004"
}

# Spark Fork
[[adapters.aave_v3]]
name = "spark"
pool_address = "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"
base_asset_type = "eth"
supply_tokens = {
    WETH = "0x59cD1C87501baa753d0B5B5Ab5D8416A45cD71DB",
    DAI = "0x4DEDf26112B3Ec8eC46e7E31EA5e123490B05B8B"
}
borrow_tokens = {
    WETH = "0x2e7576042566f8D6990e07A1B61Ad1efd86Ae70d",
    DAI = "0xf705d2B7e92B3F38e6ae7afaDAA2fEE110fE5914"
}

# Subvault with positions in both Aave and Spark
[[subvault_adapters]]
subvault_address = "0xF00eF6a1fb7CA3Bc620f51f2aBd91549DD486DaA"
additional_adapters = ["aave_v3.aave", "aave_v3.spark"]
```

## Logging Output

The adapter logs will show which instance is being used:

```
INFO Aave V3 (aave): found 2 supply positions, 1 borrow position for subvault 0xF00...
INFO Aave V3 (spark): found 1 supply position for subvault 0xF00...
```

## Benefits

1. **Consolidate tracking**: Monitor all Aave-compatible positions in one oracle run
2. **Accurate TVL**: Captures positions across all forks without double-counting
3. **Flexible configuration**: Add new forks easily without code changes
4. **Per-subvault control**: Different subvaults can use different pool combinations

## Migration Guide

### From Single Pool

**Before:**
```toml
[adapters.aave_v3]
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
supply_tokens = { WETH = "0x..." }
```

**After (no changes needed):**
Works as-is! Backwards compatible.

**To add Spark:**
```toml
# Change to array syntax and add name
[[adapters.aave_v3]]
name = "aave"
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
supply_tokens = { WETH = "0x..." }

# Add Spark instance
[[adapters.aave_v3]]
name = "spark"
pool_address = "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"
supply_tokens = { WETH = "0x..." }
```

Update subvault adapters:
```toml
# Before
additional_adapters = ["aave_v3"]

# After (use named instances)
additional_adapters = ["aave_v3.aave", "aave_v3.spark"]
```

## Troubleshooting

### Error: "No configuration found for adapter instance 'aave_v3.spark'"

**Cause**: No `[[adapters.aave_v3]]` entry with `name = "spark"`

**Fix**: Add the Spark configuration with the correct name field

### Error: "Adapter 'pendle' does not support named instances"

**Cause**: Tried to use instance syntax (e.g., `pendle.market1`) on an adapter that doesn't support it

**Fix**: Only `aave_v3` currently supports multi-instance. Use `pendle` without instance suffix.

### Tokens not being tracked

**Cause**: Token addresses missing from `supply_tokens` or `borrow_tokens` in the instance config

**Fix**: Add the missing aToken or debt token addresses to the appropriate instance configuration

## Future Extensions

This multi-instance pattern can be extended to other adapters that benefit from multiple configurations:
- Multiple Uniswap V3 position managers
- Multiple Pendle oracle instances
- Regional or fork-specific deployments

---

**See also:**
- [ADAPTERS_GUIDE.md](ADAPTERS_GUIDE.md#1-aave-v3-adapter) - General Aave V3 adapter documentation
- [tq-oracle.toml.example](tq-oracle.toml.example) - Complete configuration examples
