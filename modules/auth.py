"""阿贝云登录：全新上下文 + 验证码检测（遇码即停，绝不绕过）。

登录失败原因分类：
    captcha          出现图形验证码/滑块
    wrong_credentials 凭据错误（提示"密码错误"等文案）
    network          页面加载超时/网络异常
    unknown          页面结构变化等无法归类
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ---- 选择器常量 ----
# 已按阿贝云真实页面校准（2026-10-02 实测 https://www.abeiyun.com/login/）：
#   <form action="/login/login.html" id="loginForm" method="post">
#     <input type="text" name="username" placeholder="手机" id="userName">
#     <input id="passwordInput" type="password" name="password" placeholder="登录密码">
#     <input type="submit" value="登 录" id="loginSubmit">
# 注意：登录按钮是 <input type=submit> 而非 <button>，故 submit_button 首位为 #loginSubmit。
# 后续候选为兜底（页面改版时仍有机会命中）。
LOGIN_SELECTORS: dict[str, list[str]] = {
    "username_input": [
        "#userName",                       # 实测真实 id（优先）
        "input[name='username']",
        "input[placeholder*='手机']",       # 实测 placeholder="手机"
        "input[name='account']",
        "input[placeholder*='账号']",
        "input[placeholder*='邮箱']",
        "input[type='tel']",
    ],
    "password_input": [
        "#passwordInput",                  # 实测真实 id
        "input[type='password']",
        "input[name='password']",
    ],
    "submit_button": [
        "#loginSubmit",                    # 实测真实 id（input[type=submit]）
        "input[type='submit']",
        "button[type='submit']",
        "button:has-text('登 录')",
        "button:has-text('登录')",
        "a:has-text('登录')",
    ],
}

# 验证码特征（元素存在任一即判定遇码）
CAPTCHA_SELECTORS: list[str] = [
    "iframe[src*='captcha']",
    "img[src*='captcha']",
    "img[src*='verify']",
    "img[id*='captcha']",
    "div[class*='captcha']",
    "div[id*='captcha']",
    "canvas[class*='captcha']",
    "#geetest",
    ".geetest_holder",
    ".slider",
    "div[class*='slider']",
]

# 登录失败文案（凭据错误类）
WRONG_CREDENTIAL_TEXTS = ["密码错误", "账号不存在", "用户名或密码", "登录失败",
                          "账号或密码", "验证失败"]

# 登录成功特征（URL 跳出登录页 或 出现用户中心标记）
USER_CENTER_MARKERS = ["个人中心", "我的账户", "控制台", "用户中心", "退出登录"]


def _first_visible(page: Any, selectors: list[str]) -> Any | None:
    """返回第一个可见的元素；全部不可见/不存在返回 None。"""
    for sel in selectors:
        try:
            locator = page.locator(sel).first
            if locator.count() > 0 and locator.is_visible():
                return locator
        except Exception:  # noqa: BLE001 选择器非法/超时按不存在处理
            continue
    return None


def detect_captcha(page: Any) -> bool:
    """检测当前页面是否出现验证码（图形码/滑块/geetest 等）。

    Returns:
        True 表示出现验证码——按规格立即失败，不做任何绕过。
    """
    for sel in CAPTCHA_SELECTORS:
        try:
            if page.locator(sel).first.count() > 0:
                logger.warning("检测到验证码元素：%s", sel)
                return True
        except Exception:  # noqa: BLE001 单个选择器异常不中断检测
            continue
    return False


def is_logged_in(context: Any, cfg: Any) -> bool:
    """通过 Cookie 特征判断当前上下文是否已登录。

    Args:
        context: Playwright BrowserContext。
        cfg: Config 对象（只用 login_url 判定域名）。

    Notes:
        登录成功后站点通常下发会话 Cookie；此处做保守判断：
        存在任意非空 value 且名字含 session/token/auth 之一的 Cookie。
    """
    try:
        cookies = context.cookies()
    except Exception:  # noqa: BLE001
        return False
    keywords = ("session", "sessid", "token", "auth", "login", "uid", "user")
    return any(
        c.get("value") and any(k in c.get("name", "").lower() for k in keywords)
        for c in cookies
    )


def login(username: str, password: str, cfg: Any) -> dict[str, Any]:
    """启动浏览器并完成登录（规格入口函数）。

    Args:
        username / password: 阿贝云账号密码（来自 Secrets 环境变量）。
        cfg: Config 对象。

    Returns:
        成功: ``{"success": True, "cookies": dict, "context_state": dict,
        "session": BrowserSession}``（session 供后续抢购复用，调用方负责关闭）
        失败: ``{"success": False, "reason": str, "error": str}``
        reason ∈ {captcha, wrong_credentials, network, unknown}
    """
    from modules.browser import BrowserSession  # 延迟导入避免循环依赖

    session = BrowserSession(cfg)
    try:
        session.__enter__()
    except PlaywrightNotInstalledError as exc:
        return {"success": False, "reason": "unknown", "error": str(exc)}
    except Exception as exc:  # noqa: BLE001 浏览器启动失败归为环境错误
        logger.error("浏览器启动失败：%s", exc)
        return {"success": False, "reason": "network",
                "error": f"浏览器启动失败: {exc}"}

    result = login_with_context(session.context, username, password, cfg)
    if result.get("success"):
        result["session"] = session  # 成功时把 session 交给调用方继续抢购
        return result
    # 失败时立即释放浏览器，节省 Actions 时间
    session.__exit__(None, None, None)
    return result


def login_with_context(context: Any, username: str, password: str,
                       cfg: Any) -> dict[str, Any]:
    """实际登录实现（在给定 context 上操作，便于测试 mock）。

    规格中的 ``login(username, password, config)`` 语义由本函数承接：
    main.py 先建 BrowserSession 再调用本函数。
    """
    abe = cfg.abecloud
    page = context.new_page()
    try:
        try:
            page.goto(abe.login_url, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001 超时/DNS 均归为网络错误
            logger.error("登录页加载失败：%s", exc)
            return {"success": False, "reason": "network",
                    "error": f"页面加载失败: {exc}"}

        # 填写表单前先查验证码：登录页本身可能就有码
        if detect_captcha(page):
            return {"success": False, "reason": "captcha",
                    "error": "登录页出现验证码，本项目不做绕过"}

        user_input = _first_visible(page, LOGIN_SELECTORS["username_input"])
        pass_input = _first_visible(page, LOGIN_SELECTORS["password_input"])
        submit_btn = _first_visible(page, LOGIN_SELECTORS["submit_button"])
        if user_input is None or pass_input is None or submit_btn is None:
            logger.error("登录表单元素未找到（页面结构可能已变化）")
            return {"success": False, "reason": "unknown",
                    "error": "登录表单元素未找到，请校准 LOGIN_SELECTORS"}

        user_input.fill(username)
        pass_input.fill(password)
        submit_btn.click()

        # 提交后再查验证码：点击后弹出的滑块/图形码
        if detect_captcha(page):
            return {"success": False, "reason": "captcha",
                    "error": "登录提交后出现验证码"}

        # 等待跳转或错误文案（二选一先出现）
        try:
            with page.expect_navigation(wait_until="domcontentloaded",
                                        timeout=15000):
                pass
        except Exception:  # noqa: BLE001 未跳转则继续按页面文案判定
            logger.debug("提交后未发生跳转，按页面内容判定登录结果")

        body_text = ""
        try:
            body_text = page.inner_text("body")
        except Exception:  # noqa: BLE001
            body_text = ""

        if any(t in body_text for t in WRONG_CREDENTIAL_TEXTS):
            # 排除验证码误报文案后判定凭据错误
            if not detect_captcha(page):
                logger.warning("页面提示凭据错误")
                return {"success": False, "reason": "wrong_credentials",
                        "error": "页面提示账号或密码错误"}

        # 成功判定：URL 跳出登录页，或出现用户中心标记，或会话 Cookie 存在
        current_url = page.url
        left_login_page = "login" not in current_url.lower()
        has_marker = any(m in body_text for m in USER_CENTER_MARKERS)
        cookie_ok = is_logged_in(context, cfg)

        if left_login_page or has_marker or cookie_ok:
            cookies = {c["name"]: c["value"] for c in context.cookies()}
            logger.info("登录成功")
            return {"success": True, "cookies": cookies,
                    "context_state": {"url": current_url}}

        logger.warning("登录后未检测到成功特征，按 unknown 处理")
        return {"success": False, "reason": "unknown",
                "error": "登录后未出现成功特征（URL/文案/Cookie 均未命中）"}
    finally:
        try:
            page.close()
        except Exception:  # noqa: BLE001
            pass
