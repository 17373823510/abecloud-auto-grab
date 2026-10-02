"""配置加载：环境变量优先于可选的 config.yaml。

敏感信息（账号/密码/PushPlus token）只从环境变量读取，
config.yaml 仅承载非敏感的调优参数。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # PyYAML 为可选依赖：无 config.yaml 时可不装
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]


class ConfigError(Exception):
    """配置缺失或非法时抛出，main.py 捕获后以退出码 5 结束。"""


def _to_bool(value: str | None, default: bool) -> bool:
    """把环境变量字符串解析为布尔值（空值返回默认）。"""
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class AbeCloudConfig:
    """阿贝云站点与浏览器参数。"""

    # 以下为阿贝云实测真实地址（2026-10-02 校准），可用环境变量/config.yaml 覆盖
    login_url: str = "https://www.abeiyun.com/login/"
    grab_url: str = "https://www.abeiyun.com/control/#/freeServerList"
    target_plan: str = "立即开通"  # 控制台免费服务器列表中的开通按钮文案
    headless: bool = True
    timeout_ms: int = 60000
    work_hours_only: bool = False
    work_hour_start: int = 9
    work_hour_end: int = 22
    timezone: str = "Asia/Shanghai"


@dataclass
class PushPlusConfig:
    """PushPlus 通知渠道参数。"""

    api_url: str = "https://www.pushplus.plus/send"
    timeout: int = 10
    token: str = ""  # 仅从环境变量 PUSHPLUS_TOKEN 注入
    wechat_enabled: bool = True
    qq_enabled: bool = False
    sms_enabled: bool = False
    # 连续失败 N 次后才发失败通知（默认 12 次 ≈ 2 小时）
    failure_notify_threshold: int = 12


@dataclass
class StateConfig:
    """状态持久化行为开关。"""

    enable_git_commit: bool = True
    enable_disable_workflow: bool = True
    heartbeat_on_failure: bool = True
    heartbeat_commit_hours: float = 6.0
    state_file: str = "state/grab_state.json"


@dataclass
class OutputConfig:
    """输出目录与保留策略。"""

    log_dir: str = "logs"
    screenshot_dir: str = "screenshots"
    keep_screenshots: int = 5


@dataclass
class Config:
    """全局配置对象（环境变量 + config.yaml 合并结果）。"""

    # 敏感字段：仅环境变量
    abecloud_username: str = ""
    abecloud_password: str = ""
    github_token: str = ""
    github_repository: str = ""
    github_workflow_ref: str = ""

    abecloud: AbeCloudConfig = field(default_factory=AbeCloudConfig)
    pushplus: PushPlusConfig = field(default_factory=PushPlusConfig)
    state: StateConfig = field(default_factory=StateConfig)
    output: OutputConfig = field(default_factory=OutputConfig)

    base_dir: Path = field(default_factory=Path.cwd)


def load_config(config_path: str | None = None,
                base_dir: Path | None = None) -> Config:
    """加载配置：环境变量优先，config.yaml 提供非敏感默认值。

    Args:
        config_path: 可选 YAML 配置路径（默认查找 ./config.yaml）。
        base_dir: 项目基准目录（默认当前目录），用于解析相对路径。

    Returns:
        填充完毕的 Config 对象。

    Raises:
        ConfigError: config.yaml 存在但 PyYAML 未安装、或 YAML 语法错误。
    """
    cfg = Config(base_dir=base_dir or Path.cwd())

    # ---- 1. 可选 YAML（非敏感调优参数） ----
    yaml_path = Path(config_path) if config_path else cfg.base_dir / "config.yaml"
    data: dict[str, Any] = {}
    if yaml_path.is_file():
        if yaml is None:
            raise ConfigError(
                f"发现配置文件 {yaml_path} 但未安装 PyYAML，"
                "请 pip install PyYAML 或删除该文件改用纯环境变量"
            )
        with open(yaml_path, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if not isinstance(loaded, dict):
            raise ConfigError(f"配置文件 {yaml_path} 顶层必须是映射（mapping）")
        data = loaded

    def _apply(section: str, target: Any) -> None:
        """把 YAML 中 section 的已知字段写入 dataclass（未知字段忽略并提示）。"""
        items = data.get(section)
        if not isinstance(items, dict):
            return
        for key, value in items.items():
            if hasattr(target, key) and value is not None:
                current = getattr(target, key)
                # 按现有字段类型做轻量转换
                if isinstance(current, bool):
                    value = _to_bool(str(value), current)
                elif isinstance(current, int) and not isinstance(current, bool):
                    value = int(value)
                elif isinstance(current, float):
                    value = float(value)
                setattr(target, key, value)

    _apply("abecloud", cfg.abecloud)
    _apply("pushplus", cfg.pushplus)
    _apply("state", cfg.state)
    _apply("output", cfg.output)

    # ---- 2. 环境变量（敏感信息 + 运行时注入，优先级最高） ----
    cfg.abecloud_username = os.environ.get("ABECLOUD_USERNAME", "").strip()
    cfg.abecloud_password = os.environ.get("ABECLOUD_PASSWORD", "").strip()
    cfg.github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    cfg.github_repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    cfg.github_workflow_ref = os.environ.get("GITHUB_WORKFLOW_REF", "").strip()

    pp = cfg.pushplus
    pp.token = os.environ.get("PUSHPLUS_TOKEN", "").strip()
    pp.wechat_enabled = _to_bool(
        os.environ.get("PUSHPLUS_WECHAT_ENABLED"), pp.wechat_enabled)
    pp.qq_enabled = _to_bool(
        os.environ.get("PUSHPLUS_QQ_ENABLED"), pp.qq_enabled)
    pp.sms_enabled = _to_bool(
        os.environ.get("PUSHPLUS_SMS_ENABLED"), pp.sms_enabled)

    # headless 在 Actions 上必须为 True，环境变量可强制本地调试
    if _to_bool(os.environ.get("ABECLOUD_HEADLESS"), cfg.abecloud.headless) is False:
        cfg.abecloud.headless = False
    return cfg


def validate_config(cfg: Config) -> tuple[bool, list[str]]:
    """校验配置完整性。

    Returns:
        (是否通过, 错误消息列表)。必填项：阿贝云账号、密码。
        PushPlus token 缺失只降级（不发通知），不算配置错误。
    """
    errors: list[str] = []
    if not cfg.abecloud_username:
        errors.append("缺少环境变量 ABECLOUD_USERNAME（阿贝云账号）")
    if not cfg.abecloud_password:
        errors.append("缺少环境变量 ABECLOUD_PASSWORD（阿贝云密码）")
    if not cfg.abecloud.login_url.startswith("http"):
        errors.append("abecloud.login_url 必须是 http(s) URL")
    if not cfg.abecloud.grab_url.startswith("http"):
        errors.append("abecloud.grab_url 必须是 http(s) URL")
    if cfg.abecloud.work_hour_start >= cfg.abecloud.work_hour_end:
        errors.append("work_hour_start 必须小于 work_hour_end")
    return (not errors, errors)
