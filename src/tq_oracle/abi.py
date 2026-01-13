from __future__ import annotations

from typing import cast

import asyncio

import json
from pathlib import Path

from eth_typing import URI, ChecksumAddress
from web3 import Web3

from tq_oracle.settings import OracleSettings

ABIS_DIR = Path(__file__).parent / "abis"

CORE_VAULTS_COLLECTOR_PATH = ABIS_DIR / "CoreVaultsCollector.json"
MULTICALL_ABI_PATH = ABIS_DIR / "Multicall.json"
ORACLE_ABI_PATH = ABIS_DIR / "IOracle.json"
ORACLE_HELPER_ABI_PATH = ABIS_DIR / "OracleHelper.json"
VAULT_ABI_PATH = ABIS_DIR / "Vault.json"
AGGREGATOR_ABI_PATH = ABIS_DIR / "AggregatorV3Interface.json"
ERC20_ABI_PATH = ABIS_DIR / "ERC20.json"
GNOSIS_SAFE_ABI_PATH = ABIS_DIR / "SafeL2.json"
WSTETH_ABI_PATH = ABIS_DIR / "WstETH.json"
FEE_MANAGER_ABI_PATH = ABIS_DIR / "FeeManager.json"
STAKEWISE_VAULT_ABI_PATH = ABIS_DIR / "StakeWiseVault.json"
STAKEWISE_OS_TOKEN_VAULT_ESCROW_ABI_PATH = ABIS_DIR / "StakeWiseOsTokenVaultEscrow.json"
OSTOKEN_VAULT_CONTROLLER_ABI_PATH = ABIS_DIR / "OsTokenVaultController.json"
ERC4626_ABI_PATH = ABIS_DIR / "ERC4626.json"
SNUSD_ABI_PATH = ABIS_DIR / "SNUSD.json"
PENDLE_ORACLE_ABI_PATH = ABIS_DIR / "PendleOracle.json"
PENDLE_MARKET_ABI_PATH = ABIS_DIR / "PendleMarket.json"
CHAINLINK_FEED_ABI_PATH = ABIS_DIR / "ChainlinkFeed.json"
UNISWAP_V4_POSITION_MANAGER_ABI_PATH = ABIS_DIR / "UniswapV4PositionManager.json"
UNISWAP_V4_STATE_VIEW_ABI_PATH = ABIS_DIR / "UniswapV4StateView.json"


def load_abi(path: str | Path) -> list[dict]:
    """Load an ABI from a JSON file and return its "abi" field.

    Args:
        path: Path to the JSON file containing an "abi" field.

    Returns:
        ABI as a list of dictionaries.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
        KeyError: If the JSON does not contain an "abi" field.
    """
    p = Path(path)
    with p.open() as f:
        data = json.load(f)
    return data["abi"]


def load_core_vaults_collector_abi() -> list[dict]:
    return load_abi(CORE_VAULTS_COLLECTOR_PATH)


def load_multicall_abi() -> list[dict]:
    """Load the Multicall ABI."""
    return load_abi(MULTICALL_ABI_PATH)


def load_oracle_abi() -> list[dict]:
    """Load the Oracle ABI."""
    return load_abi(ORACLE_ABI_PATH)


def load_oracle_helper_abi() -> list[dict]:
    """Load the OracleHelper ABI."""
    return load_abi(ORACLE_HELPER_ABI_PATH)


def load_vault_abi() -> list[dict]:
    """Load the Vault ABI."""
    return load_abi(VAULT_ABI_PATH)


def load_fee_manager_abi() -> list[dict]:
    """Load the Fee Manager ABI."""
    return load_abi(FEE_MANAGER_ABI_PATH)


def load_aggregator_abi() -> list[dict]:
    """Load the Aggregator ABI."""
    return load_abi(AGGREGATOR_ABI_PATH)


def load_erc20_abi() -> list[dict]:
    """Load the ERC20 ABI."""
    return load_abi(ERC20_ABI_PATH)


def load_wsteth_abi() -> list[dict]:
    """Load the WstETH ABI."""
    return load_abi(WSTETH_ABI_PATH)


def load_stakewise_vault_abi() -> list[dict]:
    """Load the StakeWise vault ABI."""
    return load_abi(STAKEWISE_VAULT_ABI_PATH)


def load_stakewise_os_token_vault_escrow_abi() -> list[dict]:
    """Load the StakeWise osToken vault escrow ABI."""
    return load_abi(STAKEWISE_OS_TOKEN_VAULT_ESCROW_ABI_PATH)


def load_ostoken_vault_controller_abi() -> list[dict]:
    """Load the OsToken Vault Controller ABI."""
    return load_abi(OSTOKEN_VAULT_CONTROLLER_ABI_PATH)


def load_erc4626_abi() -> list[dict]:
    """Load the ERC4626 vault ABI."""
    return load_abi(ERC4626_ABI_PATH)


def load_snusd_abi() -> list[dict]:
    """Load the sNUSD ABI."""
    return load_abi(SNUSD_ABI_PATH)


def load_pendle_oracle_abi() -> list[dict]:
    """Load the Pendle Oracle ABI."""
    return load_abi(PENDLE_ORACLE_ABI_PATH)


def load_pendle_market_abi() -> list[dict]:
    """Load the Pendle Market ABI."""
    return load_abi(PENDLE_MARKET_ABI_PATH)


def load_chainlink_feed_abi() -> list[dict]:
    """Load the Chainlink Feed ABI."""
    return load_abi(CHAINLINK_FEED_ABI_PATH)


def load_uniswap_v4_position_manager_abi() -> list[dict]:
    """Load the Uniswap V4 PositionManager ABI."""
    return load_abi(UNISWAP_V4_POSITION_MANAGER_ABI_PATH)


def load_uniswap_v4_state_view_abi() -> list[dict]:
    """Load the Uniswap V4 StateView ABI."""
    return load_abi(UNISWAP_V4_STATE_VIEW_ABI_PATH)


def get_oracle_address_from_vault(settings: OracleSettings) -> ChecksumAddress:
    """Fetch the oracle address from the vault contract.

    Args:
        vault_address: The vault contract address
        rpc_url: RPC endpoint URL

    Returns:
        The oracle contract address from the vault

    Raises:
        ConnectionError: If RPC connection fails
        ValueError: If contract call fails
    """
    rpc_url = settings.vault_rpc_required
    vault_address = settings.vault_address_required
    block_number = settings.block_number_required
    w3 = Web3(Web3.HTTPProvider(URI(rpc_url)))
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")

    vault_abi = load_vault_abi()
    checksum_vault = w3.to_checksum_address(vault_address)
    vault_contract = w3.eth.contract(address=checksum_vault, abi=vault_abi)

    try:
        oracle_addr: ChecksumAddress = vault_contract.functions.oracle().call(
            block_identifier=block_number
        )
        return oracle_addr
    except Exception as e:
        raise ValueError(f"Failed to fetch oracle address from vault: {e}") from e


async def fetch_subvault_addresses(settings: OracleSettings) -> list[str]:
    """Fetch all subvault addresses from the vault contract.

    Args:
        settings: Oracle settings containing vault_address, vault_rpc, and block_number

    Returns:
        List of subvault addresses

    Raises:
        ConnectionError: If RPC connection fails
        ValueError: If contract call fails
    """
    rpc_url = settings.vault_rpc_required
    vault_address = settings.vault_address_required
    block_number = settings.block_number_required

    w3 = Web3(Web3.HTTPProvider(URI(rpc_url)))
    if not w3.is_connected():
        raise ConnectionError(f"Failed to connect to RPC: {rpc_url}")

    vault_abi = load_vault_abi()
    checksum_vault = w3.to_checksum_address(vault_address)
    vault_contract = w3.eth.contract(address=checksum_vault, abi=vault_abi)

    try:
        count: int = await asyncio.to_thread(
            vault_contract.functions.subvaults().call,
            block_identifier=block_number,
        )
        subvaults = await asyncio.gather(
            *[
                asyncio.to_thread(
                    vault_contract.functions.subvaultAt(i).call,
                    block_identifier=block_number,
                )
                for i in range(count)
            ]
        )

        return cast(list[str], subvaults)
    except Exception as e:
        raise ValueError(f"Failed to fetch subvault addresses from vault: {e}") from e
