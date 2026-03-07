"""HyperCore asset adapter for portfolio NAV tracking.

Fetches portfolio value (NAV) from HyperLiquid's API for:
- Native Hyperliquid vaults (vault leader + depositors)
- Sub-accounts managed by master EOAs
- Direct EOA portfolio values

Returns USDC-denominated NAV as AssetData for TVL aggregation.
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

import aiohttp

from tq_oracle.constants import (
    HL_MAX_PORTFOLIO_STALENESS_SECONDS,
    HYPERCORE_MAINNET_API,
    USDC_HYPEREVM_MAINNET,
)

from .base import AssetData, BaseAssetAdapter

if TYPE_CHECKING:
    from tq_oracle.settings import ChainConfig, OracleSettings

logger = logging.getLogger(__name__)

# USDC has 6 decimals, but we normalize to 18 for consistency
USDC_DECIMALS = 6
TARGET_DECIMALS = 18
DECIMAL_MULTIPLIER = 10 ** (TARGET_DECIMALS - USDC_DECIMALS)
USDC_DECIMAL_SCALE = Decimal(10**USDC_DECIMALS)

# HTTP request timeout (seconds)
API_TIMEOUT = aiohttp.ClientTimeout(total=30)


class HyperCoreAdapter(BaseAssetAdapter):
    """Adapter for fetching portfolio NAV from HyperCore.

    This adapter queries the HyperLiquid API to get:
    - Portfolio value history (accountValueHistory)
    - Vault equity for native HL vaults

    The adapter returns a single AssetData with:
    - asset_address: USDC address (since NAV is USDC-denominated)
    - amount: Portfolio NAV in wei (18 decimals)

    Configuration:
        The adapter uses ChainConfig from OracleSettings.chains where
        network="hypercore". It reads:
        - api_url: HyperLiquid API endpoint
        - vaults: List of native HL vaults to track
        - subaccounts: List of sub-accounts to track
    """

    def __init__(
        self,
        config: OracleSettings,
        api_url: str | None = None,
        usdc_address: str | None = None,
        max_staleness_seconds: int = HL_MAX_PORTFOLIO_STALENESS_SECONDS,
    ):
        """Initialize HyperCore adapter.

        Args:
            config: Oracle settings
            api_url: HyperLiquid API URL (default: mainnet)
            usdc_address: USDC address for asset tagging
            max_staleness_seconds: Max age of portfolio data before rejection
        """
        super().__init__(config)

        # Get HyperCore chain config if available
        self._chain_config = self._get_hypercore_chain_config()

        # API configuration
        if api_url:
            self.api_url = api_url
        elif self._chain_config and self._chain_config.api_url:
            self.api_url = self._chain_config.api_url
        else:
            self.api_url = HYPERCORE_MAINNET_API

        # USDC address for tagging the NAV asset
        self.usdc_address = usdc_address or USDC_HYPEREVM_MAINNET
        self.max_staleness_seconds = max_staleness_seconds

        # Shared HTTP session for connection reuse
        self._session = aiohttp.ClientSession(timeout=API_TIMEOUT)

        logger.debug(
            "HyperCore adapter initialized: api_url=%s, max_staleness=%ds",
            self.api_url,
            self.max_staleness_seconds,
        )

    def _get_hypercore_chain_config(self) -> ChainConfig | None:
        """Get HyperCore chain config from OracleSettings."""
        for chain in self.config.chains:
            if chain.network.lower() == "hypercore":
                return chain
        return None

    @property
    def adapter_name(self) -> str:
        return "hypercore"

    async def _fetch_portfolio_nav(self, address: str) -> int:
        """Fetch portfolio NAV for an address from HyperLiquid API.

        Uses the /info endpoint with "portfolio" type to get account value history.

        Args:
            address: The address to query (EOA or sub-account)

        Returns:
            Portfolio NAV in wei (18 decimals)

        Raises:
            ValueError: If portfolio data is stale or invalid
        """
        url = f"{self.api_url}/info"
        payload = {
            "type": "portfolio",
            "user": address,
        }

        async with self._session.post(url, json=payload) as response:
            if response.status != 200:
                raise ValueError(
                    f"HyperCore API error: {response.status} for {address}"
                )
            data = await response.json()

        # Parse portfolio response
        # Format: [("day", {"accountValueHistory": [[timestamp_ms, value_str], ...]}), ...]
        nav = self._parse_portfolio_response(data, address)
        return nav

    def _parse_portfolio_response(self, data: list[Any], address: str) -> int:
        """Parse portfolio API response and extract latest NAV.

        Args:
            data: API response data
            address: Address for logging

        Returns:
            Portfolio NAV in wei (18 decimals)

        Raises:
            ValueError: If data is invalid or stale
        """
        if not data:
            raise ValueError(f"Empty portfolio response for {address}")

        # Find the "day" entry which contains accountValueHistory
        day_data = None
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                if item[0] == "day":
                    day_data = item[1]
                    break

        if not day_data:
            # Try alternate response format (direct dict)
            if isinstance(data, dict) and "accountValueHistory" in data:
                day_data = data
            else:
                raise ValueError(f"No 'day' data in portfolio response for {address}")

        account_history = day_data.get("accountValueHistory", [])
        if not account_history:
            raise ValueError(f"Empty accountValueHistory for {address}")

        # Parse and validate history points
        clean_points: list[tuple[int, Decimal]] = []
        for point in account_history:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            parsed = self._parse_history_point(point[0], point[1])
            if parsed:
                clean_points.append(parsed)

        if not clean_points:
            raise ValueError(f"No valid history points for {address}")

        # Sort by timestamp and get latest
        clean_points.sort(key=lambda p: p[0])
        last_ts_ms, latest_value = clean_points[-1]

        # Check staleness
        now_ms = int(time.time() * 1000)
        age_seconds = (now_ms - last_ts_ms) / 1000

        if age_seconds > self.max_staleness_seconds:
            raise ValueError(
                f"Portfolio data for {address} is stale: {age_seconds:.0f}s old "
                f"(max: {self.max_staleness_seconds}s)"
            )

        # Convert to wei (18 decimals) using Decimal for precision
        # API returns value as string (USDC), we parse with Decimal to avoid float loss
        nav_usdc_6_decimals = int(latest_value * USDC_DECIMAL_SCALE)
        nav_wei = nav_usdc_6_decimals * DECIMAL_MULTIPLIER

        logger.debug(
            "HyperCore %s: NAV=%s USDC (age: %.0fs)",
            address[:10],
            latest_value,
            age_seconds,
        )

        return nav_wei

    def _parse_history_point(
        self, ts: Any, value_str: Any
    ) -> tuple[int, Decimal] | None:
        """Parse a single history point.

        Args:
            ts: Timestamp (ms)
            value_str: Value string

        Returns:
            Tuple of (timestamp_ms, Decimal value) or None if invalid
        """
        try:
            ts_ms = int(ts)
            value = Decimal(str(value_str))
            if value.is_finite() and value >= 0:
                return ts_ms, value
        except (TypeError, ValueError, InvalidOperation) as e:
            logger.debug(f"Skipping invalid history point: {e}")
        return None

    async def _fetch_vault_nav(self, vault_address: str) -> int:
        """Fetch NAV for a native Hyperliquid vault.

        Uses the /info endpoint with "vaultDetails" type.

        Args:
            vault_address: The vault address

        Returns:
            Vault equity in wei (18 decimals)

        Raises:
            ValueError: If vault data is invalid or stale
        """
        url = f"{self.api_url}/info"
        payload = {
            "type": "vaultDetails",
            "vaultAddress": vault_address,
        }

        async with self._session.post(url, json=payload) as response:
            if response.status != 200:
                raise ValueError(
                    f"HyperCore vault API error: {response.status} for {vault_address}"
                )
            data = await response.json()

        # Parse vault details
        if not data:
            raise ValueError(f"Empty vault response for {vault_address}")

        # Get total equity from vault details
        portfolio = data.get("portfolio", {})
        if not portfolio:
            # Try clearinghouseState for equity
            clearinghouse = data.get("clearinghouseState", {})
            margin_summary = clearinghouse.get("marginSummary", {})
            equity_str = margin_summary.get("accountValue", "0")
        else:
            # Vault has portfolio data
            equity_str = portfolio.get("equity", "0")

        # Check staleness via lastUpdateTime if available
        last_update_ms = data.get("lastUpdateTime")
        if last_update_ms is not None:
            now_ms = int(time.time() * 1000)
            age_seconds = (now_ms - int(last_update_ms)) / 1000
            if age_seconds > self.max_staleness_seconds:
                raise ValueError(
                    f"Vault data for {vault_address} is stale: {age_seconds:.0f}s old "
                    f"(max: {self.max_staleness_seconds}s)"
                )

        try:
            equity = Decimal(str(equity_str))
        except (TypeError, ValueError, InvalidOperation):
            raise ValueError(f"Invalid equity value for vault {vault_address}: {equity_str}")

        if equity < 0:
            raise ValueError(f"Negative equity for vault {vault_address}: {equity}")

        # Convert to wei using Decimal for precision
        nav_usdc_6_decimals = int(equity * USDC_DECIMAL_SCALE)
        nav_wei = nav_usdc_6_decimals * DECIMAL_MULTIPLIER

        logger.info(
            "HyperCore vault %s: equity=%s USDC",
            vault_address[:10],
            equity,
        )

        return nav_wei

    async def fetch_assets(
        self,
        subvault_address: str,
        previous_assets: list[AssetData] | None = None,
    ) -> list[AssetData]:
        """Fetch portfolio NAV for an address on HyperCore.

        Args:
            subvault_address: Address to query NAV for
            previous_assets: Not used (no asset chaining for HyperCore)

        Returns:
            List with single AssetData containing NAV in USDC
        """
        try:
            nav_wei = await self._fetch_portfolio_nav(subvault_address)

            if nav_wei == 0:
                logger.debug("HyperCore %s: zero NAV", subvault_address[:10])
                return []

            logger.info(
                "HyperCore %s: NAV=%d wei (%.2f USDC)",
                subvault_address[:10],
                nav_wei,
                nav_wei / (10**TARGET_DECIMALS),
            )

            return [
                AssetData(
                    asset_address=self.usdc_address,
                    amount=nav_wei,
                )
            ]

        except Exception as e:
            logger.error("HyperCore fetch failed for %s: %s", subvault_address, e)
            raise

    async def fetch_all_assets(self) -> list[AssetData]:
        """Fetch NAV for all configured vaults and sub-accounts.

        Reads from ChainConfig.vaults and ChainConfig.subaccounts to get
        the list of addresses to query.

        Returns:
            List of AssetData for all configured HyperCore positions
        """
        if not self._chain_config:
            logger.debug("No HyperCore chain config, skipping fetch_all_assets")
            return []

        all_assets: list[AssetData] = []

        # Fetch native vault NAVs
        for vault_config in self._chain_config.vaults:
            nav_wei = await self._fetch_vault_nav(vault_config.vault_address)
            if nav_wei > 0:
                all_assets.append(
                    AssetData(
                        asset_address=self.usdc_address,
                        amount=nav_wei,
                    )
                )
                name = vault_config.name or vault_config.vault_address[:10]
                logger.info(
                    "HyperCore vault '%s': NAV=%.2f USDC",
                    name,
                    nav_wei / (10**TARGET_DECIMALS),
                )

        # Fetch sub-account NAVs
        for subaccount_config in self._chain_config.subaccounts:
            nav_wei = await self._fetch_portfolio_nav(
                subaccount_config.master_address
            )
            if nav_wei > 0:
                all_assets.append(
                    AssetData(
                        asset_address=self.usdc_address,
                        amount=nav_wei,
                    )
                )
                logger.info(
                    "HyperCore sub-account %s: NAV=%.2f USDC",
                    subaccount_config.master_address[:10],
                    nav_wei / (10**TARGET_DECIMALS),
                )

        # Also fetch for subvault addresses in chain config
        for subvault in self._chain_config.subvault_addresses:
            nav_wei = await self._fetch_portfolio_nav(subvault)
            if nav_wei > 0:
                all_assets.append(
                    AssetData(
                        asset_address=self.usdc_address,
                        amount=nav_wei,
                    )
                )

        logger.info(
            "HyperCore: fetched %d positions, total NAV=%.2f USDC",
            len(all_assets),
            sum(a.amount for a in all_assets) / (10**TARGET_DECIMALS),
        )

        return all_assets
