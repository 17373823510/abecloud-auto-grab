"""PushPlus 通知：微信/QQ 渠道发详细 markdown，短信渠道仅发自定义标题。

文案规范（规格 7.6）：
    短信标题 = "阿贝云免费服务器抢购成功/失败" 主体 + 简短阶段名（≤8 字），
    不加时间戳；微信/QQ 发全面内容。
通知失败不阻塞主流程（只记日志）。
"""

from __future__ import annotations

import logging
from typing import Any

from modules import utils

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

# ---- 短信标题规范表（严格按规格，勿随意修改） ----
SMS_TITLE_SUCCESS = "阿贝云免费服务器抢购成功"
SMS_TITLE_LOGIN_FAILED = "阿贝云免费服务器抢购失败-登录失败"
SMS_TITLE_CAPTCHA = "阿贝云免费服务器抢购失败-需要验证码"
SMS_TITLE_GRAB_FAILED = "阿贝云免费服务器抢购失败"


def _enabled_channels(cfg: Any) -> list[str]:
    """返回启用的渠道列表（wechat / qq / sms）。"""
    pp = cfg.pushplus
    channels: list[str] = []
    if pp.wechat_enabled:
        channels.append("wechat")
    if pp.qq_enabled:
        channels.append("qq")
    if pp.sms_enabled:
        channels.append("sms")
    return channels


def send_raw(title: str, content: str, channel: str, cfg: Any) -> dict[str, Any]:
    """底层发送：调用 PushPlus API。

    Args:
        title: 消息标题（短信渠道只有标题会被送达）。
        content: 正文（短信渠道忽略）。
        channel: wechat / qq / sms。
        cfg: Config 对象。

    Returns:
        ``{"ok": bool, "channel": str, "code": int, "msg": str}``
        常见错误码：302 token 无效、888 积分不足。
    """
    pp = cfg.pushplus
    if not pp.token:
        logger.warning("PUSHPLUS_TOKEN 未配置，跳过 %s 通知", channel)
        return {"ok": False, "channel": channel, "code": -1,
                "msg": "token 未配置"}
    if requests is None:
        logger.warning("requests 未安装，跳过 %s 通知", channel)
        return {"ok": False, "channel": channel, "code": -1,
                "msg": "requests 未安装"}

    payload = {"token": pp.token, "title": title, "template": "markdown"}
    if channel in ("wechat", "qq"):
        payload["content"] = content
        payload["channel"] = channel
    else:  # sms：正文固定，仅自定义标题可送达
        payload["content"] = "阿贝云免费服务器抢购通知"
        payload["channel"] = "sms"

    try:
        resp = requests.post(pp.api_url, json=payload, timeout=pp.timeout)
        data = resp.json()
    except Exception as exc:  # noqa: BLE001 网络/解析异常
        logger.warning("%s 通知发送异常（不阻塞主流程）：%s", channel, exc)
        return {"ok": False, "channel": channel, "code": -1,
                "msg": str(exc)}

    code = int(data.get("code", -1))
    ok = code == 200
    if ok:
        logger.info("%s 通知发送成功", channel)
    else:
        # 常见错误码：302 token 无效；888 积分不足
        logger.warning("%s 通知失败：code=%s msg=%s",
                       channel, code, data.get("msg", ""))
    return {"ok": ok, "channel": channel, "code": code,
            "msg": str(data.get("msg", ""))}


def send_success(detail: str, cfg: Any,
                 state: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """抢购成功通知：成功必发（除非未配置 token）。

    Args:
        detail: 成功详情（套餐、实例信息等）。
        cfg: Config 对象。
        state: 可选 state dict（用于累计尝试次数、成功时间展示）。

    Returns:
        各渠道发送结果列表。
    """
    state = state or {}
    now_cn = utils.format_cn(utils.now_utc())
    attempts = state.get("attempt_count", "N/A")
    target = cfg.abecloud.target_plan

    md = "\n".join([
        "## ✅ 阿贝云免费服务器抢购成功",
        "",
        f"- **事件**：✅ 抢购成功",
        f"- **时间**：{now_cn}（北京时间）",
        f"- **目标套餐**：{target}",
        f"- **成功详情**：{detail or '见服务器控制台'}",
        f"- **累计尝试次数**：{attempts}",
        "",
        "> ⚠️ 提醒：workflow 已自动禁用，如需再次抢购请到 Actions 页面手动启用。",
    ])
    results: list[dict[str, Any]] = []
    for channel in _enabled_channels(cfg):
        results.append(
            send_raw("[阿贝云] 抢购成功", md, channel, cfg))
    # 短信渠道标题严格按规范
    return results


def send_failure(reason: str, error: str, cfg: Any) -> list[dict[str, Any]]:
    """失败通知（默认关闭；连续失败达阈值后由 main 触发）。"""
    now_cn = utils.format_cn(utils.now_utc())
    md = "\n".join([
        "## ❌ 阿贝云免费服务器抢购失败",
        "",
        f"- **失败原因**：{reason}",
        f"- **错误详情**：{error or '（无）'}",
        f"- **时间**：{now_cn}（北京时间）",
        "- **下次重试**：约 10 分钟后（GitHub Actions */10 调度）",
    ])
    results: list[dict[str, Any]] = []
    for channel in _enabled_channels(cfg):
        results.append(send_raw("[阿贝云] 抢购失败", md, channel, cfg))
    return results


def send_skip(reason: str, cfg: Any) -> list[dict[str, Any]]:
    """跳过通知（可选，默认仅 DEBUG 日志，不发消息避免噪音）。

    目前实现为：只在日志里记录，不实际发送——符合规格
    "失败通知默认关闭" 的噪音控制精神。返回空列表保持接口一致。
    """
    logger.debug("跳过通知（默认关闭）：%s", reason)
    return []


def send_test(cfg: Any) -> list[dict[str, Any]]:
    """测试通知（--test-notify 入口）：向所有启用渠道发一条测试消息。"""
    now_cn = utils.format_cn(utils.now_utc())
    md = "\n".join([
        "## 🧪 PushPlus 测试消息",
        "",
        "如果你看到这条消息，说明通知渠道配置正确。",
        f"- 时间：{now_cn}（北京时间）",
    ])
    results: list[dict[str, Any]] = []
    for channel in _enabled_channels(cfg):
        results.append(send_raw("[阿贝云] 通知测试", md, channel, cfg))
    return results


def sms_title_for(reason: str) -> str:
    """按失败原因映射规范短信标题（供测试与一致性校验）。"""
    mapping = {
        "captcha": SMS_TITLE_CAPTCHA,
        "login_failed": SMS_TITLE_LOGIN_FAILED,
        "login": SMS_TITLE_LOGIN_FAILED,
    }
    return mapping.get(reason, SMS_TITLE_GRAB_FAILED)
