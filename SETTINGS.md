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

| Adapter defaults | Env var | TOML path | Default | Effect |
| --- | --- | --- | --- | --- |
| idle_balances.extra_tokens | TQ_ORACLE_ADAPTERS__IDLE_BALANCES__EXTRA_TOKENS | adapters.idle_balances.extra_tokens | {} | Map symbol→address added (tvl-only) when `additional_asset_support` is true. |
| idle_balances.extra_addresses | TQ_ORACLE_ADAPTERS__IDLE_BALANCES__EXTRA_ADDRESSES | adapters.idle_balances.extra_addresses | [] | Extra vault-like addresses to scan for idle balances. |
| stakewise.stakewise_vault_addresses | TQ_ORACLE_ADAPTERS__STAKEWISE__STAKEWISE_VAULT_ADDRESSES | adapters.stakewise.stakewise_vault_addresses | [] | Vault list for StakeWise adapter; if empty, uses network default. |
| stakewise.stakewise_exit_queue_start_block | TQ_ORACLE_ADAPTERS__STAKEWISE__STAKEWISE_EXIT_QUEUE_START_BLOCK | adapters.stakewise.stakewise_exit_queue_start_block | 0 | From-block for exit queue scan. |
| stakewise.stakewise_exit_max_lookback_blocks | TQ_ORACLE_ADAPTERS__STAKEWISE__STAKEWISE_EXIT_MAX_LOOKBACK_BLOCKS | adapters.stakewise.stakewise_exit_max_lookback_blocks | 28800 (~4 days) | Lookback cap when scanning exit logs. |
| stakewise.extra_addresses | TQ_ORACLE_ADAPTERS__STAKEWISE__EXTRA_ADDRESSES | adapters.stakewise.extra_addresses | [] | Extra addresses (vault-like) to scan for StakeWise positions. |
| stakewise.skip_exit_queue_scan | TQ_ORACLE_ADAPTERS__STAKEWISE__SKIP_EXIT_QUEUE_SCAN | adapters.stakewise.skip_exit_queue_scan | false | If true, skips exit queue tickets and only reads shares. |

## Custom Adapter Settings

| `subvault_adapters` entry (TOML list) | Key | Default | Purpose |
| --- | --- | --- | --- |
| subvault_address | required | n/a | Target subvault (or arbitrary address if `skip_subvault_existence_check=true`). |
| additional_adapters | [] | [] | Names from registry: `idle_balances`, `stakewise`, `streth`; run against this subvault. |
| skip_idle_balances | false | false | Skip default idle_balances for this subvault. |
| skip_streth | false | false | Skip strETH adapter for this subvault. |
| adapter_overrides | {} | {} | Per-adapter kwargs merged over defaults (e.g., custom `stakewise_vault_addresses`). |
| skip_subvault_existence_check | false | false | Allows non-vault addresses; requires `allow_dangerous=true`. |

## Derived Settings

| Runtime derived | Source | Notes |
| --- | --- | --- |
| using_default_rpc | set in CLI callback | True when `vault_rpc` came from network default. |
| chain_id | computed lazily | Derived from `vault_rpc` when first accessed. |
| oracle_helper defaults | computed in CLI callback | Set per network if `oracle_helper_address` not provided. |
| block_number fallback | computed in CLI callback | Latest block pulled from `vault_rpc` if not supplied. |
