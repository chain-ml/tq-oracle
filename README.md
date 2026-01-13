# TQ Oracle - TVL Reporting CLI

A command-line application for collecting Total Value Locked (TVL) data from vault protocols using modular protocol adapters.

## Overview

TQ Oracle performs read smart contract READ calls through a registry of protocol adapters to aggregate TVL data for specified vaults. Each adapter is responsible for querying specific contracts and returning standardized asset price data.

For detailed system architecture and integration with Mellow Finance flexible-vaults, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Running without installing

You can run this CLI without any git cloning, directly with `uv`

```sh
uvx --from git+https://github.com/chain-ml/tq-oracle.git tq-oracle --help   
```

## Installation

This project uses `uv` for dependency management:

```bash
# Clone the repository
git clone <repo-url>
cd tq-oracle

# Install dependencies
uv sync

```

## Usage

### Configuration Methods

TQ Oracle supports three ways to configure the application, with the following precedence (highest to lowest):

1. **CLI Arguments** - Explicit command-line flags
2. **Environment Variables** - Set via shell or `.env` file
3. **TOML Configuration File** - Persistent configuration

### Basic Command

Run the CLI with a vault address and dry-run flag to preview reports without submission.

### Using a Configuration File (Recommended)

- Create `tq-oracle.toml` in project directory or `~/.config/tq-oracle/config.toml`
- Configure vault address, network, RPC endpoints, and operational settings
- Run with minimal CLI arguments - config file is auto-detected
- See `tq-oracle.toml.example` for complete configuration template


### Configuration Options

> [!IMPORTANT]  
> See [SETTINGS.md](./SETTINGS.md) for complete guide.

### Usage Examples

**Run with auto-detected config file:**

```bash
# Loads from tq-oracle.toml or ~/.config/tq-oracle/config.toml
uv run tq-oracle
```

**Run with explicit vault address:**

```bash
# Override vault address from config
uv run tq-oracle 0xYourVaultAddress
```

**Run with custom config file:**

```bash
uv run tq-oracle --config path/to/custom-config.toml
```

**Run with network override:**

```bash
uv run tq-oracle --network sepolia 0xYourVaultAddress
```

**Preview configuration without running:**

```bash
uv run tq-oracle --show-config
```

**Increase verbosity for debugging:**

```bash
uv run tq-oracle --log-level DEBUG
```

## Architecture

```sh
src/tq_oracle/
├── main.py              # CLI entry point (Typer)
├── settings.py          # Configuration management (pydantic-settings)
├── state.py             # Application state container
├── abi.py               # ABI loading utilities
├── constants.py         # Application constants
├── logger.py            # Logging configuration
├── pipeline/            # Orchestration (preflight → assets → pricing → report)
├── adapters/            # Protocol adapters
│   ├── asset_adapters/      # Fetch asset balances (e.g. idle, stakewise, streth)
│   ├── price_adapters/      # Fetch asset prices (cow_swap, eth, pyth)
│   ├── check_adapters/      # Pre-flight checks (active_submit, timeout)
│   └── price_validators/    # Price validation
├── processors/          # Data aggregation and TVL computation
├── checks/              # Pre-flight validation orchestration
├── report/              # Report encoding, generation, and publishing
├── abis/                # Contract ABIs (JSON)
└── tests/               # Mirrors src structure
```

> [!IMPORTANT]  
> See [ARCHITECTURE.md](./ARCHITECTURE.md) for complete rundown.

## External Links

- `flexible-vaults` [repo](https://github.com/mellow-finance/flexible-vaults)
