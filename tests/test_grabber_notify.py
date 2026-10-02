"""抢购与通知模块单元测试：成功判定三重验证、可抢性文案、短信标题规范。"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import grabber, notify  # noqa: E402


class FakeLocator:
    """模拟 Playwright locator：预置 count/is_visible/inner_text/is_disabled。"""

    def __init__(self, count: int = 1, text: str = "",
                 disabled: bool = False) -> None:
        self._count = count
        self._text = text
        self._disabled = disabled

    @property
    def first(self) -> "FakeLocator":
        return self

    def count(self) -> int:
        return self._count

    def is_visible(self) -> bool:
        return self._count > 0

    def inner_text(self) -> str:
        return self._text

    def is_disabled(self) -> bool:
        return self._disabled


class FakePage:
    """模拟 Playwright page：按选择器前缀返回预置 locator。"""

    def __init__(self, body_text: str, button_locator: FakeLocator | None,
                 captcha_locator: FakeLocator | None = None) -> None:
        self.body_text = body_text
        self.button = button_locator
        self.captcha = captcha_locator

    def locator(self, selector: str) -> FakeLocator:
        # 验证码选择器特征命中
        if any(k in selector for k in ("captcha", "verify", "geetest",
                                       "slider")):
            return self.captcha or FakeLocator(count=0)
        # 抢购按钮选择器特征命中（grabber 的 grab_button 组）
        if any(k in selector for k in ("领取", "抢购")):
            return self.button or FakeLocator(count=0)
        return FakeLocator(count=0)

    def inner_text(self, _: str) -> str:
        return self.body_text


# ---------- verify_success 三重验证 ----------

def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        abecloud=SimpleNamespace(target_plan="免费套餐"))


def test_verify_success_all_three_hit() -> None:
    page = FakePage(
        body_text="恭喜！领取成功，实例已到账",
        button_locator=FakeLocator(count=0),  # 按钮已消失
    )
    assert grabber.verify_success(page, _cfg()) is True


def test_verify_success_missing_success_text() -> None:
    page = FakePage(body_text="页面已加载",
                    button_locator=FakeLocator(count=0))
    assert grabber.verify_success(page, _cfg()) is False


def test_verify_success_failure_text_blocks() -> None:
    page = FakePage(
        body_text="领取成功，但今日已结束",  # 出现失败文案 → 不算成功
        button_locator=FakeLocator(count=0),
    )
    assert grabber.verify_success(page, _cfg()) is False


def test_verify_success_button_still_active() -> None:
    page = FakePage(
        body_text="恭喜领取成功",
        button_locator=FakeLocator(text="立即领取"),  # 按钮仍可点 → 不算成功
    )
    assert grabber.verify_success(page, _cfg()) is False


def test_verify_success_button_claimed_text() -> None:
    page = FakePage(
        body_text="恭喜领取成功",
        button_locator=FakeLocator(text="已领取"),
    )
    assert grabber.verify_success(page, _cfg()) is True


# ---------- check_availability 文案判定 ----------

def _cfg_with_url(url: str = "https://example.com/free") -> SimpleNamespace:
    return SimpleNamespace(
        abecloud=SimpleNamespace(grab_url=url, target_plan="免费套餐"))


def test_check_availability_unavailable_text(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grabber, "_grab_page_text",
                        lambda *a, **k: "活动已结束，明天再来吧")
    result = grabber.check_availability(_cfg_with_url())
    assert result["available"] is False
    assert "活动已结束" in result["reason"]


def test_check_availability_available(
        monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(grabber, "_grab_page_text",
                        lambda *a, **k: "免费服务器 立即领取 数量有限")
    result = grabber.check_availability(_cfg_with_url())
    assert result["available"] is True


def test_check_availability_network_error_treated_available(
        monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: Any, **k: Any) -> str:
        raise ConnectionError("dns fail")

    monkeypatch.setattr(grabber, "_grab_page_text", boom)
    result = grabber.check_availability(_cfg_with_url())
    # 网络异常宁可启动浏览器重试，不误杀机会
    assert result["available"] is True
    assert result["reason"] == "network_error"


# ---------- 短信标题规范 ----------

def test_sms_titles_exact() -> None:
    assert notify.SMS_TITLE_SUCCESS == "阿贝云免费服务器抢购成功"
    assert notify.SMS_TITLE_LOGIN_FAILED == "阿贝云免费服务器抢购失败-登录失败"
    assert notify.SMS_TITLE_CAPTCHA == "阿贝云免费服务器抢购失败-需要验证码"
    assert notify.SMS_TITLE_GRAB_FAILED == "阿贝云免费服务器抢购失败"


def test_sms_title_for_reason_mapping() -> None:
    assert notify.sms_title_for("captcha") == notify.SMS_TITLE_CAPTCHA
    assert notify.sms_title_for("login") == notify.SMS_TITLE_LOGIN_FAILED
    assert notify.sms_title_for("sold_out") == notify.SMS_TITLE_GRAB_FAILED


# ---------- notify 发送（mock requests） ----------

def test_send_raw_no_token_skips() -> None:
    cfg = SimpleNamespace(pushplus=SimpleNamespace(
        token="", api_url="x", timeout=5,
        wechat_enabled=True, qq_enabled=False, sms_enabled=False))
    result = notify.send_raw("t", "c", "wechat", cfg)
    assert result["ok"] is False
    assert "token" in result["msg"]


def test_send_raw_success_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    class FakeResp:
        @staticmethod
        def json() -> dict[str, Any]:
            return {"code": 200, "msg": "ok"}

    class FakeRequests:
        @staticmethod
        def post(url: str, json: dict[str, Any], timeout: int) -> FakeResp:
            calls.append({"url": url, "json": json})
            return FakeResp()

    monkeypatch.setattr(notify, "requests", FakeRequests)
    cfg = SimpleNamespace(pushplus=SimpleNamespace(
        token="tok123", api_url="https://pp/send", timeout=5,
        wechat_enabled=True, qq_enabled=False, sms_enabled=False))
    result = notify.send_raw("标题", "正文", "wechat", cfg)
    assert result["ok"] is True
    assert calls[0]["json"]["channel"] == "wechat"
    assert calls[0]["json"]["template"] == "markdown"


def test_send_raw_sms_channel_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResp:
        @staticmethod
        def json() -> dict[str, Any]:
            return {"code": 200, "msg": "ok"}

    class FakeRequests:
        @staticmethod
        def post(url: str, json: dict[str, Any], timeout: int) -> FakeResp:
            captured.update(json)
            return FakeResp()

    monkeypatch.setattr(notify, "requests", FakeRequests)
    cfg = SimpleNamespace(pushplus=SimpleNamespace(
        token="tok123", api_url="https://pp/send", timeout=5,
        wechat_enabled=False, qq_enabled=False, sms_enabled=True))
    notify.send_raw(notify.SMS_TITLE_SUCCESS, "正文会被忽略", "sms", cfg)
    # 短信：标题自定义 + 正文固定 + channel=sms
    assert captured["title"] == "阿贝云免费服务器抢购成功"
    assert captured["channel"] == "sms"
    assert captured["content"] == "阿贝云免费服务器抢购通知"


def test_send_raw_token_invalid_code(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResp:
        @staticmethod
        def json() -> dict[str, Any]:
            return {"code": 302, "msg": "token无效"}  # PushPlus 错误码 302

    class FakeRequests:
        @staticmethod
        def post(url: str, json: dict[str, Any], timeout: int) -> FakeResp:
            return FakeResp()

    monkeypatch.setattr(notify, "requests", FakeRequests)
    cfg = SimpleNamespace(pushplus=SimpleNamespace(
        token="bad", api_url="https://pp/send", timeout=5,
        wechat_enabled=True, qq_enabled=False, sms_enabled=False))
    assert notify.send_raw("t", "c", "wechat", cfg)["ok"] is False


def test_send_success_content_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []

    class FakeResp:
        @staticmethod
        def json() -> dict[str, Any]:
            return {"code": 200, "msg": "ok"}

    class FakeRequests:
        @staticmethod
        def post(url: str, json: dict[str, Any], timeout: int) -> FakeResp:
            sent.append(json)
            return FakeResp()

    monkeypatch.setattr(notify, "requests", FakeRequests)
    cfg = SimpleNamespace(
        abecloud=SimpleNamespace(target_plan="免费套餐"),
        pushplus=SimpleNamespace(
            token="tok", api_url="https://pp/send", timeout=5,
            wechat_enabled=True, qq_enabled=True, sms_enabled=False))
    notify.send_success("免费套餐 | 实例 i-abc", cfg,
                        {"attempt_count": 42})
    assert len(sent) == 2  # wechat + qq
    body = sent[0]["content"]
    assert "抢购成功" in body
    assert "免费套餐" in body
    assert "42" in body
    assert "workflow 已自动禁用" in body
