"""通用工具：时间处理、工作窗口判断、旧截图清理、脱敏、JSON 读写。

所有时间统一按 UTC 存储字符串、按北京时间（Asia/Shanghai）展示。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, time as dt_time, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 北京时间 UTC+8，不依赖系统时区配置
_TZ_CN = timezone(timedelta(hours=8))

# 敏感模式：token=xxx / password=xxx / 40+ 长度 base64 串 / ghs_ 开头的 GitHub token
_RE_TOKEN_KV = re.compile(r"(token\s*=\s*)(\S+)", re.IGNORECASE)
_RE_PASSWORD_KV = re.compile(r"(password\s*=\s*)(\S+)", re.IGNORECASE)
_RE_LONG_B64 = re.compile(r"[A-Za-z0-9+/_-]{40,}")
_RE_GITHUB_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b")


def now_utc() -> datetime:
    """返回当前 UTC 时间（aware 对象）。"""
    return datetime.now(timezone.utc)


def now_cn() -> datetime:
    """返回当前北京时间（aware 对象）。"""
    return datetime.now(_TZ_CN)


def to_utc_iso(dt: datetime) -> str:
    """将 datetime 序列化为 UTC ISO 8601 字符串。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def from_utc_iso(value: str) -> datetime | None:
    """解析 UTC ISO 8601 字符串，失败返回 None（调用方按未记录处理）。"""
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def format_cn(dt: datetime) -> str:
    """将时间格式化为北京时间可读字符串（YYYY-MM-DD HH:MM:SS）。"""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_TZ_CN).strftime("%Y-%m-%d %H:%M:%S")


def is_in_work_window(cfg: Any, dt_cn: datetime | None = None) -> bool:
    """判断当前北京时间是否处于工作窗口 [start, end) 小时内。

    Args:
        cfg: 含 work_hours_only / work_hour_start / work_hour_end 属性的配置对象。
        dt_cn: 可选的北京时间；缺省取当前时间。

    Returns:
        未启用窗口限制时恒为 True。
    """
    if not getattr(cfg, "work_hours_only", False):
        return True
    current = dt_cn or now_cn()
    start = getattr(cfg, "work_hour_start", 9)
    end = getattr(cfg, "work_hour_end", 22)
    return start <= current.hour < end


def mask_account(value: str) -> str:
    """账号脱敏：只显示前 3 位 + ***。"""
    if not value:
        return ""
    return value[:3] + "***"


def mask_secret(value: str) -> str:
    """密钥脱敏：完全遮蔽，仅保留前 2 位用于人工比对。"""
    if not value:
        return ""
    return value[:2] + "***"


def sanitize_text(text: str, account: str = "") -> str:
    """对任意文本做脱敏，用于日志输出。

    规则：token=/password= 键值遮蔽、GITHUB_TOKEN (ghp_/ghs_/...) 遮蔽、
    40+ 字符 base64 串遮蔽；若提供账号则同时遮蔽账号明文。
    """
    result = text
    if account and len(account) >= 3:
        result = result.replace(account, mask_account(account))
    result = _RE_TOKEN_KV.sub(r"\1***", result)
    result = _RE_PASSWORD_KV.sub(r"\1***", result)
    result = _RE_GITHUB_TOKEN.sub("***", result)
    result = _RE_LONG_B64.sub("***", result)
    return result


def read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 文件，返回 dict；文件不存在或损坏时抛 IOError/ValueError。

    调用方决定损坏时的降级行为（state.py 中按未成功处理）。
    """
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, data: dict[str, Any]) -> None:
    """将 dict 以 UTF-8 + 缩进写入 JSON 文件，父目录不存在时自动创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def write_json_to_bytes(data: dict[str, Any]) -> bytes:
    """将 dict 序列化为 JSON 字节串（用于 GitHub contents API 的 base64 上传）。"""
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def clean_old_screenshots(screenshot_dir: Path, keep: int = 5) -> int:
    """清理旧截图，只保留最近 keep 张，返回删除数量。

    keep <= 0 时不清理（保守处理，避免误删全部）。
    """
    if keep <= 0 or not screenshot_dir.is_dir():
        return 0
    files = sorted(
        (p for p in screenshot_dir.glob("*.png") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in files[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError as exc:  # 单个文件删除失败不阻塞整体清理
            logger.warning("删除旧截图失败 %s: %s", old, exc)
    return removed


def parse_time_window(start_hour: int, end_hour: int) -> tuple[dt_time, dt_time]:
    """将小时数转换为 time 对象元组（供展示/调试用）。"""
    return dt_time(hour=start_hour % 24), dt_time(hour=end_hour % 24)
