"""Blockchain contract address constants."""

from typing import Optional, TypedDict, cast


class NetworkAssets(TypedDict):
    USDC: Optional[str]
    USDT: Optional[str]
    USDS: Optional[str]
    ETH: Optional[str]
    WETH: Optional[str]
    WSTETH: Optional[str]
    OSETH: Optional[str]


class StakewiseAddresses(TypedDict):
    """Hard-coded StakeWise contract addresses per network."""

    os_token: str
    os_token_vault_controller: str
    os_token_vault_escrow: str
    vault: str


class StrEthAddresses(TypedDict):
    """strETH-related contract addresses per network."""

    streth: str
    core_vaults_collector: str
    multicall: str


STRETH_MAINNET_ADDRESSES: StrEthAddresses = {
    "streth": "0x277C6A642564A91ff78b008022D65683cEE5CCC5",
    "core_vaults_collector": "0x551233202dcC8761123c0489c3D59ef602f6BEC6",
    "multicall": "0xcA11bde05977b3631167028862bE2a173976CA11",
}

STRETH_ADDRESSES: dict[str, StrEthAddresses] = {
    "mainnet": STRETH_MAINNET_ADDRESSES,
}

ETH_ASSET = "0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE"
ETH_ADDRESS = ETH_ASSET  # Alias for compatibility
# Native ETH as address(0) - used by Uniswap V4
NATIVE_ETH_ADDRESS = "0x0000000000000000000000000000000000000000"

ETH_MAINNET_ASSETS: NetworkAssets = {
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "USDS": "0xdC035D45d973E3EC169d2276DDab16f1e407384F",
    "ETH": ETH_ASSET,
    "WETH": "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
    "WSTETH": "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0",
    "OSETH": "0xf1C9acDc66974dFB6dEcB12aA385b9cD01190E38",
}

SEPOLIA_ASSETS: NetworkAssets = {
    "USDC": "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238",
    "USDT": None,
    "USDS": None,
    "ETH": ETH_ASSET,
    "WETH": "0xf531B8F309Be94191af87605CfBf600D71C2cFe0",
    "WSTETH": None,
    "OSETH": None,
}

BASE_ASSETS: NetworkAssets = {
    "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "USDT": "0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2",
    "USDS": "0x820c137fa70c8691f0e44dc420a5e53c168921dc",
    "ETH": ETH_ASSET,
    "WETH": "0x4200000000000000000000000000000000000006",
    "WSTETH": "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452",
    "OSETH": None,
}

# Hardcoded overrides, if required
# https://docs.pyth.network/price-feeds/core/price-feeds/price-feed-ids
PYTH_PRICE_FEED_IDS: dict[str, str] = {}

DEFAULT_MAINNET_RPC_URL = "https://eth.drpc.org"
DEFAULT_SEPOLIA_RPC_URL = "https://sepolia.drpc.org"
DEFAULT_BASE_RPC_URL = "https://mainnet.base.org"

MAINNET_ORACLE_HELPER = "0x000000005F543c38d5ea6D0bF10A50974Eb55E35"
SEPOLIA_ORACLE_HELPER = "0x65464fe20562C22B2802B4094d3E042E18b5dfC2"
BASE_ORACLE_HELPER = "0x9bB327889402AC19BF2D164eA79CcfE46c16a37B"

STAKEWISE_MAINNET_ADDRESSES: StakewiseAddresses = {
    "os_token": "0xf1C9acDc66974dFB6dEcB12aA385b9cD01190E38",
    "os_token_vault_controller": "0x2A261e60FB14586B474C208b1B7AC6D0f5000306",
    "os_token_vault_escrow": "0x09e84205DF7c68907e619D07aFD90143c5763605",
    "vault": "0xe6D8d8Ac54461b1c5ed15740eeE322043F696C08",
}

STAKEWISE_ADDRESSES: dict[str, StakewiseAddresses] = {
    "mainnet": STAKEWISE_MAINNET_ADDRESSES,
}

# Aave V3 token addresses per network
# Pool address for Aave V3 on mainnet
AAVE_V3_POOL_MAINNET = "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"

# Aave V3 aTokens (supply) on mainnet
AAVE_V3_SUPPLY_TOKENS_MAINNET: dict[str, str] = {
    "WETH": "0x4d5F47FA6A74757f35C14fD3a6Ef8E3C9BC514E8",
    "wstETH": "0x0B925eD163218f6662a35e0f0371Ac234f9E9371",
    "USDC": "0x98C23E9d8f34FEFb1B7BD6a91B7FF122F4e16F5c",
    "USDT": "0x23878914EFE38d27C4D67Ab83ed1b93A74D4086a",
    "USDe": "0x4F5923Fc5FD4a93352581b38B7cD26943012DECF",  # aEthUSDe
}

# Aave V3 variable debt tokens (borrow) on mainnet
AAVE_V3_BORROW_TOKENS_MAINNET: dict[str, str] = {
    "WETH": "0xeA51d7853EEFb32b6ee06b1C12E6dcCA88Be0fFE",
    "wstETH": "0xC96113eED8cAB59cD8A66813bCB0cEb29F06D2e4",
    "USDC": "0x72E95b8931767C79bA4EeE721354d6E99a61D004",
    "USDT": "0x6df1C1E379bC5a00a7b4C6e67A203333772f45A8",
    "USDe": "0x015396E1F286289aE23a762088E863b3ec465145",  # Variable debt USDe
}

# Pendle oracle address on mainnet
# https://docs.pendle.finance/Developers/Contracts/PendleOracle
PENDLE_ORACLE_MAINNET = "0x9a9Fa8338dd5E5B2188006f1Cd2Ef26d921650C2"

# Example Pendle markets structure (can be overridden in TOML):
# This is just for reference - actual markets should be configured per-deployment
PENDLE_MARKETS_EXAMPLE: dict[str, dict[str, str]] = {
    # "market_name": {
    #     "market": "0x...",  # Pendle market address
    #     "accounting_asset": "0x...",  # Asset to price in (e.g., USDC, WETH)
    # }
}

# Chainlink price feed addresses
# https://docs.chain.link/data-feeds/price-feeds/addresses
CHAINLINK_ETH_USD_MAINNET = (
    "0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419"  # ETH/USD (8 decimals)
)
CHAINLINK_ETH_USD_SEPOLIA = (
    "0x694AA1769357215DE4FAC081bf1f309aDC325306"  # ETH/USD (8 decimals)
)
CHAINLINK_ETH_USD_BASE = (
    "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70"  # ETH/USD (8 decimals)
)

CHAINLINK_FEEDS: dict[str, str] = {
    "mainnet": CHAINLINK_ETH_USD_MAINNET,
    "sepolia": CHAINLINK_ETH_USD_SEPOLIA,
    "base": CHAINLINK_ETH_USD_BASE,
}

# CoinGecko API settings
COINGECKO_API_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_PRO_API_BASE_URL = "https://pro-api.coingecko.com/api/v3"

# Default CoinGecko ID mappings for common tokens
# Format: token_address (lowercase) -> coingecko_id
COINGECKO_DEFAULT_IDS: dict[str, str] = {
    # Mainnet stablecoins
    "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": "usd-coin",  # USDC
    "0xdac17f958d2ee523a2206206994597c13d831ec7": "tether",  # USDT
    "0x6b175474e89094c44da98b954eedeac495271d0f": "dai",  # DAI
    "0x4c9edd5852cd905f086c759e8383e09bff1e68b3": "usde",  # USDe
    "0x0000206329b97db379d5e1bf586bbdb969c63274": "usda",  # USDA
    # ETH and derivatives
    "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2": "weth",  # WETH
    "0x7f39c581f595b53c5cb19bd0b3f8da6c935e2ca0": "wrapped-steth",  # wstETH
    "0xf1c9acdc66974dfb6decb12aa385b9cd01190e38": "os-eth",  # osETH
}

DEFAULT_ADDITIONAL_ASSETS: dict[str, dict[str, str]] = {
    "mainnet": {
        "osETH": STAKEWISE_MAINNET_ADDRESSES["os_token"],
    },
    "base": {
        "USDC": cast(str, BASE_ASSETS["USDC"]),
    },
}


STAKEWISE_EXIT_LOG_CHUNK = 1_000
STAKEWISE_EXIT_MAX_LOOKBACK_BLOCKS = 28_800  # ~4 days on 12s blocks

TOKEN_MESSENGER_V2_PROD = "0x28b5a0e9C621a5BadaA536219b3a228C8168cf5d"
TOKEN_MESSENGER_V2_TEST = "0x8FE6B999Dc680CcFDD5Bf7EB0974218be2542DAA"

RPC_RATE_LIMIT_DELAY = (
    5  # Delay in seconds between RPC calls to avoid rate limits with get_logs()
)
L1_BLOCK_TIME = 12  # Ethereum L1 block time in seconds

# Retry Configuration for Post-Checks
MAX_RETRY_ATTEMPTS = 5
RETRY_DELAY_SECONDS = 120  # 2 minutes

# =============================================================================
# HyperEVM / HyperCore Constants
# =============================================================================

# HyperEVM RPC endpoints
HYPEREVM_MAINNET_RPC = "https://rpc.hyperliquid.xyz/evm"
HYPEREVM_TESTNET_RPC = "https://rpc.hyperliquid-testnet.xyz/evm"

# HyperCore API endpoints
HYPERCORE_MAINNET_API = "https://api.hyperliquid.xyz"
HYPERCORE_TESTNET_API = "https://api.hyperliquid-testnet.xyz"

# USDC on HyperEVM (native Circle USDC via CCTP V2)
USDC_HYPEREVM_MAINNET = "0xb88339CB7199b77E23DB6E890353E22632Ba630f"
USDC_HYPEREVM_TESTNET = "0x2B3370eE501B4a559b57D449569354196457D8Ab"

# HyperEVM system addresses for bridging to/from HyperCore
HYPEREVM_USDC_SYSTEM_ADDRESS = "0x2000000000000000000000000000000000000000"
HYPEREVM_HYPE_SYSTEM_ADDRESS = "0x2222222222222222222222222222222222222222"

# CoreDepositWallet - for HyperEVM → HyperCore transfers
CORE_DEPOSIT_WALLET_MAINNET = "0x6b9e773128f453f5c2c60935ee2de2cbc5390a24"

# CoreWriter - for executing trades on HyperCore from HyperEVM
CORE_WRITER_ADDRESS = "0x3333333333333333333333333333333333333333"

# Block times in seconds
HYPEREVM_BLOCK_TIME = 1  # HyperEVM/HyperCore block time
HL_BLOCK_TIME = HYPEREVM_BLOCK_TIME  # Alias for backwards compatibility
ARBITRUM_BLOCK_TIME = 1  # Arbitrum L2 (~0.25s actual, use 1 for safety)
BASE_BLOCK_TIME = 2  # Base L2 block time

# CCTP lookback configuration
CCTP_LOOKBACK_BLOCKS = 80  # ~16 minutes on L1 (80 * 12s)
CCTP_RATE_LIMITED_LOOKBACK_BLOCKS = 80  # Same, for rate-limited RPCs

# HyperCore portfolio staleness threshold
HL_MAX_PORTFOLIO_STALENESS_SECONDS = 120  # 2 minutes

# HyperEVM assets (similar structure to ETH_MAINNET_ASSETS)
HYPEREVM_MAINNET_ASSETS: NetworkAssets = {
    "USDC": USDC_HYPEREVM_MAINNET,
    "USDT": None,
    "USDS": None,
    "ETH": None,  # No native ETH on HyperEVM
    "WETH": None,
    "WSTETH": None,
    "OSETH": None,
}

HYPEREVM_TESTNET_ASSETS: NetworkAssets = {
    "USDC": USDC_HYPEREVM_TESTNET,
    "USDT": None,
    "USDS": None,
    "ETH": None,
    "WETH": None,
    "WSTETH": None,
    "OSETH": None,
}
