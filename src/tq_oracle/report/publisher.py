from __future__ import annotations

import asyncio
import json
import logging

import requests
from eth.constants import ZERO_ADDRESS
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_typing import URI
from safe_eth.eth import EthereumClient, EthereumNetwork
from safe_eth.safe.api import TransactionServiceApi
from safe_eth.safe.safe_tx import SafeTx
from web3 import Web3

from ..settings import OracleSettings
from .encoder import encode_submit_reports
from .generator import OracleReport

logger = logging.getLogger(__name__)


async def publish_to_stdout(
    report: OracleReport, oracle_address: str, indent: bool
) -> None:
    """Publish report to stdout (dry run mode).

    Args:
        report: The oracle report to publish
        oracle_address: The address of the oracle contract
        indent: Whether or not to indent report output

    This corresponds to the "Report published to stdout" step in the flowchart.
    """
    (_, encoded_calldata) = encode_submit_reports(
        oracle_address=oracle_address,
        report=report,
    )
    data = {
        "report": report.to_dict(),
        "encoded_calldata": encoded_calldata.hex(),
    }
    print(json.dumps(data, indent=2 if indent else None))


async def build_transaction(
    config: OracleSettings,
    report: OracleReport,
) -> dict[str, str | bytes | int]:
    """Build a submitReports() transaction.

    Args:
        config: CLI configuration
        report: The oracle report to submit

    Returns:
        Transaction data ready to be sent

    This corresponds to the "submitReports() txn built" step in the flowchart.
    """
    to_address, calldata = encode_submit_reports(
        oracle_address=config.oracle_address,
        report=report,
    )
    logger.debug("Built submitReports() transaction to: %s", to_address)
    logger.debug("Encoded calldata: %s", calldata.hex())

    return {
        "to": to_address,
        "data": calldata,
        "value": 0,
        "operation": 0,  # CALL
    }


async def send_to_safe(
    config: OracleSettings,
    transaction: dict[str, str | bytes | int],
) -> str:
    """Send transaction to Gnosis Safe for signing.

    Args:
        config: CLI configuration with Safe details
        transaction: The transaction to send

    Returns:
        Safe UI URL for transaction approval

    This corresponds to the "send txn to Safe" and "txn appears on Safe for signing" steps.

    Raises:
        ValueError: If safe_address or private_key is not configured
    """
    if not config.safe_address:
        raise ValueError("safe_address required for Broadcast mode")

    if not config.private_key:
        raise ValueError("private_key required for Broadcast mode")

    private_key_str = config.private_key.get_secret_value()

    account: LocalAccount = Account.from_key(private_key_str)  # pyrefly: ignore
    logger.info("Proposing transaction as: %s", account.address)

    network = EthereumNetwork(config.chain_id)
    ethereum_client = EthereumClient(URI(config.vault_rpc_required))

    api_key = (
        config.safe_txn_srvc_api_key.get_secret_value()
        if config.safe_txn_srvc_api_key
        else None
    )
    tx_service = TransactionServiceApi(
        network, ethereum_client, api_key=api_key, request_timeout=10
    )

    safe_checksum = Web3.to_checksum_address(config.safe_address)

    safe_api_url = f"{tx_service.base_url}/api/v1/safes/{safe_checksum}/"
    logger.debug("Fetching Safe info from: %s", safe_api_url)
    safe_info_response = await asyncio.to_thread(
        requests.get, safe_api_url, timeout=10.0
    )
    safe_info_response.raise_for_status()
    safe_info_data = safe_info_response.json()
    nonce = int(safe_info_data.get("nonce", 0))

    logger.info("Building transaction for Safe: %s (nonce: %d)", safe_checksum, nonce)

    to_addr = transaction["to"]
    data = transaction["data"]
    value = transaction.get("value", 0)
    operation = transaction.get("operation", 0)

    assert isinstance(to_addr, str), "to must be string"
    assert isinstance(data, bytes), "data must be bytes"
    assert isinstance(value, int), "value must be int"
    assert isinstance(operation, int), "operation must be int"

    if config.safe_txn_srvc_api_key is None:
        logger.debug(
            "No Transaction Service API key configured; waiting for 2s to avoid rate limits"
        )
        await asyncio.sleep(2)

    safe_tx = SafeTx(
        ethereum_client=ethereum_client,
        safe_address=safe_checksum,
        to=Web3.to_checksum_address(to_addr),
        value=value,
        data=data,
        operation=operation,
        safe_tx_gas=0,
        base_gas=0,
        gas_price=0,
        gas_token=Web3.to_checksum_address(ZERO_ADDRESS),
        refund_receiver=Web3.to_checksum_address(ZERO_ADDRESS),
        safe_nonce=nonce,
        safe_version="1.3.0",
        chain_id=config.chain_id,
    )

    safe_tx.sign(private_key_str)

    await asyncio.to_thread(tx_service.post_transaction, safe_tx)

    safe_tx_hash = safe_tx.safe_tx_hash.hex()
    logger.info("Transaction proposed: %s", safe_tx_hash)

    network_prefix = {
        1: "eth",
        11155111: "sep",
        100: "gno",
        137: "matic",
        8453: "base",
        42161: "arb1",
        10: "oeth",
    }.get(config.chain_id, "eth")

    ui_url = (
        f"https://app.safe.global/transactions/queue"
        f"?safe={network_prefix}:{config.safe_address}"
        f"#{safe_tx_hash}"
    )

    return ui_url


async def publish_report(
    config: OracleSettings,
    report: OracleReport,
) -> None:
    """Publish the oracle report based on configuration.

    Args:
        config: CLI configuration (determines dry run vs real submission)
        report: The oracle report to publish

    This handles the branching logic in the flowchart:
    - If dry_run: publish to stdout
    - If not dry_run and Broadcast mode: build transaction, send to Safe
    """
    if config.dry_run:
        await publish_to_stdout(
            report, config.oracle_address, config.dry_run_report_indent
        )
        return

    if config.is_broadcast:
        try:
            transaction = await build_transaction(config, report)
            safe_url = await send_to_safe(config, transaction)
            logger.info("\nTransaction proposed to Safe")
            logger.info(f"Approve here: {safe_url}")
            return
        except ValueError as e:
            logger.error(f"\n❌ Error: {e}")
            raise SystemExit(1) from e

    raise ValueError(
        "Either dry_run must be True or safe_address must be set for Broadcast mode"
    )
