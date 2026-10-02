"""配置模块单元测试：环境变量优先级、必填校验、YAML 合并。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.config import Config, ConfigError, load_config, validate_config  # noqa: E402


@pytest.fixture()
def env_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入必填凭据环境变量。"""
    monkeypatch.setenv("ABECLOUD_USERNAME", "13800001111")
    monkeypatch.setenv("ABECLOUD_PASSWORD", "s3cret-pass")


def test_load_config_from_env(env_creds: None, tmp_path: Path) -> None:
    cfg = load_config(base_dir=tmp_path)
    assert cfg.abecloud_username == "13800001111"
    assert cfg.abecloud_password == "s3cret-pass"


def test_missing_credentials_reported(tmp_path: Path,
                                       monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABECLOUD_USERNAME", raising=False)
    monkeypatch.delenv("ABECLOUD_PASSWORD", raising=False)
    cfg = load_config(base_dir=tmp_path)
    valid, errors = validate_config(cfg)
    assert not valid
    assert any("ABECLOUD_USERNAME" in e for e in errors)
    assert any("ABECLOUD_PASSWORD" in e for e in errors)


def test_yaml_overrides_defaults(env_creds: None, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text(
        "abecloud:\n  timeout_ms: 30000\n  target_plan: 免费1核\n"
        "state:\n  heartbeat_commit_hours: 12.0\n",
        encoding="utf-8",
    )
    cfg = load_config(base_dir=tmp_path)
    assert cfg.abecloud.timeout_ms == 30000
    assert cfg.abecloud.target_plan == "免费1核"
    assert cfg.state.heartbeat_commit_hours == 12.0


def test_env_overrides_yaml(env_creds: None, tmp_path: Path,
                             monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "config.yaml").write_text(
        "pushplus:\n  wechat_enabled: false\n", encoding="utf-8")
    monkeypatch.setenv("PUSHPLUS_WECHAT_ENABLED", "true")
    cfg = load_config(base_dir=tmp_path)
    assert cfg.pushplus.wechat_enabled is True


def test_channel_env_flags(env_creds: None, tmp_path: Path,
                           monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUSHPLUS_QQ_ENABLED", "true")
    monkeypatch.setenv("PUSHPLUS_SMS_ENABLED", "1")
    monkeypatch.setenv("PUSHPLUS_WECHAT_ENABLED", "false")
    cfg = load_config(base_dir=tmp_path)
    assert cfg.pushplus.qq_enabled is True
    assert cfg.pushplus.sms_enabled is True
    assert cfg.pushplus.wechat_enabled is False


def test_bad_yaml_top_level(env_creds: None, tmp_path: Path) -> None:
    (tmp_path / "config.yaml").write_text("- just\n- a\n- list\n",
                                          encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(base_dir=tmp_path)


def test_work_window_validation(env_creds: None, tmp_path: Path,
                                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ABECLOUD_USERNAME", raising=False)
    monkeypatch.delenv("ABECLOUD_PASSWORD", raising=False)
    cfg = load_config(base_dir=tmp_path)
    cfg.abecloud.work_hour_start = 22
    cfg.abecloud.work_hour_end = 9
    valid, errors = validate_config(cfg)
    assert not valid
    assert any("work_hour_start" in e for e in errors)
