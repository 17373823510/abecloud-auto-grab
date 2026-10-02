"""状态持久化：state/grab_state.json + GitHub API 提交 + 禁用 workflow。

双保险机制：
    主方案：成功后调用 GitHub API 禁用 workflow（立即生效）
    兜底方案：state 文件 success=true，每次运行开始先检查
心跳机制：未成功时定期 commit（默认每 6 小时一次，防 60 天无活动禁用）
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from modules import utils

logger = logging.getLogger(__name__)

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

# GitHub API 基址（企业服务器不在本项目范围内，固定官方地址）
GITHUB_API = "https://api.github.com"

# state 文件默认字段（首次运行自动创建）
DEFAULT_STATE: dict[str, Any] = {
    "success": False,
    "success_time": "",
    "success_detail": "",
    "last_attempt_time": "",
    "last_result": "",
    "attempt_count": 0,
    "heartbeat_time": "",
    "consecutive_failures": 0,
    "last_heartbeat_commit": "",
}


class StateStore:
    """状态读写与远程同步（commit / disable workflow / 心跳）。

    Args:
        cfg: Config 对象（读取 state.state_file、GITHUB_TOKEN 等）。
    """

    def __init__(self, cfg: Any) -> None:
        self.cfg = cfg
        self.path = (cfg.base_dir / cfg.state.state_file).resolve()
        self.state: dict[str, Any] = dict(DEFAULT_STATE)

    # ---- 本地读写 ----

    def load(self) -> dict[str, Any]:
        """加载本地 state 文件。

        文件不存在或损坏（非法 JSON / 非映射）时按默认（未成功）处理，
        并记录 warning——绝不让坏文件中断抢购流程。
        """
        if not self.path.is_file():
            logger.info("state 文件不存在，使用默认初始状态")
            self.state = dict(DEFAULT_STATE)
            return self.state
        try:
            data = utils.read_json(self.path)
        except (OSError, ValueError) as exc:
            logger.warning("state 文件损坏，按未成功处理：%s", exc)
            self.state = dict(DEFAULT_STATE)
            return self.state
        if not isinstance(data, dict):
            logger.warning("state 文件顶层非映射，按未成功处理")
            self.state = dict(DEFAULT_STATE)
            return self.state
        # 合并：未知字段忽略，缺失字段补默认值
        merged = dict(DEFAULT_STATE)
        merged.update({k: v for k, v in data.items() if k in DEFAULT_STATE})
        self.state = merged
        return self.state

    def save(self) -> None:
        """把当前 state 写回本地文件（自动创建父目录）。"""
        utils.write_json(self.path, self.state)

    def is_already_success(self) -> bool:
        """是否已成功过（兜底检查入口）。"""
        return bool(self.state.get("success"))

    def mark_success(self, detail: str) -> dict[str, Any]:
        """标记成功：置位 success、记录时间与详情、清零连续失败。"""
        now = utils.to_utc_iso(utils.now_utc())
        self.state.update({
            "success": True,
            "success_time": now,
            "success_detail": detail,
            "last_attempt_time": now,
            "last_result": "success",
            "attempt_count": int(self.state.get("attempt_count", 0)) + 1,
            "consecutive_failures": 0,
        })
        logger.info("state 已标记成功：%s", detail)
        return self.state

    def mark_failure(self, reason: str, error: str = "") -> dict[str, Any]:
        """标记一次失败尝试（含 unknown），累加尝试与连续失败计数。"""
        now = utils.to_utc_iso(utils.now_utc())
        self.state.update({
            "last_attempt_time": now,
            "last_result": reason,
            "attempt_count": int(self.state.get("attempt_count", 0)) + 1,
            "consecutive_failures": int(
                self.state.get("consecutive_failures", 0)) + 1,
        })
        if error:
            logger.info("失败原因：%s %s", reason, error)
        return self.state

    def mark_skip(self, reason: str) -> None:
        """记录跳过（不进尝试计数，避免心跳膨胀）。"""
        self.state["last_result"] = f"skipped:{reason}"

    def update_heartbeat(self) -> None:
        """更新心跳时间字段（仅本地，是否 commit 由 should_commit_heartbeat 判定）。"""
        self.state["heartbeat_time"] = utils.to_utc_iso(utils.now_utc())

    def should_commit_heartbeat(self) -> bool:
        """是否到达心跳提交间隔（默认 6 小时，可配置）。

        Returns:
            距上次心跳 commit 超过配置间隔（或从未提交过）时 True。
        """
        last = utils.from_utc_iso(
            str(self.state.get("last_heartbeat_commit", "")))
        interval_h = float(self.cfg.state.heartbeat_commit_hours)
        if last is None:
            return True
        elapsed = (utils.now_utc() - last).total_seconds() / 3600.0
        return elapsed >= interval_h

    def reset(self) -> None:
        """重置本地 state 为初始值（--reset 入口；远程需手动 commit）。"""
        self.state = dict(DEFAULT_STATE)
        self.save()
        logger.info("本地 state 已重置（注意：需手动 push 才能同步远程）")

    # ---- 远程操作（GitHub API） ----

    def _api_headers(self) -> dict[str, str]:
        """构造 GitHub API 请求头（token 不落日志）。"""
        return {
            "Authorization": f"Bearer {self.cfg.github_token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _remote_path(self) -> str:
        """state 文件在仓库内的 POSIX 相对路径。"""
        try:
            return self.path.relative_to(self.cfg.base_dir).as_posix()
        except ValueError:
            # state 文件不在基准目录下（异常配置），退回配置中的相对路径
            return self.cfg.state.state_file

    def commit_state(self, message: str) -> bool:
        """通过 GitHub API PUT contents 提交 state 文件（推荐方式 B）。

        Args:
            message: commit message。

        Returns:
            是否提交成功；失败仅记录日志，不抛异常（不阻塞主流程）。
        """
        if not self.cfg.state.enable_git_commit:
            logger.debug("state git commit 已禁用，跳过")
            return False
        if requests is None:
            logger.warning("requests 未安装，无法提交 state")
            return False
        if not self.cfg.github_token or not self.cfg.github_repository:
            logger.warning("缺少 GITHUB_TOKEN / GITHUB_REPOSITORY，无法提交 state")
            return False

        repo = self.cfg.github_repository
        remote_path = self._remote_path()
        url = f"{GITHUB_API}/repos/{repo}/contents/{remote_path}"

        try:
            # 1. 获取当前文件 SHA（不存在则新建）
            sha: str | None = None
            resp = requests.get(url, headers=self._api_headers(),
                                timeout=15)
            if resp.status_code == 200:
                sha = resp.json().get("sha")
            elif resp.status_code != 404:
                logger.warning("获取远程 state SHA 失败：HTTP %s",
                               resp.status_code)
                return False

            # 2. PUT 新内容
            content = base64.b64encode(
                utils.write_json_to_bytes(self.state)).decode("ascii")
            payload: dict[str, Any] = {
                "message": message,
                "content": content,
            }
            if sha:
                payload["sha"] = sha
            put = requests.put(url, headers=self._api_headers(),
                               json=payload, timeout=15)
            if put.status_code in (200, 201):
                logger.info("state 已提交到仓库：%s", message)
                return True
            logger.warning("state 提交失败：HTTP %s %s",
                           put.status_code, put.text[:200])
            return False
        except Exception as exc:  # noqa: BLE001 网络等任何异常
            logger.warning("state 提交异常（不阻塞主流程）：%s", exc)
            return False

    def disable_workflow(self) -> bool:
        """成功后禁用当前 workflow（主停止方案）。

        workflow 文件名从 GITHUB_WORKFLOW_REF 提取
        （格式：owner/repo/.github/workflows/grab.yml@refs/heads/main）。

        Returns:
            是否禁用成功；失败仅记录日志，由 state 兜底。
        """
        if not self.cfg.state.enable_disable_workflow:
            logger.debug("禁用 workflow 已配置关闭，跳过")
            return False
        if requests is None:
            logger.warning("requests 未安装，无法禁用 workflow")
            return False
        if not self.cfg.github_token or not self.cfg.github_repository:
            logger.warning("缺少 GITHUB_TOKEN / GITHUB_REPOSITORY，无法禁用 workflow")
            return False

        ref = self.cfg.github_workflow_ref
        # 从 ".../.github/workflows/grab.yml@refs/heads/main" 提取 grab.yml
        workflow_file = ""
        if ref:
            head = ref.split("@", 1)[0]
            if "/.github/workflows/" in head:
                workflow_file = head.rsplit("/.github/workflows/", 1)[1]
        if not workflow_file:
            logger.warning("无法从 GITHUB_WORKFLOW_REF 提取 workflow 文件名：%r",
                           utils.sanitize_text(ref))
            return False

        url = (f"{GITHUB_API}/repos/{self.cfg.github_repository}"
               f"/actions/workflows/{workflow_file}/disable")
        try:
            resp = requests.put(url, headers=self._api_headers(), timeout=15)
            if resp.status_code == 204:
                logger.success(  # type: ignore[attr-defined]
                    "Workflow disabled, no more scheduled runs")
                return True
            logger.warning("禁用 workflow 失败：HTTP %s %s",
                           resp.status_code, resp.text[:200])
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("禁用 workflow 异常（不阻塞主流程）：%s", exc)
            return False

    def commit_heartbeat(self) -> bool:
        """提交心跳 commit（防 60 天无活动禁用）。

        message 统一为 ``chore: heartbeat <北京时间> [skip ci]``。
        成功后记录 last_heartbeat_commit。
        """
        cn_time = utils.format_cn(utils.now_utc())
        ok = self.commit_state(f"chore: heartbeat {cn_time} [skip ci]")
        if ok:
            self.state["last_heartbeat_commit"] = utils.to_utc_iso(
                utils.now_utc())
            self.save()
        return ok


# ---- 模块级便捷函数（对应规格 7.5 的函数式签名） ----

def load_state(cfg: Any) -> dict[str, Any]:
    """模块级入口：加载 state（返回字段 dict）。"""
    return StateStore(cfg).load()


def save_state(cfg: Any, state: dict[str, Any]) -> None:
    """模块级入口：保存 state dict 到本地文件。"""
    store = StateStore(cfg)
    store.state = state
    store.save()


def is_already_success(cfg: Any) -> bool:
    """模块级入口：读取 state 判断是否已成功。"""
    store = StateStore(cfg)
    store.load()
    return store.is_already_success()
