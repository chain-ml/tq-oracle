from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import backoff
from eth.constants import ZERO_ADDRESS
from eth_abi.abi import decode, encode
from web3 import Web3
from web3.eth import Contract
from web3.exceptions import ProviderConnectionError

from ...abi import load_core_vaults_collector_abi, load_multicall_abi, load_vault_abi
from ...logger import get_logger
from ...settings import Network, OracleSettings
from .base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    # Kept for type checkers that prefer local import inference.
    from ...settings import OracleSettings

logger = get_logger(__name__)


class StrETHAdapter(BaseAssetAdapter):
    streth_address: str
    streth_redemption_asset: str
    core_vaults_collector: str
    multicall: Contract
    w3: Web3

    def __init__(self, config: OracleSettings):
        """Initialize the adapter.

        Args:
            config: Oracle configuration
        """
        super().__init__(config)

        self._skip = config.network != Network.MAINNET
        if self._skip:
            logger.info(
                "Skipping strETH adapter: network=%s (mainnet only)",
                config.network.value,
            )
            return

        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError(
                f"Failed to connect to RPC: {config.vault_rpc_required}"
            )
        self.block_number = config.block_number_required

        self.vault_address = Web3.to_checksum_address(config.vault_address_required)
        self.streth_address = Web3.to_checksum_address(config.streth)
        self.core_vaults_collector = Web3.to_checksum_address(
            config.core_vaults_collector
        )
        self.streth_redemption_asset = Web3.to_checksum_address(
            config.streth_redemption_asset
        )

        self.multicall: Contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.multicall), abi=load_multicall_abi()
        )

        self._rpc_sem = asyncio.Semaphore(getattr(self.config, "max_calls", 5))
        self._rpc_delay = getattr(self.config, "rpc_delay", 0.15)  # seconds
        self._rpc_jitter = getattr(self.config, "rpc_jitter", 0.10)  # seconds

    @backoff.on_exception(
        backoff.expo, (ProviderConnectionError), max_time=30, jitter=backoff.full_jitter
    )
    async def _rpc(self, fn, *args, **kwargs):
        """Throttle + backoff a single RPC."""
        async with self._rpc_sem:
            try:
                return await asyncio.to_thread(fn, *args, **kwargs)
            finally:
                delay = self._rpc_delay + random.random() * self._rpc_jitter
                if delay > 0:
                    await asyncio.sleep(delay)

    @property
    def adapter_name(self) -> str:
        return "streth"

    async def _fetch_assets(self, subvault_addresses: list[str]) -> list[AssetData]:
        """Fetch strETH positions for the given subvaults on the configured chain.

        Args:
            subvault_addresses: List of subvaults to query

        Returns:
            List of AssetData objects containing asset addresses and balances
        """
        collector = self.w3.eth.contract(
            Web3.to_checksum_address(self.core_vaults_collector),
            abi=load_core_vaults_collector_abi(),
        )

        calls = []
        for subvault in subvault_addresses:
            calls.append(
                [
                    Web3.to_checksum_address(collector.address),
                    collector.encode_abi(
                        "getDistributions",
                        args=[
                            Web3.to_checksum_address(subvault),
                            encode(
                                ["address", "address"],
                                [self.streth_address, self.streth_redemption_asset],
                            ),
                            [],
                        ],
                    ),
                ]
            )

        call_results = (
            await self._rpc(
                self.multicall.functions.aggregate(calls).call,
                block_identifier=self.block_number,
            )
        )[1]

        cumulative_amounts: dict[str, int] = {}
        for call_result in call_results:
            try:
                balances = list(
                    decode(["(address,int256,string,address)[]"], call_result)[0]
                )
            except Exception as exc:
                logger.error("Failed to decode distributions from multicall: %s", exc)
                raise ValueError("Invalid distributions data from multicall") from exc
            for asset, amount, _, _ in balances:
                if amount != 0:
                    cumulative_amounts[asset] = (
                        cumulative_amounts.get(asset, 0) + amount
                    )

        result: list[AssetData] = []
        for asset, amount in cumulative_amounts.items():
            checksum = Web3.to_checksum_address(asset)
            if checksum == ZERO_ADDRESS:
                raise ValueError("Received zero asset address in strETH distributions")
            result.append(AssetData(checksum, amount))
        return result

    async def fetch_assets(self, subvault_address: str, previous_assets: list[AssetData] | None = None) -> list[AssetData]:
        if self._skip:
            return []
        return await self._fetch_assets([subvault_address])

    async def fetch_all_assets(self) -> list[AssetData]:
        """Fetch strETH positions for all subvaults of the vault on the configured chain.

        Returns:
            List of AssetData objects containing asset addresses and balances
        """
        if self._skip:
            return []
        vault_contract: Contract = self.w3.eth.contract(
            address=self.vault_address, abi=load_vault_abi()
        )
        count: int = await self._rpc(
            vault_contract.functions.subvaults().call,
            block_identifier=self.block_number,
        )

        calls = [
            [
                vault_contract.address,
                vault_contract.encode_abi("subvaultAt", args=[index]),
            ]
            for index in range(count)
        ]
        responses = (
            await self._rpc(
                self.multicall.functions.aggregate(calls).call,
                block_identifier=self.block_number,
            )
        )[1]
        try:
            subvaults = [decode(["address"], response)[0] for response in responses]
        except Exception as exc:
            logger.error("Failed to decode subvaultAt responses: %s", exc)
            raise ValueError("Invalid subvault address data from multicall") from exc
        return await self._fetch_assets(subvaults)
