"""登录模块单元测试：验证码检测、已登录判定（mock page/context）。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import auth  # noqa: E402


class FakeLocator:
    def __init__(self, count: int = 0) -> None:
        self._count = count

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return self._count


class FakePage:
    """按选择器内容返回命中数的模拟页面。"""

    def __init__(self, hits: dict[str, int]) -> None:
        # hits: 选择器关键字 → 命中数量
        self.hits = hits

    def locator(self, selector: str) -> FakeLocator:
        for keyword, count in self.hits.items():
            if keyword in selector:
                return FakeLocator(count=count)
        return FakeLocator(count=0)


def test_detect_captcha_hit() -> None:
    page = FakePage({"captcha": 1})
    assert auth.detect_captcha(page) is True


def test_detect_captcha_slider_hit() -> None:
    page = FakePage({"slider": 1})
    assert auth.detect_captcha(page) is True


def test_detect_captcha_miss() -> None:
    page = FakePage({"username": 1})
    assert auth.detect_captcha(page) is False


def test_detect_captcha_geetest_hit() -> None:
    page = FakePage({"geetest": 1})
    assert auth.detect_captcha(page) is True


def test_is_logged_in_by_session_cookie() -> None:
    class FakeContext:
        @staticmethod
        def cookies() -> list[dict[str, Any]]:
            return [{"name": "PHPSESSID", "value": "abc123"},
                    {"name": "trivial", "value": ""}]

    cfg = SimpleNamespace(abecloud=SimpleNamespace(login_url="https://x/l"))
    assert auth.is_logged_in(FakeContext(), cfg) is True


def test_is_logged_in_no_keyword_cookie() -> None:
    class FakeContext:
        @staticmethod
        def cookies() -> list[dict[str, Any]]:
            return [{"name": "cdn_pref", "value": "dark"}]

    cfg = SimpleNamespace(abecloud=SimpleNamespace(login_url="https://x/l"))
    assert auth.is_logged_in(FakeContext(), cfg) is False


def test_is_logged_in_cookies_error_returns_false() -> None:
    class FakeContext:
        @staticmethod
        def cookies() -> list[dict[str, Any]]:
            raise RuntimeError("context closed")

    cfg = SimpleNamespace(abecloud=SimpleNamespace(login_url="https://x/l"))
    assert auth.is_logged_in(FakeContext(), cfg) is False
