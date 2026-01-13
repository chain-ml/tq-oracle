# TQ Oracle - Modular TVL Reporting System

A command-line application for collecting Total Value Locked (TVL) data from DeFi vault protocols using modular protocol adapters with support for complex position types including lending, PT tokens, LP positions, and RWA assets.

## Overview

TQ Oracle performs smart contract read calls through a registry of protocol adapters to aggregate TVL data for Mellow Finance flexible-vaults and compatible systems. The system supports:

- **Multi-protocol asset discovery** (Aave, Uniswap, Pendle, StakeWise, etc.)
- **Adapter chaining** for wrapped token conversion (PT tokens → underlying, ERC4626 → underlying)
- **Multi-source pricing** (CoW Swap, Chainlink, CoinGecko, Pyth validation)
- **Per-subvault breakdowns** with detailed asset reporting
- **Extra address tracking** (swap modules, treasury addresses)

For detailed system architecture, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Quick Start

### Running without installation

```sh
uvx --from git+https://github.com/chain-ml/tq-oracle.git tq-oracle --help
```

### Installation

This project uses `uv` for dependency management:

```bash
# Clone the repository
git clone <repo-url>
cd tq-oracle

# Install dependencies
uv sync
```

### Basic Usage

```bash
# Run with auto-detected config file (tq-oracle.toml or ~/.config/tq-oracle/config.toml)
uv run tq-oracle

# Run with explicit vault address
uv run tq-oracle 0xYourVaultAddress

# Run with custom config file
uv run tq-oracle --config path/to/custom-config.toml

# Preview configuration without running
uv run tq-oracle --show-config

# Increase verbosity for debugging
uv run tq-oracle --log-level DEBUG
```

## Configuration

TQ Oracle supports three configuration methods with the following precedence (highest to lowest):

1. **CLI Arguments** - Explicit command-line flags
2. **Environment Variables** - Set via shell or `.env` file
3. **TOML Configuration File** - Persistent configuration

See [tq-oracle.toml.example](tq-oracle.toml.example) for a comprehensive configuration template and [SETTINGS.md](SETTINGS.md) for complete configuration documentation.

## Asset Adapters

Asset adapters discover and value protocol positions. The system includes:

### Default Adapters (Global)

Run automatically across all subvaults unless explicitly skipped:

- **idle_balances** - Native ETH and ERC20 token balances
- **stakewise** - StakeWise vault shares and exit queue positions
- **streth** - strETH rebasing token positions

### Additional Adapters (Per-Subvault)

Configured per-subvault via `additional_adapters`:

#### Discovery Adapters

Discover new assets (non-conversion):

- **aave_v3** - Aave V3 aToken (supply) and debt token (borrow) positions
  - Supports **multi-pool/fork tracking** (e.g., `aave_v3.aave` + `aave_v3.spark`)
  - See [AAVE_MULTI_POOL_GUIDE.md](AAVE_MULTI_POOL_GUIDE.md) for Spark configuration
- **uniswap_v3** - Uniswap V3 LP positions (NFT-based)
- **uniswap_v4** - Uniswap V4 LP positions (NFT-based, requires Graph API key)

#### Conversion Adapters

Transform wrapped tokens to underlying assets:

- **pendle** - Converts PT and LP tokens to underlying via Pendle oracle
- **erc4626** - Converts ERC4626 vault tokens to underlying via `convertToAssets()`

### Configuration Examples

#### Simple Aave Position

```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["aave_v3"]
```

#### Adapter Chaining (Aave + PT Conversion)

```toml
# Flow: aave_v3 discovers aPT tokens → pendle converts PT to underlying
[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["aave_v3", "pendle", "erc4626"]
```

**Order matters!** Discovery adapters run first, then conversion adapters transform their outputs.

#### Uniswap Positions

```toml
[[subvault_adapters]]
subvault_address = "0xYourSubvault"
additional_adapters = ["uniswap_v3", "uniswap_v4"]
```

### Adapter Chaining

Adapters run **sequentially per subvault**, allowing transformation of wrapped tokens:

```
Input: Aave discovers [aPT-jrUSDe: 1000]
  ↓
Step 1: Pendle converts PT-jrUSDe 1000 → USDe 980
  ↓
Step 2: ERC4626 (no vault tokens, passes through)
  ↓
Output: [USDe: 980]
```

**Canonical ordering:**
- Discovery adapters first (any order): `aave_v3`, `uniswap_v3`, `uniswap_v4`
- Conversion adapters last (order matters): `pendle` → `erc4626`

See [ADAPTER_CHAINING_SUMMARY.md](ADAPTER_CHAINING_SUMMARY.md) for complete details.

## Price Adapters

Price adapters fetch asset prices in ETH. Priority order (highest to lowest):

1. **ETH** - Base asset (always 1:1)
2. **CoinGecko** - API-based pricing for RWA and less liquid assets (requires API key)
3. **Chainlink** - Stablecoin pricing via ETH/USD oracle
4. **CoW Swap** - Fallback DEX pricing for all other tokens
5. **Pyth** - Validation only (checks confidence ratios, not primary pricing)

### Configuration

```toml
# CoinGecko (optional, requires API key)
coingecko_enabled = true
[coingecko_token_ids]
"0xE556ABa6fe6036275Ec1f87eda296BE72C811BCE" = "nusd-2"  # nUSD

# Chainlink (optional, for stablecoins)
chainlink_enabled = true
chainlink_eth_usd_feed = "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"
chainlink_stablecoins = [
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",  # USDC
    "0xdAC17F958D2ee523a2206206994597C13D831ec7",  # USDT
]

# Pyth (validation)
pyth_enabled = true
pyth_max_confidence_ratio = 0.01
```

## Extra Addresses

Track balances for addresses outside the subvault system (e.g., swap modules, treasury):

```toml
[adapters.idle_balances]
extra_addresses = ["0x17aeAbfD3cB214A8757bF07D2E248d526c8C4809"]
```

Extra addresses appear in per-subvault breakdowns labeled as `Extra Address: 0x...`.

## Reporting

The system generates detailed reports including:

### Per-Subvault Breakdown

```
┌─ Subvault: 0xf37D9264099Fc67448e56e60BC33095F2b2a95d3
│  USDe                    1.109715 (0.000348 ETH) [0x4c9EDD...]
└─ Total Value: 0.000348 ETH

┌─ Extra Address: 0x17aeAbfD3cB214A8757bF07D2E248d526c8C4809
│  USDC                    100.000000 (0.032145 ETH) [0xA0b86...]
│  nUSD                     50.000000 (0.016073 ETH) [0xE556A...]
└─ Total Value: 0.048218 ETH
```

### Final Report

```
Final Report:
  Total TVL: 0.045485 ETH
  Assets: 12 unique tokens
  Subvaults: 6
  Block: 21234567
```

## Architecture

```
src/tq_oracle/
├── main.py              # CLI entry point (Typer)
├── settings.py          # Configuration management (pydantic-settings)
├── pipeline/            # Orchestration
│   ├── preflight.py     # Pre-checks (timeout, active proposals)
│   ├── assets.py        # Asset discovery & adapter chaining
│   ├── pricing.py       # Price fetching & validation
│   └── report.py        # Report generation & publishing
├── adapters/
│   ├── asset_adapters/  # Protocol adapters
│   │   ├── aave_v3.py
│   │   ├── pendle.py
│   │   ├── uniswap/
│   │   │   ├── uniswap_v3.py
│   │   │   └── uniswap_v4.py
│   │   └── rwa/
│   │       └── erc4626_vault.py
│   ├── price_adapters/  # Price sources
│   │   ├── cow_swap.py
│   │   ├── chainlink.py
│   │   └── coingecko.py
│   └── check_adapters/  # Pre-flight checks
├── processors/          # TVL computation
├── report/              # Report encoding & breakdown logging
└── tests/               # Test suite (mirrors src structure)
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for complete system design documentation.

## Environment Variables

Required for certain features:

```bash
# CoinGecko API (optional, for RWA pricing)
export TQ_ORACLE_COINGECKO_API_KEY="your-api-key"

# Uniswap V4 (required for V4 adapter)
export TQ_ORACLE_GRAPH_API_KEY="your-graph-api-key"

# Safe transaction signing (for production, not used since we don't have proposers with hot EOA)
export PRIVATE_KEY="your-private-key"
```

## Complete Configuration Example

See [tq-oracle.toml.example](tq-oracle.toml.example) for a production-ready configuration demonstrating:

- All available adapters
- Adapter chaining patterns
- Price adapter configuration
- RPC rate limiting
- Pre-check settings
- Per-subvault overrides

## Adapter Documentation

Detailed documentation for specific adapter types:

- **Aave V3** - [ADAPTERS_GUIDE.md](ADAPTERS_GUIDE.md#1-aave-v3-adapter)
  - **Multi-Pool/Fork Support** - [AAVE_MULTI_POOL_GUIDE.md](AAVE_MULTI_POOL_GUIDE.md) (Aave + Spark)
- **Pendle** - [ADAPTERS_GUIDE.md](ADAPTERS_GUIDE.md#2-pendle-adapter)
- **RWA/ERC4626** - [RWA_ADAPTERS_SUMMARY.md](RWA_ADAPTERS_SUMMARY.md), [COINGECKO_RWA_SETUP.md](COINGECKO_RWA_SETUP.md)
- **Uniswap V3/V4** - [UNISWAP_ADAPTERS_SUMMARY.md](UNISWAP_ADAPTERS_SUMMARY.md)
- **Adapter Chaining** - [ADAPTER_CHAINING_SUMMARY.md](ADAPTER_CHAINING_SUMMARY.md)

## Development

### Running Tests

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov=src/tq_oracle

# Run specific test file
uv run pytest tests/test_adapters.py
```

### Code Formatting

```bash
# Format code
uv run ruff format

# Lint code
uv run ruff check
```

## Troubleshooting

### Common Issues

**Adapter chain results lost:**
- Ensure non-conversion adapters pass through `previous_assets` in all return paths
- Check adapter ordering (discovery before conversion)

**Price validation failures:**
- Check Pyth confidence ratio settings (`pyth_max_confidence_ratio`)
- Verify CoinGecko token ID mappings
- Enable DEBUG logging to see price fetching details

**Uniswap V4 not working:**
- Verify `TQ_ORACLE_GRAPH_API_KEY` environment variable is set
- Check position_manager address in config matches deployed contract

**Extra addresses not showing:**
- Verify addresses are in `extra_addresses` array under `[adapters.idle_balances]`
- Check that `additional_asset_support = true` in config

### Debug Mode

```bash
# Enable debug logging
uv run tq-oracle --log-level DEBUG

# Show configuration without running
uv run tq-oracle --show-config
```

## External Links

- [flexible-vaults repo](https://github.com/mellow-finance/flexible-vaults)
- [Pendle Oracle Docs](https://docs.pendle.finance/)
- [Aave V3 Documentation](https://docs.aave.com/developers/)
- [Uniswap V4 Documentation](https://docs.uniswap.org/contracts/v4/overview)
