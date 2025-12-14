"""Etherscan Logs API client for fetching event logs."""

from typing import Any, cast

import backoff
import requests
from eth_abi.codec import ABICodec
from web3 import Web3
from web3._utils.events import get_event_data
from web3._utils.filters import construct_event_filter_params
from web3.contract.contract import ContractEvent
from web3.types import EventData

from ..logger import get_logger

logger = get_logger(__name__)

ETHERSCAN_API_V2_URL = "https://api.etherscan.io/v2/api"


class EtherscanRateLimitError(Exception):
    """Raised when Etherscan returns a rate limit error."""


class EtherscanLogsClient:
    """Client for fetching event logs from Etherscan API v2."""

    def __init__(
        self,
        api_key: str,
        chain_id: int,
        *,
        page_size: int = 1000,
        request_timeout: int = 15,
    ):
        self._api_key = api_key
        self._chain_id = chain_id
        self._page_size = min(page_size, 1000)
        self._timeout = request_timeout
        self._session = requests.Session()

    def fetch_logs(
        self,
        event: ContractEvent,
        contract_address: str,
        argument_filters: dict[str, Any],
        from_block: int,
        to_block: int,
    ) -> list[EventData] | None:
        """Fetch and decode event logs from Etherscan.

        Args:
            event: Web3 ContractEvent to fetch logs for
            contract_address: Contract address to query
            argument_filters: Dict mapping indexed field names to filter values
            from_block: Starting block number
            to_block: Ending block number

        Returns:
            List of decoded EventData, or None if the request fails
        """
        abi = event._get_event_abi()
        codec: ABICodec = event.w3.codec
        event_name = abi.get("name", "unknown")

        # Use documented web3 internal API for filter params
        try:
            _, filter_params = construct_event_filter_params(
                abi,
                codec,
                address=Web3.to_checksum_address(contract_address),
                argument_filters=argument_filters,
                from_block=from_block,
                to_block=to_block,
            )
        except Exception as exc:
            logger.warning(
                "Etherscan filter params failed — event=%s error=%s",
                event_name,
                exc,
            )
            return None

        topics: list[Any] = list(filter_params.get("topics") or [])
        if not topics:
            logger.warning(
                "Etherscan query skipped — event=%s has no topics",
                event_name,
            )
            return None

        logs: list[EventData] = []
        page = 1

        while True:
            result = self._call(contract_address, topics, from_block, to_block, page)
            if result is None:
                return None if page == 1 else logs

            for raw in result:
                try:
                    log_entry = self._to_log_entry(raw)
                    logs.append(cast(EventData, get_event_data(codec, abi, log_entry)))
                except Exception:
                    continue

            if len(result) < self._page_size:
                break
            page += 1

        logger.debug(
            "Etherscan query — event=%s filters=%s found=%d",
            abi.get("name", "unknown"),
            argument_filters,
            len(logs),
        )
        return logs

    @backoff.on_exception(
        backoff.expo,
        (requests.RequestException, EtherscanRateLimitError),
        max_time=30,
        jitter=backoff.full_jitter,
    )
    def _call(
        self,
        address: str,
        topics: list[Any],
        from_block: int,
        to_block: int,
        page: int,
    ) -> list[dict[str, Any]] | None:
        """Make a paginated request to Etherscan logs API."""
        params: dict[str, Any] = {
            "chainid": self._chain_id,
            "module": "logs",
            "action": "getLogs",
            "address": address,
            "fromBlock": from_block,
            "toBlock": to_block,
            "page": page,
            "offset": self._page_size,
            "apikey": self._api_key,
        }

        # Add topics - convert bytes to hex string for Etherscan API
        for i, topic in enumerate(topics[:3]):
            if topic is not None:
                params[f"topic{i}"] = topic.hex() if isinstance(topic, bytes) else topic
                if i > 0:
                    params[f"topic0_{i}_opr"] = "and"

        resp = self._session.get(
            ETHERSCAN_API_V2_URL, params=params, timeout=self._timeout
        )
        resp.raise_for_status()
        data = resp.json()

        result = data.get("result", "")
        message = data.get("message", "").lower()

        # Handle string results (errors)
        if isinstance(result, str):
            if "rate limit" in result.lower():
                raise EtherscanRateLimitError(result)
            if "no records" in result.lower():
                return []
            logger.debug("Etherscan returned error — result=%s", result)
            return None

        # Handle list results
        if isinstance(result, list):
            # Empty list with "No records found" message is valid (no events)
            if not result and "no records" in message:
                return []
            # Success - return the results
            if data.get("status") == "1":
                return result
            # Empty list with status=0 but no error message - treat as no records
            if not result:
                return []

        logger.debug(
            "Etherscan unexpected response — status=%s message=%s",
            data.get("status"),
            message,
        )
        return None

    @staticmethod
    def _to_log_entry(raw: dict[str, Any]) -> dict[str, Any]:
        """Convert Etherscan log to web3 log entry format."""
        return {
            "address": raw.get("address"),
            "blockHash": bytes.fromhex(raw.get("blockHash", "0" * 66)[2:]),
            "blockNumber": int(raw.get("blockNumber", "0x0"), 16),
            "data": bytes.fromhex((raw.get("data") or "0x")[2:] or "00"),
            "logIndex": int(raw.get("logIndex", "0x0"), 16),
            "topics": [bytes.fromhex(t[2:]) for t in raw.get("topics", []) if t],
            "transactionHash": bytes.fromhex(raw.get("transactionHash", "0" * 66)[2:]),
            "transactionIndex": int(raw.get("transactionIndex", "0x0"), 16),
            "removed": False,
        }
