"""Uniswap V3 LP position adapter.

This adapter handles Uniswap V3 NFT positions by:
1. Enumerating all position NFT IDs owned by a subvault
2. Fetching position details (token0, token1, liquidity, fee tier)
3. Calculating withdrawable amounts for both tokens
4. Returning both token amounts for pricing pipeline
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import backoff
from web3 import Web3
from web3.exceptions import ProviderConnectionError

from ....logger import get_logger
from ..base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from ....settings import OracleSettings

logger = get_logger(__name__)


class UniswapV3Adapter(BaseAssetAdapter):
    """
    Adapter for Uniswap V3 NFT positions.

    This adapter:
    - Enumerates position NFT IDs owned by subvault (via ERC721Enumerable)
    - Queries position details for each NFT
    - Calculates token0 and token1 amounts withdrawable
    - Returns both tokens for pricing pipeline

    Configuration via TOML:
    [adapters.uniswap_v3]
    position_manager = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"  # Mainnet default

    [[subvault_adapters]]
    subvault_address = "0x..."
    additional_adapters = ["uniswap_v3"]
    """

    # Mainnet NonfungiblePositionManager address
    DEFAULT_POSITION_MANAGER = "0xC36442b4a4522E871399CD717aBDD847Ab11FE88"

    # Minimal ABI for what we need
    _POSITION_MANAGER_ABI = [
        # ERC721Enumerable interface
        {
            "inputs": [{"internalType": "address", "name": "owner", "type": "address"}],
            "name": "balanceOf",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        },
        {
            "inputs": [
                {"internalType": "address", "name": "owner", "type": "address"},
                {"internalType": "uint256", "name": "index", "type": "uint256"},
            ],
            "name": "tokenOfOwnerByIndex",
            "outputs": [{"internalType": "uint256", "name": "", "type": "uint256"}],
            "stateMutability": "view",
            "type": "function",
        },
        # Position details
        {
            "inputs": [
                {"internalType": "uint256", "name": "tokenId", "type": "uint256"}
            ],
            "name": "positions",
            "outputs": [
                {"internalType": "uint96", "name": "nonce", "type": "uint96"},
                {"internalType": "address", "name": "operator", "type": "address"},
                {"internalType": "address", "name": "token0", "type": "address"},
                {"internalType": "address", "name": "token1", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
                {"internalType": "int24", "name": "tickLower", "type": "int24"},
                {"internalType": "int24", "name": "tickUpper", "type": "int24"},
                {"internalType": "uint128", "name": "liquidity", "type": "uint128"},
                {
                    "internalType": "uint256",
                    "name": "feeGrowthInside0LastX128",
                    "type": "uint256",
                },
                {
                    "internalType": "uint256",
                    "name": "feeGrowthInside1LastX128",
                    "type": "uint256",
                },
                {"internalType": "uint128", "name": "tokensOwed0", "type": "uint128"},
                {"internalType": "uint128", "name": "tokensOwed1", "type": "uint128"},
            ],
            "stateMutability": "view",
            "type": "function",
        },
        # Decrease liquidity simulation (used to calculate amounts)
        {
            "inputs": [
                {
                    "components": [
                        {
                            "internalType": "uint256",
                            "name": "tokenId",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint128",
                            "name": "liquidity",
                            "type": "uint128",
                        },
                        {
                            "internalType": "uint256",
                            "name": "amount0Min",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "amount1Min",
                            "type": "uint256",
                        },
                        {
                            "internalType": "uint256",
                            "name": "deadline",
                            "type": "uint256",
                        },
                    ],
                    "internalType": "struct INonfungiblePositionManager.DecreaseLiquidityParams",
                    "name": "params",
                    "type": "tuple",
                }
            ],
            "name": "decreaseLiquidity",
            "outputs": [
                {"internalType": "uint256", "name": "amount0", "type": "uint256"},
                {"internalType": "uint256", "name": "amount1", "type": "uint256"},
            ],
            "stateMutability": "payable",
            "type": "function",
        },
    ]

    # Uniswap V3 Pool ABI - for getting current tick and liquidity
    _POOL_ABI = [
        {
            "inputs": [],
            "name": "slot0",
            "outputs": [
                {"internalType": "uint160", "name": "sqrtPriceX96", "type": "uint160"},
                {"internalType": "int24", "name": "tick", "type": "int24"},
                {
                    "internalType": "uint16",
                    "name": "observationIndex",
                    "type": "uint16",
                },
                {
                    "internalType": "uint16",
                    "name": "observationCardinality",
                    "type": "uint16",
                },
                {
                    "internalType": "uint16",
                    "name": "observationCardinalityNext",
                    "type": "uint16",
                },
                {"internalType": "uint8", "name": "feeProtocol", "type": "uint8"},
                {"internalType": "bool", "name": "unlocked", "type": "bool"},
            ],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    # Uniswap V3 Factory ABI - for getting pool address
    _FACTORY_ABI = [
        {
            "inputs": [
                {"internalType": "address", "name": "tokenA", "type": "address"},
                {"internalType": "address", "name": "tokenB", "type": "address"},
                {"internalType": "uint24", "name": "fee", "type": "uint24"},
            ],
            "name": "getPool",
            "outputs": [{"internalType": "address", "name": "pool", "type": "address"}],
            "stateMutability": "view",
            "type": "function",
        },
    ]

    FACTORY_ADDRESS = "0x1F98431c8aD98523631AE4a59f267346ea31F984"  # Mainnet

    def __init__(self, config: OracleSettings, **overrides):
        """Initialize Uniswap V3 adapter.

        Args:
            config: Oracle settings
            **overrides: Optional overrides
                - position_manager: Custom position manager address
        """
        super().__init__(config)

        # Initialize Web3
        self.w3 = Web3(Web3.HTTPProvider(config.vault_rpc_required))
        if not self.w3.is_connected():
            raise ConnectionError("Failed to connect to RPC for Uniswap V3 adapter")

        self.block_number = config.block_number_required

        # RPC throttling
        self._rpc_sem = asyncio.Semaphore(config.rpc_max_concurrent_calls)
        self._rpc_delay = config.rpc_delay
        self._rpc_jitter = config.rpc_jitter
        self._rpc_timeout = config.rpc_timeout

        # Load configuration
        adapter_config = config.adapters.uniswap_v3

        # Position manager address
        if "position_manager" in overrides:
            self.position_manager = overrides["position_manager"]
        elif adapter_config.position_manager:
            self.position_manager = adapter_config.position_manager
        else:
            self.position_manager = self.DEFAULT_POSITION_MANAGER

        logger.debug(
            "Uniswap V3 adapter initialized with position manager: %s",
            self.position_manager,
        )

    @property
    def adapter_name(self) -> str:
        """Return adapter identifier."""
        return "uniswap_v3"

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

    async def _get_position_count(self, owner: str) -> int:
        """Get number of position NFTs owned by address.

        Args:
            owner: Owner address

        Returns:
            Number of positions owned
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.position_manager),
            abi=self._POSITION_MANAGER_ABI,
        )
        balance = await self._rpc(
            contract.functions.balanceOf(Web3.to_checksum_address(owner)).call,
            block_identifier=self.block_number,
        )
        return int(balance)

    async def _get_position_id_by_index(self, owner: str, index: int) -> int:
        """Get position NFT ID by index.

        Args:
            owner: Owner address
            index: Index in owner's position array

        Returns:
            Position NFT ID (token ID)
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.position_manager),
            abi=self._POSITION_MANAGER_ABI,
        )
        token_id = await self._rpc(
            contract.functions.tokenOfOwnerByIndex(
                Web3.to_checksum_address(owner), index
            ).call,
            block_identifier=self.block_number,
        )
        return int(token_id)

    async def _get_position_details(self, token_id: int) -> dict:
        """Get position details from NFT ID.

        Args:
            token_id: Position NFT ID

        Returns:
            Dict with position details
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.position_manager),
            abi=self._POSITION_MANAGER_ABI,
        )
        (
            nonce,
            operator,
            token0,
            token1,
            fee,
            tick_lower,
            tick_upper,
            liquidity,
            fee_growth_inside_0_last_x128,
            fee_growth_inside_1_last_x128,
            tokens_owed_0,
            tokens_owed_1,
        ) = await self._rpc(
            contract.functions.positions(token_id).call,
            block_identifier=self.block_number,
        )

        return {
            "nonce": nonce,
            "operator": operator,
            "token0": token0,
            "token1": token1,
            "fee": fee,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "liquidity": liquidity,
            "fee_growth_inside_0_last_x128": fee_growth_inside_0_last_x128,
            "fee_growth_inside_1_last_x128": fee_growth_inside_1_last_x128,
            "tokens_owed_0": tokens_owed_0,
            "tokens_owed_1": tokens_owed_1,
        }

    async def _get_pool_address(self, token0: str, token1: str, fee: int) -> str:
        """Get pool address from factory.

        Args:
            token0: Token0 address
            token1: Token1 address
            fee: Fee tier

        Returns:
            Pool address
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(self.FACTORY_ADDRESS),
            abi=self._FACTORY_ABI,
        )
        pool = await self._rpc(
            contract.functions.getPool(
                Web3.to_checksum_address(token0),
                Web3.to_checksum_address(token1),
                fee,
            ).call,
            block_identifier=self.block_number,
        )
        return str(pool)

    async def _get_pool_slot0(self, pool_address: str) -> tuple[int, int]:
        """Get current pool state (sqrtPriceX96 and tick).

        Args:
            pool_address: Pool address

        Returns:
            Tuple of (sqrtPriceX96, tick)
        """
        contract = self.w3.eth.contract(
            address=Web3.to_checksum_address(pool_address),
            abi=self._POOL_ABI,
        )
        slot0 = await self._rpc(
            contract.functions.slot0().call,
            block_identifier=self.block_number,
        )
        sqrt_price_x96 = int(slot0[0])
        tick = int(slot0[1])
        return sqrt_price_x96, tick

    def _calculate_amounts_from_liquidity(
        self,
        liquidity: int,
        sqrt_price_x96: int,
        tick_lower: int,
        tick_upper: int,
        current_tick: int,
    ) -> tuple[int, int]:
        """Calculate token amounts from liquidity.

        This uses the Uniswap V3 math to calculate how much of each token
        would be received if the position was withdrawn.

        Args:
            liquidity: Position liquidity
            sqrt_price_x96: Current pool sqrt price
            tick_lower: Position lower tick
            tick_upper: Position upper tick
            current_tick: Current pool tick

        Returns:
            Tuple of (amount0, amount1)
        """
        if liquidity == 0:
            return 0, 0

        # Calculate sqrt prices at tick bounds
        sqrt_price_lower_x96 = self._get_sqrt_ratio_at_tick(tick_lower)
        sqrt_price_upper_x96 = self._get_sqrt_ratio_at_tick(tick_upper)

        # Position is entirely in token1
        if current_tick < tick_lower:
            amount0 = self._get_amount0_delta(
                sqrt_price_lower_x96, sqrt_price_upper_x96, liquidity
            )
            amount1 = 0

        # Position is entirely in token0
        elif current_tick >= tick_upper:
            amount0 = 0
            amount1 = self._get_amount1_delta(
                sqrt_price_lower_x96, sqrt_price_upper_x96, liquidity
            )

        # Position is active (contains both tokens)
        else:
            amount0 = self._get_amount0_delta(
                sqrt_price_x96, sqrt_price_upper_x96, liquidity
            )
            amount1 = self._get_amount1_delta(
                sqrt_price_lower_x96, sqrt_price_x96, liquidity
            )

        return amount0, amount1

    def _get_sqrt_ratio_at_tick(self, tick: int) -> int:
        """Calculate sqrt price at tick.

        Uses Uniswap V3 tick math: price = 1.0001^tick

        Args:
            tick: Tick value

        Returns:
            Sqrt price in X96 format
        """
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

        # Round up to ensure amount0 >= floor(amount0)
        return (ratio >> 32) + (1 if ratio % (1 << 32) > 0 else 0)

    def _get_amount0_delta(
        self, sqrt_price_a_x96: int, sqrt_price_b_x96: int, liquidity: int
    ) -> int:
        """Calculate amount0 delta.

        Args:
            sqrt_price_a_x96: First sqrt price
            sqrt_price_b_x96: Second sqrt price
            liquidity: Liquidity amount

        Returns:
            Amount of token0
        """
        if sqrt_price_a_x96 > sqrt_price_b_x96:
            sqrt_price_a_x96, sqrt_price_b_x96 = sqrt_price_b_x96, sqrt_price_a_x96

        numerator1 = liquidity << 96
        numerator2 = sqrt_price_b_x96 - sqrt_price_a_x96

        return (numerator1 * numerator2) // sqrt_price_b_x96 // sqrt_price_a_x96

    def _get_amount1_delta(
        self, sqrt_price_a_x96: int, sqrt_price_b_x96: int, liquidity: int
    ) -> int:
        """Calculate amount1 delta.

        Args:
            sqrt_price_a_x96: First sqrt price
            sqrt_price_b_x96: Second sqrt price
            liquidity: Liquidity amount

        Returns:
            Amount of token1
        """
        if sqrt_price_a_x96 > sqrt_price_b_x96:
            sqrt_price_a_x96, sqrt_price_b_x96 = sqrt_price_b_x96, sqrt_price_a_x96

        return (liquidity * (sqrt_price_b_x96 - sqrt_price_a_x96)) >> 96

    async def _process_position(
        self, token_id: int, subvault_address: str
    ) -> list[AssetData]:
        """Process a single Uniswap V3 position.

        Args:
            token_id: Position NFT ID
            subvault_address: Subvault address

        Returns:
            List of AssetData for both tokens
        """
        try:
            # Get position details
            position = await self._get_position_details(token_id)

            if position["liquidity"] == 0:
                logger.debug(
                    "Uniswap V3 position %d: zero liquidity (possibly closed)", token_id
                )
                return []

            # Get pool address
            pool_address = await self._get_pool_address(
                position["token0"], position["token1"], position["fee"]
            )

            # Get current pool state
            sqrt_price_x96, current_tick = await self._get_pool_slot0(pool_address)

            # Calculate token amounts
            amount0, amount1 = self._calculate_amounts_from_liquidity(
                position["liquidity"],
                sqrt_price_x96,
                position["tick_lower"],
                position["tick_upper"],
                current_tick,
            )

            # Add uncollected fees
            amount0 += position["tokens_owed_0"]
            amount1 += position["tokens_owed_1"]

            logger.info(
                "Uniswap V3 position %d: token0=%s amount0=%d, token1=%s amount1=%d, "
                "fee=%d, liquidity=%d, tick_range=[%d, %d], current_tick=%d",
                token_id,
                position["token0"],
                amount0,
                position["token1"],
                amount1,
                position["fee"],
                position["liquidity"],
                position["tick_lower"],
                position["tick_upper"],
                current_tick,
            )

            results = []
            if amount0 > 0:
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(position["token0"]),
                        amount=amount0,
                    )
                )
            if amount1 > 0:
                results.append(
                    AssetData(
                        asset_address=Web3.to_checksum_address(position["token1"]),
                        amount=amount1,
                    )
                )

            return results

        except Exception as e:
            logger.error("Failed to process Uniswap V3 position %d: %s", token_id, e)
            return []

    async def fetch_assets(
        self, subvault_address: str, previous_assets: list[AssetData] | None = None
    ) -> list[AssetData]:
        """Fetch Uniswap V3 positions for a subvault.

        This method:
        1. Enumerates all position NFT IDs owned by subvault
        2. Fetches position details for each NFT
        3. Calculates withdrawable token0 and token1 amounts
        4. Returns both tokens for pricing pipeline

        Args:
            subvault_address: Subvault address to query

        Returns:
            List of AssetData with token amounts
        """
        # Get position count
        position_count = await self._get_position_count(subvault_address)

        if position_count == 0:
            logger.debug(
                "Uniswap V3: no positions found for subvault %s", subvault_address
            )
            # Pass through previous assets from adapter chain even if we have no positions
            return previous_assets if previous_assets else []

        logger.info(
            "Uniswap V3: found %d positions for subvault %s",
            position_count,
            subvault_address,
        )

        # Enumerate all position IDs
        position_ids = []
        for i in range(position_count):
            try:
                token_id = await self._get_position_id_by_index(subvault_address, i)
                position_ids.append(token_id)
                logger.debug("Uniswap V3: position index %d = token ID %d", i, token_id)
            except Exception as e:
                logger.error(
                    "Failed to get position ID at index %d for %s: %s",
                    i,
                    subvault_address,
                    e,
                )

        # Process each position
        all_results: list[AssetData] = []
        for token_id in position_ids:
            results = await self._process_position(token_id, subvault_address)
            all_results.extend(results)

        # Aggregate amounts by token address
        aggregated: dict[str, int] = {}
        for asset in all_results:
            addr = asset.asset_address.lower()
            aggregated[addr] = aggregated.get(addr, 0) + asset.amount

        final_results = [
            AssetData(asset_address=Web3.to_checksum_address(addr), amount=amount)
            for addr, amount in aggregated.items()
        ]

        logger.info(
            "Uniswap V3: fetched %d unique tokens from %d positions for subvault %s",
            len(final_results),
            len(position_ids),
            subvault_address,
        )

        # Pass through previous assets from adapter chain (non-conversion adapter)
        if previous_assets:
            return previous_assets + final_results
        return final_results

    async def fetch_all_assets(self) -> list[AssetData]:
        """Global asset fetching not supported for Uniswap V3.

        Uniswap V3 adapter works per-subvault only.

        Returns:
            Empty list
        """
        logger.debug("Uniswap V3 adapter does not support global asset fetching")
        return []
