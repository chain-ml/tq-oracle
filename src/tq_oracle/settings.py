"""Settings module with unified configuration precedence: CLI > ENV > CONFIG FILE."""

from __future__ import annotations

from tq_oracle.constants import STAKEWISE_EXIT_MAX_LOOKBACK_BLOCKS

import os
from enum import Enum
from pathlib import Path
from typing import Any, Literal

try:
    import tomllib  # py311+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

from dotenv import load_dotenv
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from .constants import NetworkAssets, StrEthAddresses

load_dotenv()


class Network(str, Enum):
    MAINNET = "mainnet"
    SEPOLIA = "sepolia"
    BASE = "base"
    HYPEREVM = "hyperevm"
    HYPERCORE = "hypercore"


class IdleBalancesAdapterSettings(BaseModel):
    """Configuration options for idle balance collection."""

    extra_tokens: dict[str, str] = Field(default_factory=dict)
    extra_addresses: list[str] = Field(default_factory=list)
    non_tvl_tokens: dict[str, str] = Field(default_factory=dict)
    # Map extra_address -> list of adapter names to run after idle_balances
    # e.g., { "0x17aeAbfD3cB214A8757bF07D2E248d526c8C4809": ["erc4626", "snusd"] }
    extra_address_adapters: dict[str, list[str]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class StakewiseAdapterSettings(BaseModel):
    """Configuration options for StakeWise adapter defaults."""

    stakewise_vault_addresses: list[str] = Field(default_factory=list)
    stakewise_exit_queue_start_block: int = 0
    stakewise_exit_max_lookback_blocks: int = STAKEWISE_EXIT_MAX_LOOKBACK_BLOCKS
    extra_addresses: list[str] = Field(default_factory=list)
    skip_exit_queue_scan: bool = False

    model_config = ConfigDict(extra="ignore")


class AaveV3AdapterSettings(BaseModel):
    """Configuration options for Aave V3 adapter defaults.

    Can be used as a single config or as part of a list for multiple instances
    (e.g., Aave + Spark). When using multiple instances, each must have a unique
    'name' field to reference in additional_adapters as 'aave_v3.{name}'.
    """

    name: str | None = None  # Instance name for multi-config (e.g., "spark", "aave")
    pool_address: str | None = None
    supply_tokens: dict[str, str] = Field(default_factory=dict)
    borrow_tokens: dict[str, str] = Field(default_factory=dict)
    base_asset_type: str = "eth"  # 'eth' or 'usd'

    model_config = ConfigDict(extra="ignore")


class PendleAdapterSettings(BaseModel):
    """Configuration options for Pendle PT/LP adapter defaults."""

    oracle_address: str | None = None
    markets: dict[str, dict[str, str]] = Field(default_factory=dict)
    # markets structure: { "market_name": { "market": "0x...", "accounting_asset": "0x..." } }

    model_config = ConfigDict(extra="ignore")


class ERC4626AdapterSettings(BaseModel):
    """Configuration options for ERC4626 vault adapter defaults."""

    vaults: dict[str, dict[str, str]] = Field(default_factory=dict)
    # vaults structure: { "vault_name": {
    #   "vault_token": "0x...",
    #   "underlying_asset": "0x...",
    #   "market_discount": 100  # optional: discount in tenths of bps (default: 0)
    #                           # e.g., 100 = 10 bps, 5 = 0.5 bps
    # } }

    model_config = ConfigDict(extra="ignore")


class SNUSDAdapterSettings(BaseModel):
    """Configuration options for sNUSD adapter defaults."""

    snusd_token: str | None = None
    nusd_token: str | None = None
    cooldown_period: int | None = None  # Optional, defaults to 10 days (864000s)

    model_config = ConfigDict(extra="ignore")


class WstETHWithdrawalSettings(BaseModel):
    """Configuration options for wstETH withdrawal queue adapter."""

    withdrawal_queue: str | None = None  # Lido withdrawal queue address
    # Defaults to mainnet: 0x889edC2eDab5f40e902b864aD4d7AdE8E412F9B1

    model_config = ConfigDict(extra="ignore")


class SUSDeCooldownSettings(BaseModel):
    """Configuration options for sUSDe cooldown adapter."""

    susde_token: str | None = None  # sUSDe token address
    # Defaults to mainnet: 0x9D39A5DE30e57443BfF2A8307A4256c8797A3497
    usde_token: str | None = None  # USDe underlying token address
    # Defaults to mainnet: 0x4c9EDD5852cd905f086C759E8383e09bff1E68B3

    model_config = ConfigDict(extra="ignore")


class UniswapV3AdapterSettings(BaseModel):
    """Configuration options for Uniswap V3 adapter defaults."""

    position_manager: str | None = None  # Defaults to mainnet if not provided

    model_config = ConfigDict(extra="ignore")


class UniswapV4AdapterSettings(BaseModel):
    """Configuration options for Uniswap V4 adapter defaults."""

    position_manager: str | None = None  # Required for V4
    pool_manager: str | None = None  # Defaults to mainnet if not provided
    pools: list[dict[str, Any]] = Field(default_factory=list)
    # pools structure: [{ "token0": "0x...", "token1": "0x...", "fee": 3000, "tick_spacing": 60, "hook": "0x..." }]

    model_config = ConfigDict(extra="ignore")


class MorphoBlueAdapterSettings(BaseModel):
    """Configuration options for Morpho Blue adapter defaults."""

    morpho_address: str | None = None  # Morpho Blue contract address
    # Defaults to mainnet: 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
    markets: dict[str, dict[str, str]] = Field(default_factory=dict)
    # markets structure: { "market_name": { "market_id": "0x..." } }

    model_config = ConfigDict(extra="ignore")


class HyperCoreVaultConfig(BaseModel):
    """Configuration for a native Hyperliquid vault."""

    vault_address: str
    name: str | None = None  # Optional friendly name

    model_config = ConfigDict(extra="ignore")


class HyperCoreSubAccountConfig(BaseModel):
    """Configuration for a HyperCore sub-account."""

    master_address: str
    agent_wallet: str | None = None  # Optional - for oracle read-only

    model_config = ConfigDict(extra="ignore")


class ChainConfig(BaseModel):
    """Configuration for a chain in multi-chain setup.

    Chains can be:
    - primary: The main chain with redemption/deposit capability
    - satellite: Contributes to TVL only

    Example TOML:
        [[chains]]
        name = "mainnet"
        network = "mainnet"
        vault_address = "0x..."
        rpc = "https://..."
        role = "primary"
        required = true
    """

    name: str  # Friendly name for logging
    network: str  # Network identifier (mainnet, hyperevm, hypercore)
    vault_address: str | None = None
    rpc: str | None = None  # RPC endpoint for EVM chains
    api_url: str | None = None  # API endpoint for non-EVM chains (HyperCore)
    block_number: int | None = None  # Optional block number for state snapshot
    role: Literal["primary", "satellite"] = "satellite"
    required: bool = True  # If false, chain failure doesn't block report
    subvault_addresses: list[str] = Field(default_factory=list)
    adapters: dict[str, Any] = Field(default_factory=dict)  # Per-chain adapter config
    # HyperCore-specific
    vaults: list[HyperCoreVaultConfig] = Field(default_factory=list)
    subaccounts: list[HyperCoreSubAccountConfig] = Field(default_factory=list)

    model_config = ConfigDict(extra="ignore")


class BridgeConfig(BaseModel):
    """Configuration for a cross-chain bridge to track in-flight transfers.

    Supported bridge types:
    - cctp: Circle CCTP (burns on source, mints on dest)
    - native: Native HyperEVM ↔ HyperCore transfers
    - evm_core: HyperEVM → HyperCore via CoreDepositWallet

    Example TOML:
        [[bridges]]
        type = "cctp"
        source_chain = "mainnet"
        dest_chain = "hyperevm"
        lookback_blocks = 80
    """

    type: Literal["cctp", "native", "evm_core"]
    source_chain: str  # Chain name (must match ChainConfig.name)
    dest_chain: str  # Chain name (must match ChainConfig.name)
    lookback_blocks: int = 80  # Blocks to scan for in-flight detection
    token_messenger: str | None = None  # CCTP TokenMessenger address override
    source_subvault: str | None = None  # Subvault address on source chain
    dest_subvault: str | None = None  # Subvault address on dest chain

    model_config = ConfigDict(extra="ignore")


class AdapterSettings(BaseModel):
    stakewise: StakewiseAdapterSettings = Field(
        default_factory=StakewiseAdapterSettings
    )
    idle_balances: IdleBalancesAdapterSettings = Field(
        default_factory=IdleBalancesAdapterSettings
    )
    # aave_v3 supports both single config (backwards compat) and list of named configs
    aave_v3: AaveV3AdapterSettings | list[AaveV3AdapterSettings] = Field(
        default_factory=AaveV3AdapterSettings
    )
    pendle: PendleAdapterSettings = Field(default_factory=PendleAdapterSettings)
    erc4626: ERC4626AdapterSettings = Field(default_factory=ERC4626AdapterSettings)
    snusd: SNUSDAdapterSettings = Field(default_factory=SNUSDAdapterSettings)
    wsteth_withdrawal: WstETHWithdrawalSettings = Field(
        default_factory=WstETHWithdrawalSettings
    )
    susde_cooldown: SUSDeCooldownSettings = Field(
        default_factory=SUSDeCooldownSettings
    )
    uniswap_v3: UniswapV3AdapterSettings = Field(
        default_factory=UniswapV3AdapterSettings
    )
    uniswap_v4: UniswapV4AdapterSettings = Field(
        default_factory=UniswapV4AdapterSettings
    )
    morpho_blue: MorphoBlueAdapterSettings = Field(
        default_factory=MorphoBlueAdapterSettings
    )

    model_config = ConfigDict(extra="ignore")

    def get_aave_v3_config(
        self, instance_name: str | None = None
    ) -> AaveV3AdapterSettings | None:
        """Get Aave V3 config by instance name.

        Args:
            instance_name: The instance name (e.g., 'spark', 'aave').
                          If None, returns the single/default config.

        Returns:
            The matching AaveV3AdapterSettings or None if not found.
        """
        if isinstance(self.aave_v3, list):
            if instance_name is None:
                # Return first config if no name specified
                return self.aave_v3[0] if self.aave_v3 else None
            for config in self.aave_v3:
                if config.name == instance_name:
                    return config
            return None
        else:
            # Single config - return it if no name specified or if name matches
            if instance_name is None or self.aave_v3.name == instance_name:
                return self.aave_v3
            return None

    def get_aave_v3_configs(self) -> list[AaveV3AdapterSettings]:
        """Get all Aave V3 configs as a list."""
        if isinstance(self.aave_v3, list):
            return self.aave_v3
        return [self.aave_v3]


class OracleSettings(BaseSettings):
    """Single source of truth for configuration. Values may come from:
    - CLI (init kwargs)
    - ENV / .env (prefixed with TQ_ORACLE_)
    - Config file (TOML), lowest precedence

    Do not read os.environ or files elsewhere in the codebase.
    """

    # --- global toggles ---
    dry_run: bool = True
    dry_run_report_indent: bool = True
    additional_asset_support: bool = True

    # --- core addresses / endpoints ---
    vault_address: str | None = None
    oracle_helper_address: str | None = None
    vault_rpc: str | None = None
    eth_mainnet_rpc: str | None = None  # Needed for when vault is not on mainnet
    network: Network = Network.MAINNET
    block_number: int | None = None

    # --- safe / signing ---
    safe_address: str | None = None
    private_key: SecretStr | None = None
    safe_txn_srvc_api_key: SecretStr | None = None

    # --- checks and retries ---
    ignore_empty_vault: bool = False
    ignore_timeout_check: bool = False
    ignore_active_proposal_check: bool = False
    allow_dangerous: bool = False
    pre_check_retries: int = 3
    pre_check_timeout: float = 12.0

    # --- price validation ---
    price_warning_tolerance_percentage: float = Field(
        default=0.5,
        gt=0,
        lt=100.0,
        description="Price deviation warning threshold (%). Must be positive and less than failure threshold.",
    )
    price_failure_tolerance_percentage: float = Field(
        default=1.0,
        gt=0,
        lt=100.0,
        description="Price deviation failure threshold (%). Must be positive and greater than warning threshold.",
    )

    # Pyth-specific settings
    pyth_enabled: bool = True
    pyth_hermes_endpoint: str = "https://hermes.pyth.network"
    pyth_staleness_threshold: int = 60
    pyth_max_confidence_ratio: float = 0.03
    pyth_dynamic_discovery_enabled: bool = True

    # Chainlink-specific settings
    chainlink_enabled: bool = False
    chainlink_eth_usd_feed: str | None = None
    chainlink_stablecoins: list[str] = Field(
        default_factory=list
    )  # Stablecoins to price via Chainlink
    chainlink_staleness_threshold: int = 86400  # Max age in seconds (default 24 hours)

    # CoinGecko-specific settings
    coingecko_enabled: bool = False
    coingecko_api_key: SecretStr | None = (
        None  # Optional - uses free tier if not provided
    )
    coingecko_token_ids: dict[str, str] = Field(
        default_factory=dict
    )  # token_address -> coingecko_id mapping

    # Manual price overrides (highest priority - overrides all price adapters)
    # Format: { "token_address": price_in_wei_18_decimals }
    manual_prices: dict[str, int] = Field(default_factory=dict)

    # --- RPC settings ---
    max_calls: int = 3
    rpc_max_concurrent_calls: int = 5
    rpc_delay: float = 0.15
    rpc_jitter: float = 0.10
    rpc_timeout: float = 30.0  # Timeout in seconds for RPC calls (FYEO-TQO-09)

    # --- logging ---
    log_level: str = "INFO"

    # --- adapters (from config file only) ---
    subvault_adapters: list[dict[str, Any]] = []
    adapters: AdapterSettings = Field(default_factory=AdapterSettings)

    # --- multi-chain configuration ---
    # Primary chain for redemption/deposit (default: use single-chain mode)
    primary_chain: str | None = None
    # List of chains to query for TVL aggregation
    chains: list[ChainConfig] = Field(default_factory=list)
    # List of bridges to track for in-flight reconciliation
    bridges: list[BridgeConfig] = Field(default_factory=list)

    # --- runtime computed values ---
    using_default_rpc: bool = False
    _chain_id: int | None = None
    _oracle_address: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="TQ_ORACLE_",
        env_file=".env",
        extra="ignore",  # ignore unknown keys in env/config file
    )

    @field_validator(
        "private_key", "safe_txn_srvc_api_key", "coingecko_api_key", mode="before"
    )
    @classmethod
    def wrap_secrets(cls, v: Any) -> SecretStr | None:
        """Wrap string secrets in SecretStr."""
        if v is None or isinstance(v, SecretStr):
            return v
        return SecretStr(v)

    @model_validator(mode="after")
    def validate_price_tolerance_ordering(self) -> "OracleSettings":
        """Validate that warning tolerance is less than failure tolerance."""
        if (
            self.price_warning_tolerance_percentage
            >= self.price_failure_tolerance_percentage
        ):
            raise ValueError(
                f"price_warning_tolerance_percentage ({self.price_warning_tolerance_percentage}) "
                f"must be less than price_failure_tolerance_percentage ({self.price_failure_tolerance_percentage})"
            )
        return self

    @model_validator(mode="after")
    def set_derived_values(self) -> "OracleSettings":
        """Compute environment-specific values based on configuration.

        This centralizes all environment selection logic in one place,
        removing the need for if/else checks throughout the codebase.
        """
        return self

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Custom config-file source with explicit precedence: CLI > ENV > FILE."""
        env_cfg = os.environ.get("TQ_ORACLE_CONFIG")
        cfg_path = Path(env_cfg) if env_cfg else None

        class TomlConfigSource(PydanticBaseSettingsSource):
            def __init__(self, settings_cls: type[BaseSettings], path: Path | None):
                super().__init__(settings_cls)
                self._path = path
                self._root_keys = {
                    name
                    for name in settings_cls.model_fields.keys()
                    if not name.startswith("_")
                    and name not in {"subvault_adapters", "using_default_rpc"}
                }

            def get_field_value(
                self, field: Any, field_name: str
            ) -> tuple[Any, str, bool]:
                return None, "", False

            def _promote_root_keys(self, body: dict[str, Any]) -> None:
                """Promote misplaced root-level settings from subvault adapters."""
                adapters = body.get("subvault_adapters")
                if not isinstance(adapters, list):
                    return

                cleaned_adapters: list[Any] = []
                for adapter in adapters:
                    if not isinstance(adapter, dict):
                        cleaned_adapters.append(adapter)
                        continue

                    cleaned_entry: dict[str, Any] = {}
                    for key, value in adapter.items():
                        if key in self._root_keys:
                            body.setdefault(key, value)
                        else:
                            cleaned_entry[key] = value
                    cleaned_adapters.append(cleaned_entry)

                body["subvault_adapters"] = cleaned_adapters

            def __call__(self) -> dict[str, Any]:
                if not self._path:
                    # Try default locations
                    local_config = Path("tq-oracle.toml")
                    user_config = Path.home() / ".config" / "tq-oracle" / "config.toml"
                    if local_config.exists():
                        self._path = local_config
                    elif user_config.exists():
                        self._path = user_config
                    else:
                        return {}

                if not self._path.exists():
                    return {}

                with self._path.open("rb") as f:
                    data = tomllib.load(f)  # supports top-level or [tq_oracle]
                body = data.get("tq_oracle", data)
                if not isinstance(body, dict):
                    return {}

                self._promote_root_keys(body)

                # Check for secrets in config file
                secret_fields = {"private_key", "safe_txn_srvc_api_key"}
                for key in secret_fields:
                    if key in body:
                        raise ValueError(
                            f"Security violation: '{key}' found in TOML config file. "
                            f"Secrets must only be provided via environment variables or CLI flags."
                        )

                return body

        return (
            init_settings,  # CLI (highest)
            env_settings,  # ENV
            TomlConfigSource(settings_cls, cfg_path),  # CONFIG (lowest)
            file_secret_settings,  # optional secrets dir
        )

    def as_safe_dict(self) -> dict[str, Any]:
        """Return the config as a dict with secrets redacted."""
        data = self.model_dump()
        if self.private_key:
            data["private_key"] = "***redacted***"
        if self.safe_txn_srvc_api_key:
            data["safe_txn_srvc_api_key"] = "***redacted***"
        return data

    @property
    def is_broadcast(self) -> bool:
        """Check if Broadcast mode is enabled (Safe address provided and not dry-run)."""
        return self.safe_address is not None and not self.dry_run

    @property
    def vault_address_required(self) -> str:
        """Get vault_address, raising ValueError if not set."""
        if self.vault_address is None:
            raise ValueError("vault_address must be configured")
        return self.vault_address

    @property
    def block_number_required(self) -> int:
        """Get block_number, raising ValueError if not set."""
        if self.block_number is None:
            raise ValueError("block_number must be configured")
        return self.block_number

    @property
    def vault_rpc_required(self) -> str:
        """Get vault_rpc, raising ValueError if not set."""
        if self.vault_rpc is None:
            raise ValueError("vault_rpc must be configured")
        return self.vault_rpc

    @property
    def chain_id(self) -> int:
        """Derive chain ID from the RPC endpoint."""
        if self._chain_id is None:
            if not self.vault_rpc:
                raise ValueError("vault_rpc must be set before accessing chain_id")
            from eth_typing import URI
            from web3 import Web3

            w3 = Web3(
                Web3.HTTPProvider(URI(self.vault_rpc), request_kwargs={"timeout": 15})
            )
            if not w3.is_connected():
                raise ConnectionError(f"Failed to connect to RPC: {self.vault_rpc}")
            self._chain_id = w3.eth.chain_id
        return self._chain_id

    @property
    def oracle_address(self) -> str:
        """Fetch oracle address from the vault contract."""
        if self._oracle_address is None:
            if not self.vault_address or not self.vault_rpc:
                raise ValueError(
                    "vault_address and vault_rpc must be set before accessing oracle_address"
                )
            from .abi import get_oracle_address_from_vault

            self._oracle_address = get_oracle_address_from_vault(self)
        return self._oracle_address

    @property
    def assets(self) -> NetworkAssets:
        """Get the assets for the configured network.

        Returns:
            NetworkAssets for the configured network
        """
        from .constants import (
            BASE_ASSETS,
            ETH_MAINNET_ASSETS,
            HYPEREVM_MAINNET_ASSETS,
            SEPOLIA_ASSETS,
        )

        network_assets_map = {
            Network.MAINNET: ETH_MAINNET_ASSETS,
            Network.SEPOLIA: SEPOLIA_ASSETS,
            Network.BASE: BASE_ASSETS,
            Network.HYPEREVM: HYPEREVM_MAINNET_ASSETS,
            # HYPERCORE doesn't use traditional assets - it's API-based
        }

        if self.network not in network_assets_map:
            if self.network == Network.HYPERCORE:
                # HyperCore uses USDC via API, return minimal asset map
                return HYPEREVM_MAINNET_ASSETS
            raise ValueError(f"Unknown network: {self.network}")

        return network_assets_map[self.network]

    def _resolve_streth_addresses(self) -> StrEthAddresses:
        """Return strETH addresses; only mainnet is supported."""

        if self.network is not Network.MAINNET:
            raise ValueError("strETH adapter is only supported on mainnet")

        from .constants import STRETH_MAINNET_ADDRESSES

        return STRETH_MAINNET_ADDRESSES

    @property
    def streth(self) -> str:
        return self._resolve_streth_addresses()["streth"]

    @property
    def core_vaults_collector(self) -> str:
        return self._resolve_streth_addresses()["core_vaults_collector"]

    @property
    def streth_redemption_asset(self) -> str:
        redemption_asset = self.assets.get("WSTETH")
        if redemption_asset is None:
            raise ValueError(
                "WSTETH deployment not configured for this network; required for strETH redemption"
            )

        return redemption_asset

    @property
    def multicall(self) -> str:
        return self._resolve_streth_addresses()["multicall"]
