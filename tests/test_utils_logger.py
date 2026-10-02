"""工具与日志模块单元测试：脱敏、时间窗口、JSON 读写、截图清理。"""

from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import utils  # noqa: E402
from modules.logger import SensitiveFilter, setup_logger  # noqa: E402


# ---------- 脱敏 ----------

def test_mask_account() -> None:
    assert utils.mask_account("13800001111") == "138***"
    assert utils.mask_account("") == ""


def test_sanitize_token_kv() -> None:
    text = "resp: token=abcdef1234567890 status=ok"
    assert "abcdef1234567890" not in utils.sanitize_text(text)
    assert "token=***" in utils.sanitize_text(text)


def test_sanitize_password_kv() -> None:
    text = "login password=hunter2 failed"
    assert "hunter2" not in utils.sanitize_text(text)


def test_sanitize_github_token() -> None:
    text = "auth with ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456"
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456" not in utils.sanitize_text(text)


def test_sanitize_long_base64() -> None:
    long_b64 = "A" * 60
    assert long_b64 not in utils.sanitize_text(f"cookie={long_b64}")


def test_sanitize_account_in_text() -> None:
    text = "user 13800001111 logged in"
    assert "13800001111" not in utils.sanitize_text(text, "13800001111")


# ---------- 时间 ----------

def test_utc_iso_roundtrip() -> None:
    now = utils.now_utc()
    parsed = utils.from_utc_iso(utils.to_utc_iso(now))
    assert parsed is not None
    assert abs((parsed - now).total_seconds()) < 1


def test_from_utc_iso_invalid_returns_none() -> None:
    assert utils.from_utc_iso("not-a-date") is None
    assert utils.from_utc_iso("") is None


def test_format_cn_is_beijing_time() -> None:
    utc_noon = datetime(2026, 1, 1, 4, 0, 0, tzinfo=timezone.utc)
    # UTC 04:00 == 北京时间 12:00
    assert utils.format_cn(utc_noon) == "2026-01-01 12:00:00"


def test_work_window() -> None:
    cfg = SimpleNamespace(work_hours_only=True, work_hour_start=9,
                          work_hour_end=22)
    tz_cn = utils.now_cn().tzinfo
    assert utils.is_in_work_window(cfg, datetime(2026, 1, 1, 10, 0, tzinfo=tz_cn))
    assert not utils.is_in_work_window(
        cfg, datetime(2026, 1, 1, 23, 0, tzinfo=tz_cn))
    # 未启用窗口恒为 True
    cfg_off = SimpleNamespace(work_hours_only=False)
    assert utils.is_in_work_window(cfg_off)


# ---------- JSON 与截图清理 ----------

def test_json_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "nested" / "data.json"
    utils.write_json(p, {"success": False, "n": 3})
    assert utils.read_json(p) == {"success": False, "n": 3}


def test_clean_old_screenshots_keeps_recent(tmp_path: Path) -> None:
    import os

    files = []
    base = tmp_path
    for i in range(8):
        f = base / f"s{i}.png"
        f.write_bytes(b"x")
        # 递增 mtime：文件越新 mtime 越大
        stamp = datetime.now().timestamp() + i * 100
        os.utime(f, (stamp, stamp))
        files.append(f)
    removed = utils.clean_old_screenshots(base, keep=5)
    assert removed == 3
    assert all(f.exists() for f in files[3:])  # 保留最新 5 张
    assert all(not f.exists() for f in files[:3])


def test_clean_old_screenshots_keep_zero_noop(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"x")
    assert utils.clean_old_screenshots(tmp_path, keep=0) == 0


# ---------- 日志 ----------

def test_sensitive_filter_masks_record() -> None:
    filt = SensitiveFilter(account="13800001111")
    record = logging.LogRecord(
        name="t", level=logging.INFO, pathname=__file__, lineno=1,
        msg="login 13800001111 with token=xyzsecret123 failed",
        args=(), exc_info=None)
    assert filt.filter(record) is True  # 放行但内容已脱敏
    assert "13800001111" not in record.getMessage()
    assert "xyzsecret123" not in record.getMessage()


def test_setup_logger_idempotent(tmp_path: Path) -> None:
    root1 = setup_logger(tmp_path, logging.INFO, "user123")
    n_handlers = len(root1.handlers)
    root2 = setup_logger(tmp_path, logging.INFO, "user123")
    assert root2 is root1
    assert len(root2.handlers) == n_handlers  # 不重复堆积 handler


def test_logger_skip_and_success_levels(tmp_path: Path) -> None:
    setup_logger(tmp_path, logging.INFO, "", log_to_file=False)
    log = logging.getLogger("test.levels")
    # 不抛异常即可（自定义级别方法挂载成功）
    log.skip("skipping this run")  # type: ignore[attr-defined]
    log.success("grabbed!")  # type: ignore[attr-defined]
