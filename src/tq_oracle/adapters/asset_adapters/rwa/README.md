# RWA (Real World Asset) Adapters

This directory contains adapters for handling RWA vault tokens and their redemption queues.

## Overview

RWA adapters handle two main scenarios:

1. **Active Balance Tracking** - Converting vault token balances to underlying assets
2. **Redemption Queue Tracking** - Tracking assets in various redemption states (pending, claimable)

## Available Adapters

### ERC4626VaultAdapter

Generic adapter for any ERC4626-compliant vault token.

**What it does:**
- Checks vault token balance
- Calls `convertToAssets()` to get underlying asset amount
- Returns underlying asset for pricing pipeline

**Configuration:**
```toml
[adapters.erc4626]
# Configure multiple vaults
[adapters.erc4626.vaults.vault_name]
vault_token = "0x..."  # ERC4626 vault token address
underlying_asset = "0x..."  # Optional - will auto-fetch from vault.asset() if not provided

[adapters.erc4626.vaults.another_vault]
vault_token = "0x..."
underlying_asset = "0x..."
```

**Per-Subvault Usage:**
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["erc4626"]
```

**Flow:**
```
Vault Token Balance → convertToAssets() → Underlying Token → Price Adapters → ETH
```

---

### SNUSDAdapter

Custom adapter for sNUSD with 10-day redemption cooldown tracking.

**What it does:**
- Tracks active sNUSD balance (converted to nUSD)
- Checks `cooldowns()` for pending redemptions
- Separates pending (in cooldown) vs claimable (cooldown ended)
- Returns total nUSD exposure across all states

**Configuration:**
```toml
[adapters.snusd]
snusd_token = "0x..."  # sNUSD token address
nusd_token = "0x..."   # nUSD underlying token address
cooldown_period = 864000  # Optional - 10 days in seconds (for logging/validation)
```

**Per-Subvault Usage:**
```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["snusd"]
```

**Flow:**
```
Active sNUSD → convertToAssets() → nUSD
                                      ↓
                          cooldowns(address) → {cooldownEnd, underlyingAmount}
                                      ↓
                          Check block timestamp vs cooldownEnd
                                      ↓
                          Classify as pending or claimable
                                      ↓
                          Total nUSD = active + pending + claimable → Price Adapters → ETH
```

**Logs Output:**
```
sNUSD adapter collecting balances — user=0x... block=12345
sNUSD cooldown state — cooldownEnd=1234567890, underlyingAmount=1000000000000000000, remainingSeconds=86400, isClaimable=false
sNUSD summary for 0x... — total_nusd=2000000000000000000 (active=500000000000000000, pending=1000000000000000000, claimable=500000000000000000)
```

---

## Creating Custom RWA Adapters

Most RWAs will require custom redemption queue logic. Here's the pattern:

### 1. Simple Case: Just Active Balance

If your RWA is a standard ERC4626 vault with no redemption queue, use `ERC4626VaultAdapter` - no custom code needed!

### 2. Complex Case: Custom Redemption Queue

For RWAs with bespoke redemption mechanisms (like sNUSD), create a custom adapter:

**Template Structure:**
```python
class CustomRWAAdapter(BaseAssetAdapter):
    def __init__(self, config: OracleSettings, **overrides):
        # Initialize Web3, RPC throttling, load config
        # Define custom ABI for your RWA contract

    async def fetch_assets(self, subvault_address: str) -> list[AssetData]:
        # 1. Get active token balance
        active_balance = await self._balance_of(token, subvault)
        active_underlying = await self._convert_to_underlying(active_balance)

        # 2. Query redemption queue state (custom per RWA)
        redemption_state = await self._get_redemption_state(subvault)

        # 3. Classify redemptions: pending vs claimable
        pending, claimable = self._classify_redemptions(redemption_state)

        # 4. Return total underlying exposure
        total = active_underlying + pending + claimable
        return [AssetData(asset_address=underlying_token, amount=total)]
```

**Key Considerations:**
- **On-chain vs Off-chain**: Some redemption queues may require event scanning or off-chain indexer queries
- **Block Timestamp**: Use `_get_block_timestamp()` for time-based checks (like cooldown expiry)
- **Error Handling**: Handle cases where redemption data might not be available
- **Logging**: Log breakdowns (active, pending, claimable) for transparency

### 3. Event-Based Redemption Tracking

For RWAs where redemptions aren't stored in state variables, you'll need event scanning (like StakeWise exit queue):

```python
async def _scan_redemption_events(self, user: str) -> list[RedemptionTicket]:
    # Scan historical RedemptionRequested events
    # Filter by user address
    # Return list of tickets with amounts and timestamps
```

---

## Configuration Examples

### Example 1: Generic ERC4626 Vault
```toml
[adapters.erc4626.vaults.usdy_vault]
vault_token = "0xYourVaultToken"
underlying_asset = "0xUSDY"

[[subvault_adapters]]
subvault_address = "0xSubvault1"
additional_adapters = ["erc4626"]
```

### Example 2: sNUSD with Cooldown
```toml
[adapters.snusd]
snusd_token = "0xsNUSD"
nusd_token = "0xnUSD"

[[subvault_adapters]]
subvault_address = "0xSubvault2"
additional_adapters = ["snusd"]
```

### Example 3: Multiple RWAs on Same Subvault
```toml
[[subvault_adapters]]
subvault_address = "0xMultiAssetSubvault"
additional_adapters = ["erc4626", "snusd"]
```

Each adapter runs independently and returns its positions, which get aggregated by the asset pipeline.

---

## Testing Adapters

When testing a new RWA adapter:

1. **Check Active Balance**: Verify convertToAssets() returns correct underlying amounts
2. **Test Redemption States**: Create test scenarios for pending/claimable redemptions
3. **Validate Aggregation**: Ensure active + pending + claimable = total exposure
4. **Check Edge Cases**: Zero balances, expired cooldowns, no redemptions, etc.
5. **Review Logs**: Confirm logging shows breakdown of positions clearly

---

## Adding New RWA Adapters

1. Create adapter in this directory: `your_rwa.py`
2. Add to `__init__.py` exports
3. Register in `ADAPTER_REGISTRY` in parent `__init__.py`
4. Add settings class in `src/tq_oracle/settings.py`
5. Document configuration in this README
6. Add TOML example to `tq-oracle.toml.example`
