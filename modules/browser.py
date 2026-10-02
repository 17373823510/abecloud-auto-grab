"""Playwright 浏览器工厂：headless Chromium + Actions 适配参数。

以上下文管理器形式使用，确保浏览器用完即关（Actions 容器无残留进程）。
"""

from __future__ import annotations

import contextlib
import logging
from types import TracebackType
from typing import Any, Iterator

logger = logging.getLogger(__name__)

# Playwright 延迟导入：保证缺依赖环境下 --help / py_compile 可用
try:
    from playwright.sync_api import Browser, BrowserContext, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None  # type: ignore[assignment]
    Browser = None  # type: ignore[assignment,misc]
    BrowserContext = None  # type: ignore[assignment,misc]

# Ubuntu runner 上 Chromium 需要的低资源参数（顺序固定，便于测试断言）
CHROMIUM_LAUNCH_ARGS = [
    "--disable-dev-shm-usage",
    "--no-sandbox",
    "--disable-gpu",
]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightNotInstalledError(RuntimeError):
    """本机未安装 Playwright 时抛出，提示安装命令。"""

    def __init__(self) -> None:
        super().__init__(
            "Playwright 未安装。请执行：pip install playwright && "
            "playwright install chromium"
        )


class BrowserSession:
    """封装 playwright / browser / context 三层生命周期。

    用法::

        with BrowserSession(cfg) as session:
            page = session.context.new_page()
    """

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self._playwright: Any = None
        self.browser: Any = None
        self.context: Any = None

    def __enter__(self) -> "BrowserSession":
        if sync_playwright is None:
            raise PlaywrightNotInstalledError()
        abe = self.cfg.abecloud
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(
            headless=abe.headless,
            args=CHROMIUM_LAUNCH_ARGS,
        )
        self.context = self.browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=DEFAULT_USER_AGENT,
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        self.context.set_default_timeout(abe.timeout_ms)
        logger.debug("浏览器已启动（headless=%s）", abe.headless)
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        # 逐层关闭；任何一层失败都不掩盖业务异常
        for closer, name in ((self.context, "context"),
                             (self.browser, "browser"),
                             (self._playwright, "playwright")):
            if closer is None:
                continue
            try:
                closer.close()
            except Exception as close_exc:  # noqa: BLE001 关闭失败不应中断清理链
                logger.warning("关闭 %s 失败：%s", name, close_exc)
        self.context = self.browser = self._playwright = None
        logger.debug("浏览器已关闭")


@contextlib.contextmanager
def launch_browser(cfg: Any) -> Iterator[BrowserSession]:
    """上下文管理器：启动并最终关闭浏览器。

    Yields:
        BrowserSession 实例（含 .browser / .context）。
    """
    with BrowserSession(cfg) as session:
        yield session


def create_context(browser: Any, cfg: Any) -> Any:
    """对已有 browser 创建全新上下文（Actions 无持久 Cookie，每次全新）。

    独立函数便于测试时 mock。
    """
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=DEFAULT_USER_AGENT,
        locale="zh-CN",
        timezone_id="Asia/Shanghai",
    )
    context.set_default_timeout(cfg.abecloud.timeout_ms)
    return context
