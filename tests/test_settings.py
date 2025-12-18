"""Tests for settings configuration loading."""

from __future__ import annotations

from textwrap import dedent

from tq_oracle.settings import OracleSettings


def test_promotes_root_flags_from_subvault_block(tmp_path, monkeypatch):
    """Ensure root-level flags defined after subvault adapters are respected."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        dedent(
            """
            vault_address = "0x123"
            vault_rpc = "https://rpc.example"
            dry_run = true

            [[subvault_adapters]]
            subvault_address = "0xabc"
            additional_adapters = ["idle_balances"]

            ignore_timeout_check = true
            ignore_empty_vault = true
            ignore_active_proposal_check = true
            pre_check_retries = 7
            pre_check_timeout = 42.5
            max_calls = 9
            rpc_max_concurrent_calls = 4
            rpc_delay = 0.33
            rpc_jitter = 0.21
            """
        ).strip()
    )

    monkeypatch.setenv("TQ_ORACLE_CONFIG", str(config_path))

    settings = OracleSettings()

    assert settings.ignore_timeout_check is True
    assert settings.ignore_empty_vault is True
    assert settings.ignore_active_proposal_check is True
    assert settings.pre_check_retries == 7
    assert settings.pre_check_timeout == 42.5
    assert settings.max_calls == 9
    assert settings.rpc_max_concurrent_calls == 4
    assert settings.rpc_delay == 0.33
    assert settings.rpc_jitter == 0.21

    assert settings.subvault_adapters == [
        {
            "subvault_address": "0xabc",
            "additional_adapters": ["idle_balances"],
        }
    ]


def test_idle_balances_extra_tokens_loaded(tmp_path, monkeypatch):
    """Ensure idle balance extra token addresses load from config."""

    config_path = tmp_path / "config.toml"
    config_path.write_text(
        dedent(
            """
            [adapters.idle_balances]
            extra_tokens = { osETH = "0xf1C9acDc66974dFB6dEcB12aA385b9cD01190E38" }
            """
        ).strip()
    )

    monkeypatch.setenv("TQ_ORACLE_CONFIG", str(config_path))

    settings = OracleSettings()

    assert settings.adapters.idle_balances.extra_tokens == {
        "osETH": "0xf1C9acDc66974dFB6dEcB12aA385b9cD01190E38"
    }


def test_stakewise_adapter_defaults_loaded(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        dedent(
            """
            [adapters.stakewise]
            stakewise_vault_addresses = ["0x1111111111111111111111111111111111111111"]
            stakewise_exit_queue_start_block = 123
            stakewise_exit_max_lookback_blocks = 50000
            extra_addresses = ["0x2222222222222222222222222222222222222222"]
            skip_exit_queue_scan = true
            """
        ).strip()
    )

    monkeypatch.setenv("TQ_ORACLE_CONFIG", str(config_path))

    settings = OracleSettings()

    assert settings.adapters.stakewise.stakewise_vault_addresses == [
        "0x1111111111111111111111111111111111111111"
    ]
    assert settings.adapters.stakewise.stakewise_exit_queue_start_block == 123
    assert settings.adapters.stakewise.stakewise_exit_max_lookback_blocks == 50000
    assert settings.adapters.stakewise.extra_addresses == [
        "0x2222222222222222222222222222222222222222"
    ]
    assert settings.adapters.stakewise.skip_exit_queue_scan is True


def test_additional_asset_support_toggle(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        dedent(
            """
            additional_asset_support = false
            """
        ).strip()
    )

    monkeypatch.setenv("TQ_ORACLE_CONFIG", str(config_path))

    settings = OracleSettings()

    assert settings.additional_asset_support is False


def test_get_dangerous_options_empty_by_default():
    """No dangerous options enabled by default."""
    settings = OracleSettings()
    assert settings.get_dangerous_options() == []


def test_get_dangerous_options_skip_extra_address_validation(tmp_path, monkeypatch):
    """Detects skip_extra_address_validation as dangerous."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        dedent(
            """
            [adapters.idle_balances]
            skip_extra_address_validation = true
            """
        ).strip()
    )
    monkeypatch.setenv("TQ_ORACLE_CONFIG", str(config_path))

    settings = OracleSettings()

    dangerous = settings.get_dangerous_options()
    assert dangerous == ["adapters.idle_balances.skip_extra_address_validation"]


def test_get_dangerous_options_skip_subvault_existence_check(tmp_path, monkeypatch):
    """Detects skip_subvault_existence_check as dangerous."""
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        dedent(
            """
            [[subvault_adapters]]
            subvault_address = "0xABC"
            skip_subvault_existence_check = true
            """
        ).strip()
    )
    monkeypatch.setenv("TQ_ORACLE_CONFIG", str(config_path))

    settings = OracleSettings()

    dangerous = settings.get_dangerous_options()
    assert dangerous == ["subvault_adapters[0xABC].skip_subvault_existence_check"]
