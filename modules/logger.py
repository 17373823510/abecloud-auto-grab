"""日志封装：stdout 输出（GitHub Actions 捕获）+ 可选文件，自动脱敏。

自定义级别：
    SKIP    = 15（跳过：未到时间 / 已成功 / 不可抢）
    SUCCESS = 25（抢购成功）
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

from modules import utils

# 注册自定义级别（幂等：重复调用 addLevelName 无副作用）
SKIP_LEVEL = 15
SUCCESS_LEVEL = 25
logging.addLevelName(SKIP_LEVEL, "SKIP")
logging.addLevelName(SUCCESS_LEVEL, "SUCCESS")

_FMT = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s"
_DATEFMT = "%H:%M:%S"


class SensitiveFilter(logging.Filter):
    """在日志记录进入 handler 前对消息做脱敏。

    账号明文、token=xxx、password=xxx、GITHUB_TOKEN、
    40+ 字符 base64 串都会被替换。
    """

    def __init__(self, account: str = "") -> None:
        super().__init__()
        self.account = account

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        masked = utils.sanitize_text(msg, self.account)
        if masked != msg:
            # 替换 args，让最终格式化输出脱敏后的文本
            record.msg = masked
            record.args = ()
        return True  # 返回 True 表示放行（脱敏后仍输出）


def _log_skip(self: logging.Logger, message: str, *args: object) -> None:
    if self.isEnabledFor(SKIP_LEVEL):
        self._log(SKIP_LEVEL, message, args)  # noqa: SLF001


def _log_success(self: logging.Logger, message: str, *args: object) -> None:
    if self.isEnabledFor(SUCCESS_LEVEL):
        self._log(SUCCESS_LEVEL, message, args)  # noqa: SLF001


# 给 Logger 动态挂上 skip()/success() 便捷方法（仅挂一次）
if not hasattr(logging.Logger, "skip"):  # pragma: no branch
    logging.Logger.skip = _log_skip  # type: ignore[attr-defined]
    logging.Logger.success = _log_success  # type: ignore[attr-defined]


def setup_logger(log_dir: str | Path = "logs",
                 level: int = logging.INFO,
                 account: str = "",
                 log_to_file: bool = True) -> logging.Logger:
    """初始化根 logger：stdout handler + 可选按日文件 handler。

    Args:
        log_dir: 日志目录（不存在时自动创建；权限失败时降级为仅 stdout）。
        level: 根级别，DEBUG 时透传所有模块日志。
        account: 阿贝云账号（用于日志脱敏匹配）。
        log_to_file: 是否写文件；Actions 上也可只看 stdout。

    Returns:
        配置好的根 logger。
    """
    root = logging.getLogger()
    root.setLevel(level)
    # 清掉重复初始化时遗留的 handler（幂等）
    for handler in list(root.handlers):
        root.removeHandler(handler)

    sensitive = SensitiveFilter(account)

    stream = logging.StreamHandler(sys.stdout)
    stream.setFormatter(logging.Formatter(_FMT, _DATEFMT))
    stream.addFilter(sensitive)
    root.addHandler(stream)

    if log_to_file:
        try:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(
                log_path / f"grab_{datetime.now():%Y%m%d}.log",
                encoding="utf-8",
            )
            file_handler.setFormatter(
                logging.Formatter(_FMT, datefmt="%Y-%m-%d %H:%M:%S"))
            file_handler.addFilter(sensitive)
            root.addHandler(file_handler)
        except OSError as exc:
            # 日志目录不可写不应导致程序崩溃：降级为仅 stdout
            root.warning("日志文件初始化失败，仅输出到 stdout：%s", exc)

    # 降低第三方库噪音
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("playwright").setLevel(logging.WARNING)
    return root
