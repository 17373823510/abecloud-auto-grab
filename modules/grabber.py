"""抢购核心逻辑：轻量检查（requests）+ 实际抢购（Playwright）。

成功判定三重验证（AND 关系）：
    1. 页面出现成功文案（领取成功/已到账/恭喜 等）
    2. 页面不再出现失败文案（已抢完/库存不足/今日已结束）
    3. 抢购按钮变为"已领取"或消失
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

# requests 延迟导入：保证缺依赖环境下模块可导入（--help 可用）
try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

# ---- 抢购页选择器（已按用户提供的控制台按钮文案"立即开通"补充） ----
# 注意：阿贝云控制台 /control/ 是 Vue 哈希路由 SPA，内容由 JS 渲染，
# 因此下方按钮必须在 _wait_page_ready() 等待之后再查找（见 attempt_grab）。
GRAB_SELECTORS: dict[str, list[str]] = {
    "grab_button": [
        "button:has-text('立即开通')",   # 控制台实测文案
        "a:has-text('立即开通')",
        "button:has-text('立即领取')",
        "button:has-text('免费领取')",
        "button:has-text('立即抢购')",
        "a:has-text('立即领取')",
        "a:has-text('免费领取')",
        "button:has-text('领取')",
    ],
    "confirm_button": [
        "button:has-text('确认')",
        "button:has-text('确定')",
        "div[class*='modal'] button:has-text('确')",
    ],
    "claimed_button_text": ["已领取", "已抢", "已领取完"],
}

# 成功/失败/不可抢文案（用于轻量检查与三重验证）
SUCCESS_TEXTS = ["领取成功", "已到账", "恭喜", "开通成功", "领取完成"]
FAILURE_TEXTS = ["已抢完", "库存不足", "今日已结束", "已领完", "活动已结束",
                 "来晚了", "已被领完"]
UNAVAILABLE_TEXTS = ["即将开放", "即将开始", "未开始", "敬请期待", "暂无库存",
                     "售罄", "已抢完", "今日已结束", "活动已结束"]

# SPA 空壳特征（Vue/React 单页应用在 JS 执行前的 HTML）
SPA_SHELL_MARKERS = [
    'id="app"',
    "id=app",
    'id="root"',
    "static/js/app.",
    "You need to enable JavaScript",
]

HTTP_TIMEOUT = 15


def _grab_page_text(url: str, cookies: dict[str, str] | None = None,
                    timeout: int = HTTP_TIMEOUT) -> str:
    """GET 抢购页并返回响应文本（失败抛异常，由调用方分类）。"""
    if requests is None:
        raise RuntimeError("requests 未安装：pip install requests")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    resp = requests.get(url, cookies=cookies or {}, headers=headers,
                        timeout=timeout)
    resp.raise_for_status()
    return resp.text


def check_availability(cfg: Any,
                       cookies: dict[str, str] | None = None) -> dict[str, Any]:
    """轻量检查是否可抢（不启动 Playwright，节约 Actions 额度）。

    Args:
        cfg: Config 对象（读取 abecloud.grab_url）。
        cookies: 可选登录 Cookie（登录成功后可传入提高判断准确性）。

    Returns:
        ``{"available": bool, "reason": str}``
        网络异常时返回 available=True（宁可启动浏览器重试，不误杀机会），
        reason 标注 network_error。
    """
    url = cfg.abecloud.grab_url
    try:
        text = _grab_page_text(url, cookies)
    except Exception as exc:  # noqa: BLE001 网络层任何异常
        logger.warning("轻量检查网络异常（按可抢处理，交由浏览器兜底）：%s", exc)
        return {"available": True, "reason": "network_error"}

    for keyword in UNAVAILABLE_TEXTS:
        if keyword in text:
            logger.debug("页面含不可抢文案：%s", keyword)
            return {"available": False, "reason": f"页面提示 {keyword}"}

    # SPA 空壳识别：阿贝云控制台是 Vue 哈希路由，未登录/未渲染时
    # requests 只能拿到 <div id=app> 空壳，其中不含任何库存文案。
    # 这种情况无法用轻量请求判断库存，必须交给浏览器（返回可抢，不误杀机会）。
    if any(marker in text for marker in SPA_SHELL_MARKERS):
        logger.debug("响应为 SPA 空壳，库存信息需浏览器端判定")
        return {"available": True, "reason": "spa_shell:内容由JS渲染，需浏览器判定"}

    logger.debug("轻量检查未发现不可抢文案，判定可抢")
    return {"available": True, "reason": "未发现不可抢标记"}


def _first_visible(page: Any, selectors: list[str]) -> Any | None:
    """返回第一个可见元素，全部未命中返回 None。"""
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() > 0 and locator.is_visible():
                return locator
        except Exception:  # noqa: BLE001
            continue
    return None


def _button_claimed(page: Any) -> bool:
    """判断抢购按钮是否已变为"已领取"状态或消失。"""
    btn = _first_visible(page, GRAB_SELECTORS["grab_button"])
    if btn is None:
        # 按钮消失也算"已领取"信号之一
        return True
    try:
        text = btn.inner_text()
    except Exception:  # noqa: BLE001
        return False
    return any(t in text for t in GRAB_SELECTORS["claimed_button_text"])


def _wait_page_ready(page: Any, timeout_ms: int = 15000) -> None:
    """等待 SPA 渲染完成（关键）。

    阿贝云控制台是 Vue 哈希路由（/control/#/freeServerList），
    goto 完成时 DOM 只有 <div id=app> 空壳，必须等 JS 渲染出真实内容，
    否则立即查找按钮必然失败。

    两步等待：
    1. networkidle（网络的空闲信号，SPA 数据请求完成后触发）
    2. 额外 3 秒的渲染缓冲（Vue 挂载 + DOM patch）

    任何一步失败都不阻塞（页面可能是静态页或 mock 对象）。
    """
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:  # noqa: BLE001 超时/SPA 永不停歇/对象不支持均忽略
        logger.debug("networkidle 等待未达成，继续下一步")
    try:
        page.wait_for_timeout(3000)  # Vue 挂载缓冲
    except Exception:  # noqa: BLE001
        pass


def verify_success(page: Any, cfg: Any) -> bool:
    """三重验证抢购是否成功（全部命中才算成功）。

    1. 出现成功文案
    2. 无失败文案
    3. 按钮已领取或消失
    """
    try:
        body_text = page.inner_text("body")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取页面文本失败：%s", exc)
        return False
    has_success = any(t in body_text for t in SUCCESS_TEXTS)
    no_failure = not any(t in body_text for t in FAILURE_TEXTS)
    button_ok = _button_claimed(page)
    logger.debug("验证三要素：success=%s no_failure=%s button=%s",
                 has_success, no_failure, button_ok)
    return has_success and no_failure and button_ok


def attempt_grab(context: Any, cfg: Any,
                 screenshot_dir: Any = None) -> dict[str, Any]:
    """实际抢购：打开抢购页 → 点击领取 → 处理确认 → 等待 → 判定。

    Args:
        context: 登录后的 BrowserContext。
        cfg: Config 对象。
        screenshot_dir: 截图目录（Path；None 时用 cfg.output.screenshot_dir）。

    Returns:
        ``{"success": bool, "reason": str, "detail": str, "screenshot": str}``
        reason ∈ {claimed, sold_out, disabled, error, unknown}
        失败/未知时保存截图便于人工诊断。
    """
    from pathlib import Path

    if screenshot_dir is None:
        screenshot_dir = Path(cfg.output.screenshot_dir)
    screenshot_dir = Path(screenshot_dir)

    page = context.new_page()
    screenshot_path = ""
    try:
        try:
            page.goto(cfg.abecloud.grab_url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "reason": "error",
                    "detail": f"抢购页加载失败: {exc}", "screenshot": ""}

        # SPA 渲染等待：控制台内容由 JS 填充，不等就会被误判为"未找到按钮"
        _wait_page_ready(page)

        body_text = ""
        try:
            body_text = page.inner_text("body")
        except Exception:  # noqa: BLE001
            body_text = ""

        # 失败前置判断：页面直接告知已抢完/结束
        for keyword in FAILURE_TEXTS:
            if keyword in body_text:
                logger.info("抢购页提示：%s", keyword)
                screenshot_path = _shot(page, screenshot_dir, "failed")
                return {"success": False, "reason": "sold_out",
                        "detail": f"页面提示 {keyword}",
                        "screenshot": screenshot_path}

        target = cfg.abecloud.target_plan
        grab_btn = _first_visible(page, GRAB_SELECTORS["grab_button"])
        if grab_btn is None:
            screenshot_path = _shot(page, screenshot_dir, "failed")
            logger.warning("未找到抢购按钮（选择器需校准）")
            return {"success": False, "reason": "unknown",
                    "detail": f"未找到抢购按钮（目标套餐 {target}），"
                               "请校准 GRAB_SELECTORS",
                    "screenshot": screenshot_path}

        try:
            if grab_btn.is_disabled():
                screenshot_path = _shot(page, screenshot_dir, "failed")
                return {"success": False, "reason": "disabled",
                        "detail": "抢购按钮处于禁用状态",
                        "screenshot": screenshot_path}
        except Exception:  # noqa: BLE001 is_disabled 不可用时忽略
            pass

        grab_btn.click()

        # 处理确认弹窗（若有）：出现即点确认，未出现直接继续
        confirm_btn = _first_visible(page, GRAB_SELECTORS["confirm_button"])
        if confirm_btn is not None:
            try:
                confirm_btn.click()
                logger.debug("已点击确认弹窗")
            except Exception as exc:  # noqa: BLE001
                logger.warning("确认弹窗点击失败（可能已自动确认）：%s", exc)

        # 等待 3-5 秒让结果渲染（规格要求，避免过早判定）
        page.wait_for_timeout(4000)

        if verify_success(page, cfg):
            screenshot_path = _shot(page, screenshot_dir, "success")
            detail = _extract_detail(page, cfg)
            return {"success": True, "reason": "claimed",
                    "detail": detail, "screenshot": screenshot_path}

        # 未通过三重验证：细分失败原因
        body_text = ""
        try:
            body_text = page.inner_text("body")
        except Exception:  # noqa: BLE001
            body_text = ""
        for keyword in FAILURE_TEXTS:
            if keyword in body_text:
                screenshot_path = _shot(page, screenshot_dir, "failed")
                return {"success": False, "reason": "sold_out",
                        "detail": f"提交后页面提示 {keyword}",
                        "screenshot": screenshot_path}

        # 未知状态：保存截图，下次继续尝试，避免误判
        screenshot_path = _shot(page, screenshot_dir, "failed")
        logger.warning("抢购结果未知（页面无匹配文案），已保存截图")
        return {"success": False, "reason": "unknown",
                "detail": "提交后未命中任何已知文案",
                "screenshot": screenshot_path}
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass


def _shot(page: Any, screenshot_dir: Any, tag: str) -> str:
    """保存截图，返回路径字符串；失败返回空串（不阻塞主流程）。"""
    from pathlib import Path

    try:
        screenshot_dir = Path(screenshot_dir)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        name = f"{tag}_{datetime.now():%Y%m%d_%H%M%S}.png"
        path = screenshot_dir / name
        page.screenshot(path=str(path), full_page=True)
        logger.info("截图已保存：%s", path)
        return str(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning("截图保存失败：%s", exc)
        return ""


def _extract_detail(page: Any, cfg: Any) -> str:
    """从成功页提取套餐/实例详情（尽力而为，失败返回套餐名兜底）。"""
    target = cfg.abecloud.target_plan
    try:
        body = page.inner_text("body")
        for marker in ("实例", "订单", "编号"):
            if marker in body:
                # 截取包含关键标记的短文本片段作为详情
                idx = body.find(marker)
                start = max(0, idx - 20)
                snippet = body[start:idx + 40].replace("\n", " ").strip()
                if snippet:
                    return f"{target} | {snippet}"
        return target
    except Exception:  # noqa: BLE001
        return target
