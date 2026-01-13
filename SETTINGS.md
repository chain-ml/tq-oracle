# TQ Oracle Settings & CLI

## Overview

The oracle must be configured to correctly determine the TVL, and therefore share prices, for a flexible-vault.

Where possible, values are gathered from blockchain calls but discovery is not always possible due to the lack of a registry which determines what is connected to each subvault. As such we require a fully operational vault to have a configuration which fully maps to the assets and integrations it is expected to support ahead of time.

## Types & Precedence

| Order | Source | Notes |
| --- | --- | --- |
| 1 | CLI args (`tq-oracle` entrypoint) | Highest precedence |
| 2 | Env vars (`TQ_ORACLE_*`, `.env` loaded) | Secrets must be env-only |
| 3 | TOML config | `--config/-c` or `TQ_ORACLE_CONFIG`; else `./tq-oracle.toml` then `~/.config/tq-oracle/config.toml`; accepts top-level or `[tq_oracle]` table. |

> [!IMPORTANT]  
> Settings `private_key` and `safe_txn_srvc_api_key` are rejected when added to the config file. They should instead be provided by environment variable.

## Global Settings

| Setting / Arg | CLI flag | Env var | TOML key | Default | Effect in run |
| --- | --- | --- | --- | --- | --- |
| vault_address | positional | TQ_ORACLE_VAULT_ADDRESS | vault_address | required | Target vault; required before pipeline runs. |
| config path | `--config,-c` | TQ_ORACLE_CONFIG | n/a | auto-discovery | Selects TOML file loaded at lowest precedence. |
| show_config | `--show-config` | n/a | n/a | false | Print effective settings (secrets redacted) then exit. |
| network | `--network,-n` | TQ_ORACLE_NETWORK | network | mainnet | Picks asset list, RPC default, oracle_helper default (mainnet/sepolia/base). |
| vault_rpc | `--vault-rpc` | TQ_ORACLE_VAULT_RPC | vault_rpc | per-network HTTP RPC | RPC used everywhere; auto-set from `network` if missing; tracked via `using_default_rpc`. |
| oracle_helper_address | none | TQ_ORACLE_ORACLE_HELPER_ADDRESS | oracle_helper_address | per-network constant | Address used in final price derivation; set from network if absent. |
| block_number | `--block-number` | TQ_ORACLE_BLOCK_NUMBER | block_number | latest at runtime | Snapshot height for all calls; fetched if not provided. |
| eth_mainnet_rpc | none | TQ_ORACLE_ETH_MAINNET_RPC | eth_mainnet_rpc | null | Reserved for cross-chain lookups when vault not on mainnet. |
| dry_run | `--dry-run/--no-dry-run` | TQ_ORACLE_DRY_RUN | dry_run | true | When false, Safe broadcast mode; requires `safe_address` + `private_key`. |
| dry_run_report_indent | none | none | dry_run_report_indent | true | Whether or not to indent report output |
| safe_address | none | TQ_ORACLE_SAFE_ADDRESS | safe_address | null | Gnosis Safe used for submission; mandatory when `dry_run` is false. |
| private_key | none | TQ_ORACLE_PRIVATE_KEY | private_key | null | Signer for Safe tx; env/CLI only; config file rejected. |
| safe_txn_srvc_api_key | none | TQ_ORACLE_SAFE_TXN_SRVC_API_KEY | safe_txn_srvc_api_key | null | Optional Safe Transaction Service API key; env-only; config file rejected. |
| allow_dangerous | `--allow-dangerous/--disallow-dangerous` | TQ_ORACLE_ALLOW_DANGEROUS | allow_dangerous | false | Must be true to permit `skip_subvault_existence_check` in `subvault_adapters`. |
| ignore_empty_vault | `--ignore-empty-vault/--require-nonempty-vault` | TQ_ORACLE_IGNORE_EMPTY_VAULT | ignore_empty_vault | false | If true, zero-asset OracleHelper errors return zeros instead of failing. |
| ignore_timeout_check | `--ignore-timeout-check/--enforce-timeout-check` | TQ_ORACLE_IGNORE_TIMEOUT_CHECK | ignore_timeout_check | false | Pre-check allows submission even if oracle timeout has not elapsed. |
| ignore_active_proposal_check | `--ignore-active-proposal-check/--enforce-active-proposal-check` | TQ_ORACLE_IGNORE_ACTIVE_PROPOSAL_CHECK | ignore_active_proposal_check | false | Pre-check allows submission even if Safe has active submitReport proposals. |
| pre_check_retries | none | TQ_ORACLE_PRE_CHECK_RETRIES | pre_check_retries | 3 | Retry count for preflight checks. |
| pre_check_timeout | none | TQ_ORACLE_PRE_CHECK_TIMEOUT | pre_check_timeout | 12.0s | Backoff interval between preflight retries. |
| log_level | `--log-level` | TQ_ORACLE_LOG_LEVEL | log_level | INFO | Logger level; accepts TRACE/DEBUG/INFO/WARNING/ERROR/CRITICAL. |
| additional_asset_support | none | TQ_ORACLE_ADDITIONAL_ASSET_SUPPORT | additional_asset_support | true | Enables default+extra idle balance tokens and addresses. |
| max_calls | none | TQ_ORACLE_MAX_CALLS | max_calls | 3 | Semaphore size for adapter RPC throttling. |
| rpc_max_concurrent_calls | none | TQ_ORACLE_RPC_MAX_CONCURRENT_CALLS | rpc_max_concurrent_calls | 5 | Declared concurrency cap (currently unused by adapters). |
| rpc_delay | none | TQ_ORACLE_RPC_DELAY | rpc_delay | 0.15s | Base sleep after adapter RPC calls. |
| rpc_jitter | none | TQ_ORACLE_RPC_JITTER | rpc_jitter | 0.10s | Randomized extra sleep after adapter RPC calls. |
| price_warning_tolerance_percentage | none | TQ_ORACLE_PRICE_WARNING_TOLERANCE_PERCENTAGE | price_warning_tolerance_percentage | 0.5 | Pyth validator: warn above this % deviation. |
| price_failure_tolerance_percentage | none | TQ_ORACLE_PRICE_FAILURE_TOLERANCE_PERCENTAGE | price_failure_tolerance_percentage | 1.0 | Pyth validator: fail above this % deviation; must exceed warning threshold. |
| pyth_enabled | none | TQ_ORACLE_PYTH_ENABLED | pyth_enabled | true | Toggles Pyth-based validation. |
| pyth_hermes_endpoint | none | TQ_ORACLE_PYTH_HERMES_ENDPOINT | pyth_hermes_endpoint | https://hermes.pyth.network | Pyth price/metadata source. |
| pyth_staleness_threshold | none | TQ_ORACLE_PYTH_STALENESS_THRESHOLD | pyth_staleness_threshold | 60s | Reject Pyth prices older than this window. |
| pyth_max_confidence_ratio | none | TQ_ORACLE_PYTH_MAX_CONFIDENCE_RATIO | pyth_max_confidence_ratio | 0.03 | Max allowed `conf/price` ratio from Pyth. |
| pyth_dynamic_discovery_enabled | none | TQ_ORACLE_PYTH_DYNAMIC_DISCOVERY_ENABLED | pyth_dynamic_discovery_enabled | true | Reserved; not yet wired. |

## Adapter Specific Settings

### Default Adapters

| Adapter defaults | Env var | TOML path | Default | Effect |
| --- | --- | --- | --- | --- |
| idle_balances.extra_tokens | TQ_ORACLE_ADAPTERS__IDLE_BALANCES__EXTRA_TOKENS | adapters.idle_balances.extra_tokens | {} | Map symbol→address added (tvl-only) when `additional_asset_support` is true. |
| idle_balances.extra_addresses | TQ_ORACLE_ADAPTERS__IDLE_BALANCES__EXTRA_ADDRESSES | adapters.idle_balances.extra_addresses | [] | Extra vault-like addresses to scan for idle balances. |
| idle_balances.non_tvl_tokens | TQ_ORACLE_ADAPTERS__IDLE_BALANCES__NON_TVL_TOKENS | adapters.idle_balances.non_tvl_tokens | {} | Map symbol→address for tokens tracked for idle balances but NOT marked as tvl_only. |
| stakewise.stakewise_vault_addresses | TQ_ORACLE_ADAPTERS__STAKEWISE__STAKEWISE_VAULT_ADDRESSES | adapters.stakewise.stakewise_vault_addresses | [] | Vault list for StakeWise adapter; if empty, uses network default. |
| stakewise.stakewise_exit_queue_start_block | TQ_ORACLE_ADAPTERS__STAKEWISE__STAKEWISE_EXIT_QUEUE_START_BLOCK | adapters.stakewise.stakewise_exit_queue_start_block | 0 | From-block for exit queue scan. |
| stakewise.stakewise_exit_max_lookback_blocks | TQ_ORACLE_ADAPTERS__STAKEWISE__STAKEWISE_EXIT_MAX_LOOKBACK_BLOCKS | adapters.stakewise.stakewise_exit_max_lookback_blocks | 28800 (~4 days) | Lookback cap when scanning exit logs. |
| stakewise.extra_addresses | TQ_ORACLE_ADAPTERS__STAKEWISE__EXTRA_ADDRESSES | adapters.stakewise.extra_addresses | [] | Extra addresses (vault-like) to scan for StakeWise positions. |
| stakewise.skip_exit_queue_scan | TQ_ORACLE_ADAPTERS__STAKEWISE__SKIP_EXIT_QUEUE_SCAN | adapters.stakewise.skip_exit_queue_scan | false | If true, skips exit queue tickets and only reads shares. |

### Additional Adapters

#### Aave V3 Adapter

Supports single-pool configuration (backwards compatible) or multi-pool/fork configuration via array syntax.

**Single Pool (backwards compatible):**
```toml
[adapters.aave_v3]
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
base_asset_type = "eth"
supply_tokens = { WETH = "0x..." }
borrow_tokens = { WETH = "0x..." }
```

**Multi-Pool (e.g., Aave + Spark):**
```toml
[[adapters.aave_v3]]
name = "aave"
pool_address = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
supply_tokens = { WETH = "0x..." }
borrow_tokens = { WETH = "0x..." }

[[adapters.aave_v3]]
name = "spark"
pool_address = "0xC13e21B648A5Ee794902342038FF3aDAB66BE987"
supply_tokens = { WETH = "0x..." }
borrow_tokens = { WETH = "0x..." }
```

| Setting | TOML path | Default | Effect |
| --- | --- | --- | --- |
| aave_v3.name | adapters.aave_v3.name | null | Instance name for multi-pool (e.g., "aave", "spark"). Reference as `aave_v3.{name}` in `additional_adapters`. |
| aave_v3.pool_address | adapters.aave_v3.pool_address | mainnet pool | Aave V3 pool contract address. |
| aave_v3.supply_tokens | adapters.aave_v3.supply_tokens | mainnet defaults | Map symbol→aToken address for supply positions. |
| aave_v3.borrow_tokens | adapters.aave_v3.borrow_tokens | mainnet defaults | Map symbol→debt token address for borrow positions. |
| aave_v3.base_asset_type | adapters.aave_v3.base_asset_type | "eth" | Asset denomination: "eth" or "usd". |

#### Pendle Adapter

```toml
[adapters.pendle]
oracle_address = "0x9a9Fa8338dd5E5B2188006f1Cd2Ef26d921650C2"

[adapters.pendle.markets.pt_usde_mar2026]
market = "0x..."
accounting_asset = "0x..."  # Underlying asset address
```

| Setting | TOML path | Default | Effect |
| --- | --- | --- | --- |
| pendle.oracle_address | adapters.pendle.oracle_address | null | Pendle oracle contract address for PT/LP price queries. |
| pendle.markets | adapters.pendle.markets.{name} | {} | Map of market configurations. Each market needs `market` (address) and `accounting_asset` (underlying token). |

#### ERC4626 Adapter

```toml
[adapters.erc4626.vaults.snusd]
vault_token = "0x..."
underlying_asset = "0x..."
```

| Setting | TOML path | Default | Effect |
| --- | --- | --- | --- |
| erc4626.vaults | adapters.erc4626.vaults.{name} | {} | Map of vault configurations. Each vault needs `vault_token` and `underlying_asset`. |

#### Uniswap V3 Adapter

| Setting | TOML path | Default | Effect |
| --- | --- | --- | --- |
| uniswap_v3.position_manager | adapters.uniswap_v3.position_manager | mainnet address | Uniswap V3 NonfungiblePositionManager contract address. |

#### Uniswap V4 Adapter

| Setting | Env var | TOML path | Default | Effect |
| --- | --- | --- | --- | --- |
| uniswap_v4.position_manager | n/a | adapters.uniswap_v4.position_manager | null | Uniswap V4 PositionManager contract address (required). |
| uniswap_v4.pool_manager | n/a | adapters.uniswap_v4.pool_manager | mainnet address | Uniswap V4 PoolManager contract address. |
| graph_api_key | TQ_ORACLE_GRAPH_API_KEY | n/a | null | The Graph API key for Uniswap V4 subgraph queries (required for V4 adapter). |

### Price Adapter Settings

| Setting | Env var | TOML path | Default | Effect |
| --- | --- | --- | --- | --- |
| coingecko_enabled | TQ_ORACLE_COINGECKO_ENABLED | coingecko_enabled | false | Enable CoinGecko API-based pricing. |
| coingecko_api_key | TQ_ORACLE_COINGECKO_API_KEY | n/a | null | CoinGecko API key (optional, uses free tier if not provided). |
| coingecko_token_ids | n/a | coingecko_token_ids | {} | Map token addresses to CoinGecko token IDs (e.g., `"0x..." = "usd-coin"`). |
| chainlink_enabled | TQ_ORACLE_CHAINLINK_ENABLED | chainlink_enabled | false | Enable Chainlink ETH/USD oracle for stablecoin pricing. |
| chainlink_eth_usd_feed | TQ_ORACLE_CHAINLINK_ETH_USD_FEED | chainlink_eth_usd_feed | mainnet feed | Chainlink ETH/USD price feed address. |
| chainlink_stablecoins | TQ_ORACLE_CHAINLINK_STABLECOINS | chainlink_stablecoins | [] | List of stablecoin addresses to price via Chainlink. |

## Custom Adapter Settings

| `subvault_adapters` entry (TOML list) | Key | Default | Purpose |
| --- | --- | --- | --- |
| subvault_address | required | n/a | Target subvault (or arbitrary address if `skip_subvault_existence_check=true`). |
| additional_adapters | [] | [] | List of adapter names to run against this subvault. Supports: `idle_balances`, `stakewise`, `streth`, `aave_v3`, `aave_v3.{name}`, `pendle`, `erc4626`, `uniswap_v3`, `uniswap_v4`. **Order matters** for adapter chaining (discovery before conversion). |
| skip_idle_balances | false | false | Skip default idle_balances for this subvault. |
| skip_streth | false | false | Skip strETH adapter for this subvault. |
| adapter_overrides | {} | {} | Per-adapter kwargs merged over defaults (e.g., custom `stakewise_vault_addresses`). |
| skip_subvault_existence_check | false | false | Allows non-vault addresses; requires `allow_dangerous=true`. |

### Adapter Chaining Examples

Adapters run **sequentially** per subvault in the order specified:

```toml
# Example 1: Single Aave pool
[[subvault_adapters]]
subvault_address = "0x..."
additional_adapters = ["aave_v3"]

# Example 2: Multiple pools (Aave + Spark)
[[subvault_adapters]]
subvault_address = "0x..."
additional_adapters = ["aave_v3.aave", "aave_v3.spark"]

# Example 3: Adapter chain for PT token conversion
# Flow: Aave discovers aPT tokens → Pendle converts PT → ERC4626 converts vault tokens
[[subvault_adapters]]
subvault_address = "0x..."
additional_adapters = ["aave_v3", "pendle", "erc4626"]

# Example 4: Uniswap LP positions
[[subvault_adapters]]
subvault_address = "0x..."
additional_adapters = ["uniswap_v3", "uniswap_v4"]
```

## Derived Settings

| Runtime derived | Source | Notes |
| --- | --- | --- |
| using_default_rpc | set in CLI callback | True when `vault_rpc` came from network default. |
| chain_id | computed lazily | Derived from `vault_rpc` when first accessed. |
| oracle_helper defaults | computed in CLI callback | Set per network if `oracle_helper_address` not provided. |
| block_number fallback | computed in CLI callback | Latest block pulled from `vault_rpc` if not supplied. |
