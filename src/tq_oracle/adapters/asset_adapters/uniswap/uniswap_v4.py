"""Uniswap V4 LP position adapter.

This adapter handles Uniswap V4 positions by:
1. Discovering PositionManager tokenIds owned by a subvault via the Uniswap v4 subgraph
2. Fetching poolKey + packed position info via PositionManager.getPoolAndPositionInfo(tokenId)
3. Fetching liquidity via PositionManager.getPositionLiquidity(tokenId)
4. Reading slot0 via StateView.getSlot0(poolId) where poolId = keccak256(abi.encode(poolKey))
5. Calculating withdrawable amounts for both tokens (using V3-style concentrated liquidity math)
"""

from __future__ import annotations

import asyncio
import os
import random
from typing import TYPE_CHECKING, Any

import backoff
import requests
from eth_abi import encode as abi_encode
from eth_utils import keccak
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ....abi import load_uniswap_v4_position_manager_abi, load_uniswap_v4_state_view_abi
from ....constants import ETH_MAINNET_ASSETS, NATIVE_ETH_ADDRESS
from ....logger import get_logger
from ..base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ....settings import OracleSettings

logger = get_logger(__name__)


class UniswapV4Adapter(BaseAssetAdapter):
    """Adapter for Uniswap V4 LP positions.

    Discovers positions via The Graph subgraph and calculates withdrawable amounts
    using concentrated liquidity math.
    """

    # Mainnet addresses
    DEFAULT_POOL_MANAGER = "0x000000000004444c5dc75cb358380d2e3de08a90"
    DEFAULT_STATE_VIEW = "0x7fFE42C4a5DEeA5b0feC41C94C136Cf115597227"  # StateView lens

    # Uniswap v4 subgraph (The Graph Network) – requires API key
    DEFAULT_SUBGRAPH_ID = "DiYPVdygkfjDWhbxGSqAQxwBKmfKnkWQojqeM2rkLb3G"

    def __init__(self, config: OracleSettings, **overrides: Any):
        super().__init__(config)

        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for Uniswap V4 adapter")

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout

        adapter_config = config.adapters.uniswap_v4

        self.position_manager = (
            overrides.get("position_manager") or adapter_config.position_manager or None
        )
        self.pool_manager = (
            overrides.get("pool_manager")
            or adapter_config.pool_manager
            or self.DEFAULT_POOL_MANAGER
        )

        # StateView is not in your TOML today; we default to mainnet deployment.
        self.state_view = overrides.get("state_view") or self.DEFAULT_STATE_VIEW

        # Subgraph config
        self.subgraph_id = overrides.get("subgraph_id") or os.getenv(
            "TQ_ORACLE_UNISWAP_V4_SUBGRAPH_ID", self.DEFAULT_SUBGRAPH_ID
        )
        self.graph_api_key = overrides.get("graph_api_key") or os.getenv(
            "TQ_ORACLE_GRAPH_API_KEY"
        )

        # CRITICAL: Graph API key is required for Uniswap V4 position discovery
        if not self.graph_api_key:
            raise ValueError(
                "Uniswap V4 adapter requires TQ_ORACLE_GRAPH_API_KEY environment variable. "
                "The Graph API is required to discover V4 positions (ERC721Enumerable not supported). "
                "Get an API key at https://thegraph.com/studio/"
            )

        # HTTP session for GraphQL requests (FYEO-TQO-07)
        self._session = requests.Session()

        logger.debug(
            "Uniswap V4 adapter initialized: position_manager=%s pool_manager=%s state_view=%s subgraph_id=%s",
            self.position_manager,
            self.pool_manager,
            self.state_view,
            self.subgraph_id,
        )

    @property
    def adapter_name(self) -> str:
        return "uniswap_v4"

    @backoff.on_exception(
        backoff.expo,
        (ProviderConnectionError,),
        max_time=30,
        jitter=backoff.full_jitter,
    )
    async def _rpc(self, fn, *args, **kwargs):
        """Execute RPC call with throttling, timeout, and retry (FYEO-TQO-09)."""
        async with self._rpc_sem:
            result = await asyncio.wait_for(
                asyncio.to_thread(fn, *args, **kwargs),
                timeout=self._rpc_timeout,
            )
        # Sleep outside semaphore to not block other tasks
        delay = self._rpc_delay + random.random() * self._rpc_jitter
        if delay > 0:
            await asyncio.sleep(delay)
        return result

    def _pm(self):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(self.position_manager),
            abi=load_uniswap_v4_position_manager_abi(),
        )

    def _state_view_contract(self):
        return self.w3.eth.contract(
            address=Web3.to_checksum_address(self.state_view),
            abi=load_uniswap_v4_state_view_abi(),
        )

    def _normalize_currency(self, currency: str) -> str:
        """Convert native ETH (address(0)) to WETH for pricing.

        Uniswap V4 represents native ETH as address(0). For pricing purposes,
        we convert this to WETH since price adapters work with ERC20 tokens.
        """
        if currency.lower() == NATIVE_ETH_ADDRESS.lower():
            weth = ETH_MAINNET_ASSETS.get("WETH")
            if weth:
                logger.debug("Converting native ETH (address(0)) to WETH: %s", weth)
                return weth
            logger.warning(
                "WETH address not found in ETH_MAINNET_ASSETS, using native ETH address"
            )
        return currency

    def _graph_url(self) -> str:
        if not self.graph_api_key:
            raise ValueError(
                "Missing TQ_ORACLE_GRAPH_API_KEY (required to query Uniswap v4 subgraph tokenIds)"
            )
        return f"https://gateway.thegraph.com/api/{self.graph_api_key}/subgraphs/id/{self.subgraph_id}"

    @backoff.on_exception(
        backoff.expo,
        (requests.exceptions.RequestException, requests.exceptions.HTTPError),
        max_time=30,
        giveup=lambda e: isinstance(e, requests.exceptions.HTTPError)
        and e.response is not None
        and e.response.status_code not in [429, 500, 502, 503, 504],
        jitter=backoff.full_jitter,
    )
    async def _graphql_request(self, payload: dict) -> dict:
        """Execute GraphQL request with retry logic (FYEO-TQO-08).

        Args:
            payload: GraphQL query payload with 'query' and 'variables'

        Returns:
            Parsed JSON response data

        Raises:
            requests.exceptions.HTTPError: On non-retryable HTTP errors
            ValueError: On GraphQL query errors
        """
        resp = await asyncio.to_thread(
            self._session.post,
            self._graph_url(),
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise ValueError(f"Subgraph query errors: {data['errors']}")
        return data

    async def _fetch_token_ids_from_subgraph(self, owner: str) -> list[int]:
        # Uniswap subgraph schema uses Bytes for owner; typically lowercase hex with 0x prefix
        owner_lc = owner.lower()

        query = """
        query Positions($owner: Bytes!, $first: Int!, $skip: Int!) {
          positions(where: { owner: $owner }, first: $first, skip: $skip) {
            id
          }
        }
        """

        token_ids: list[int] = []
        first = 1000
        skip = 0

        while True:
            payload = {
                "query": query,
                "variables": {"owner": owner_lc, "first": first, "skip": skip},
            }
            data = await self._graphql_request(payload)

            rows = data.get("data", {}).get("positions", []) or []
            if not rows:
                break

            for row in rows:
                # position.id is the NFT tokenId (string)
                token_ids.append(int(row["id"]))

            if len(rows) < first:
                break
            skip += first

        return token_ids

    async def _owner_of(self, token_id: int) -> str:
        owner = await self._rpc(
            self._pm().functions.ownerOf(token_id).call,
            block_identifier=self.block_number,
        )
        return str(owner)

    async def _get_pool_and_info(self, token_id: int):
        pool_key, info = await self._rpc(
            self._pm().functions.getPoolAndPositionInfo(token_id).call,
            block_identifier=self.block_number,
        )
        # pool_key is (currency0, currency1, fee, tickSpacing, hooks)
        return pool_key, int(info)

    async def _get_liquidity(self, token_id: int) -> int:
        liq = await self._rpc(
            self._pm().functions.getPositionLiquidity(token_id).call,
            block_identifier=self.block_number,
        )
        return int(liq)

    @staticmethod
    def _sign_extend_int24(x: int) -> int:
        x &= (1 << 24) - 1
        if x & (1 << 23):
            x -= 1 << 24
        return x

    def _unpack_ticks_from_info(self, info: int) -> tuple[int, int]:
        # Packed PositionInfo:
        # lowest 8 bits = flags (e.g. hasSubscriber)
        # next 24 bits = tickLower (int24)
        # next 24 bits = tickUpper (int24)
        tick_lower_u = (info >> 8) & ((1 << 24) - 1)
        tick_upper_u = (info >> (8 + 24)) & ((1 << 24) - 1)
        return self._sign_extend_int24(tick_lower_u), self._sign_extend_int24(
            tick_upper_u
        )

    def _pool_id_from_pool_key(self, pool_key: tuple) -> bytes:
        currency0, currency1, fee, tick_spacing, hooks = pool_key

        enc = abi_encode(
            ["address", "address", "uint24", "int24", "address"],
            [
                Web3.to_checksum_address(currency0),
                Web3.to_checksum_address(currency1),
                int(fee),
                int(tick_spacing),
                Web3.to_checksum_address(hooks),
            ],
        )
        return keccak(enc)

    async def _get_slot0(self, pool_id: bytes) -> tuple[int, int]:
        slot0 = await self._rpc(
            self._state_view_contract().functions.getSlot0(pool_id).call,
            block_identifier=self.block_number,
        )
        sqrt_price_x96 = int(slot0[0])
        tick = int(slot0[1])
        return sqrt_price_x96, tick

    # --- math copied from your v3 adapter (unchanged) ---

    def _get_sqrt_ratio_at_tick(self, tick: int) -> int:
        abs_tick = abs(tick)
        ratio = (
            0xFFFCB933BD6FAD37AA2D162D1A594001
            if abs_tick & 0x1
            else 0x100000000000000000000000000000000
        )

        if abs_tick & 0x2:
            ratio = (ratio * 0xFFF97272373D413259A46990580E213A) >> 128
        if abs_tick & 0x4:
            ratio = (ratio * 0xFFF2E50F5F656932EF12357CF3C7FDCC) >> 128
        if abs_tick & 0x8:
            ratio = (ratio * 0xFFE5CACA7E10E4E61C3624EAA0941CD0) >> 128
        if abs_tick & 0x10:
            ratio = (ratio * 0xFFCB9843D60F6159C9DB58835C926644) >> 128
        if abs_tick & 0x20:
            ratio = (ratio * 0xFF973B41FA98C081472E6896DFB254C0) >> 128
        if abs_tick & 0x40:
            ratio = (ratio * 0xFF2EA16466C96A3843EC78B326B52861) >> 128
        if abs_tick & 0x80:
            ratio = (ratio * 0xFE5DEE046A99A2A811C461F1969C3053) >> 128
        if abs_tick & 0x100:
            ratio = (ratio * 0xFCBE86C7900A88AEDCFFC83B479AA3A4) >> 128
        if abs_tick & 0x200:
            ratio = (ratio * 0xF987A7253AC413176F2B074CF7815E54) >> 128
        if abs_tick & 0x400:
            ratio = (ratio * 0xF3392B0822B70005940C7A398E4B70F3) >> 128
        if abs_tick & 0x800:
            ratio = (ratio * 0xE7159475A2C29B7443B29C7FA6E889D9) >> 128
        if abs_tick & 0x1000:
            ratio = (ratio * 0xD097F3BDFD2022B8845AD8F792AA5825) >> 128
        if abs_tick & 0x2000:
            ratio = (ratio * 0xA9F746462D870FDF8A65DC1F90E061E5) >> 128
        if abs_tick & 0x4000:
            ratio = (ratio * 0x70D869A156D2A1B890BB3DF62BAF32F7) >> 128
        if abs_tick & 0x8000:
            ratio = (ratio * 0x31BE135F97D08FD981231505542FCFA6) >> 128
        if abs_tick & 0x10000:
            ratio = (ratio * 0x9AA508B5B7A84E1C677DE54F3E99BC9) >> 128
        if abs_tick & 0x20000:
            ratio = (ratio * 0x5D6AF8DEDB81196699C329225EE604) >> 128
        if abs_tick & 0x40000:
            ratio = (ratio * 0x2216E584F5FA1EA926041BEDFE98) >> 128
        if abs_tick & 0x80000:
            ratio = (ratio * 0x48A170391F7DC42444E8FA2) >> 128

        if tick > 0:
            ratio = (2**256 - 1) // ratio

        return (ratio >> 32) + (1 if ratio % (1 << 32) > 0 else 0)

    def _get_amount0_delta(self, sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
        if sqrt_a > sqrt_b:
            sqrt_a, sqrt_b = sqrt_b, sqrt_a
        numerator1 = liquidity << 96
        numerator2 = sqrt_b - sqrt_a
        return (numerator1 * numerator2) // sqrt_b // sqrt_a

    def _get_amount1_delta(self, sqrt_a: int, sqrt_b: int, liquidity: int) -> int:
        if sqrt_a > sqrt_b:
            sqrt_a, sqrt_b = sqrt_b, sqrt_a
        return (liquidity * (sqrt_b - sqrt_a)) >> 96

    def _calculate_amounts_from_liquidity(
        self,
        liquidity: int,
        sqrt_price_x96: int,
        tick_lower: int,
        tick_upper: int,
        current_tick: int,
    ) -> tuple[int, int]:
        if liquidity == 0:
            return 0, 0

        sqrt_lower = self._get_sqrt_ratio_at_tick(tick_lower)
        sqrt_upper = self._get_sqrt_ratio_at_tick(tick_upper)

        if current_tick < tick_lower:
            return self._get_amount0_delta(sqrt_lower, sqrt_upper, liquidity), 0
        if current_tick >= tick_upper:
            return 0, self._get_amount1_delta(sqrt_lower, sqrt_upper, liquidity)

        amount0 = self._get_amount0_delta(sqrt_price_x96, sqrt_upper, liquidity)
        amount1 = self._get_amount1_delta(sqrt_lower, sqrt_price_x96, liquidity)
        return amount0, amount1

    async def _process_position(
        self, token_id: int, subvault_address: str
    ) -> list[AssetData]:
        try:
            pm = self._pm()

            # 1) Preflight ownership (this is the quickest way to detect "wrong block" / non-existent token)
            try:
                owner = await self._rpc(
                    pm.functions.ownerOf(token_id).call,
                    block_identifier=self.block_number,
                )
            except Exception as e:
                logger.error(
                    "Uniswap V4 tokenId %d: ownerOf() reverted at block %s (likely token doesn't exist yet at that block): %s",
                    token_id,
                    str(self.block_number),
                    e,
                )
                return []

            if owner.lower() != subvault_address.lower():
                logger.debug(
                    "Uniswap V4 tokenId %d: owner is %s (not %s) at block %s; skipping",
                    token_id,
                    owner,
                    subvault_address,
                    str(self.block_number),
                )
                return []

            # 2) Pool key + packed info
            pool_key, info = await self._get_pool_and_info(token_id)
            tick_lower, tick_upper = self._unpack_ticks_from_info(info)

            currency0, currency1, fee, tick_spacing, hooks = pool_key
            pool_id = self._pool_id_from_pool_key(pool_key)

            # 3) Liquidity
            liquidity = await self._get_liquidity(token_id)
            if liquidity == 0:
                logger.debug("Uniswap V4 position %d: zero liquidity", token_id)
                return []

            # 4) Slot0 from StateView
            sqrt_price_x96, current_tick = await self._get_slot0(pool_id)

            # 5) Amounts
            amount0, amount1 = self._calculate_amounts_from_liquidity(
                liquidity,
                sqrt_price_x96,
                tick_lower,
                tick_upper,
                current_tick,
            )

            logger.info(
                "Uniswap V4 position %d: c0=%s amt0=%d c1=%s amt1=%d fee=%d liq=%d ticks=[%d,%d] curTick=%d hooks=%s poolId=%s",
                token_id,
                currency0,
                amount0,
                currency1,
                amount1,
                int(fee),
                int(liquidity),
                int(tick_lower),
                int(tick_upper),
                int(current_tick),
                hooks,
                pool_id.hex(),
            )

            # Convert native ETH (address(0)) to WETH for pricing
            asset0 = self._normalize_currency(currency0)
            asset1 = self._normalize_currency(currency1)

            out: list[AssetData] = []
            if amount0 > 0:
                out.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(asset0),
                        amount=int(amount0),
                    )
                )
            if amount1 > 0:
                out.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(asset1),
                        amount=int(amount1),
                    )
                )
            return out

        except Exception as e:
            logger.error("Failed to process Uniswap V4 position %d: %s", token_id, e)
            return []

    async def fetch_assets(
        self,
        subvault_address: str,
        previous_assets: list[AssetData] | None = None,
    ) -> list[AssetData]:
        if not self.position_manager:
            logger.warning(
                "Uniswap V4: position_manager not configured, cannot fetch positions"
            )
            return previous_assets if previous_assets else []

        # Discover tokenIds via subgraph - MUST succeed or fail the pipeline
        try:
            token_ids = await self._fetch_token_ids_from_subgraph(subvault_address)
        except Exception as e:
            logger.error(
                "Uniswap V4: subgraph tokenId discovery failed for %s: %s",
                subvault_address,
                e,
            )
            raise ValueError(
                f"Uniswap V4 subgraph query failed: {e}. "
                "Check that TQ_ORACLE_GRAPH_API_KEY is set correctly. "
                "Get an API key at https://thegraph.com/studio/"
            ) from e

        if not token_ids:
            logger.debug(
                "Uniswap V4: no positions found for subvault %s", subvault_address
            )
            return previous_assets if previous_assets else []

        logger.info(
            "Uniswap V4: found %d positions (subgraph) for subvault %s",
            len(token_ids),
            subvault_address,
        )
        for tid in token_ids[:10]:
            logger.debug("Uniswap V4: tokenId %d", tid)
        if len(token_ids) > 10:
            logger.debug("Uniswap V4: (showing first 10 tokenIds only)")

        # Process positions
        all_results: list[AssetData] = []
        for token_id in token_ids:
            all_results.extend(await self._process_position(token_id, subvault_address))

        # Aggregate amounts by token address
        aggregated: dict[str, int] = {}
        for asset in all_results:
            addr = asset.asset_address.lower()
            aggregated[addr] = aggregated.get(addr, 0) + int(asset.amount)

        final_results = [
            AssetData(asset_address=Web3.to_checksum_address(addr), amount=amount)
            for addr, amount in aggregated.items()
        ]

        logger.info(
            "Uniswap V4: fetched %d unique tokens from %d positions for subvault %s",
            len(final_results),
            len(token_ids),
            subvault_address,
        )

        if previous_assets:
            return previous_assets + final_results
        return final_results

    async def fetch_all_assets(self) -> list[AssetData]:
        logger.debug("Uniswap V4 adapter does not support global asset fetching")
        return []
