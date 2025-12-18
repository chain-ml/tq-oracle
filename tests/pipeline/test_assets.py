import logging

import pytest

from tq_oracle.adapters.asset_adapters.base import AssetData
from tq_oracle.pipeline.assets import _process_adapter_results, _sanitize_adapter_kwargs


def test_sanitize_adapter_kwargs_drops_empty_collections():
    values = {
        "empty_dict": {},
        "empty_list": [],
        "none_value": None,
        "false_flag": False,
        "zero_value": 0,
        "address": "0x123",
    }

    sanitized = _sanitize_adapter_kwargs(values)

    assert sanitized == {
        "false_flag": False,
        "zero_value": 0,
        "address": "0x123",
    }


def test_process_adapter_results_raises_on_adapter_failure():
    log = logging.getLogger("test")
    tasks_info = [
        ("idle_balances", None),
        ("stakewise", None),
    ]
    results = (
        [AssetData(asset_address="0xToken1", amount=100)],
        ConnectionError("RPC connection failed"),
    )
    asset_data = []

    with pytest.raises(
        ValueError,
        match=r"(?s)Failed to collect assets from 1 adapter\(s\):.*stakewise: RPC connection failed",
    ):
        _process_adapter_results(tasks_info, results, asset_data, log)


def test_process_adapter_results_raises_on_multiple_failures():
    log = logging.getLogger("test")
    tasks_info = [
        ("idle_balances", None),
        ("stakewise", None),
        ("custom_adapter", None),
    ]
    results = (
        ConnectionError("RPC connection failed"),
        [AssetData(asset_address="0xToken1", amount=100)],
        ValueError("Invalid config"),
    )
    asset_data = []

    with pytest.raises(
        ValueError,
        match=r"(?s)Failed to collect assets from 2 adapter\(s\):.*idle_balances:.*custom_adapter:",
    ):
        _process_adapter_results(tasks_info, results, asset_data, log)


def test_process_adapter_results_succeeds_when_all_adapters_work():
    log = logging.getLogger("test")
    tasks_info = [
        ("idle_balances", None),
        ("stakewise", None),
    ]
    results = (
        [AssetData(asset_address="0xToken1", amount=100)],
        [AssetData(asset_address="0xToken2", amount=200)],
    )
    asset_data = []

    _process_adapter_results(tasks_info, results, asset_data, log)

    assert len(asset_data) == 2
    assert asset_data[0][0].asset_address == "0xToken1"
    assert asset_data[1][0].asset_address == "0xToken2"


def test_process_adapter_results_includes_subvault_in_error():
    log = logging.getLogger("test")
    tasks_info = [
        ("0xSubvault1", None, "stakewise"),
        ("0xSubvault2", None, "custom"),
    ]
    results = (
        [AssetData(asset_address="0xToken1", amount=100)],
        ValueError("Adapter error"),
    )
    asset_data = []

    with pytest.raises(ValueError, match=r"custom \(subvault 0xSubvault2\)"):
        _process_adapter_results(tasks_info, results, asset_data, log)
