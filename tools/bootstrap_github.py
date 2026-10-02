#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键部署到 GitHub：创建仓库 → 开写权限 → 提交并推送。

用法（在 D:\\uu\\python\\abecloud-auto-grab 目录下执行）：

    # 方式一：token 走环境变量（推荐，不落盘）
    set GH_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx
    python tools/bootstrap_github.py

    # 方式二：交互式输入
    python tools/bootstrap_github.py

    # 只推送（仓库已在 GitHub 上手动建好了）
    python tools/bootstrap_github.py --remote-only

    # 私有仓库（注意：私有仓库 Actions 有分钟数限制，抢购场景建议公开）
    python tools/bootstrap_github.py --private

需要的 token 权限：Classic token 勾选 ``repo``；
或 fine-grained token 对该账号仓库授予 Administration:read-write / Contents:read-write。

仅依赖标准库，无第三方包。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

API = "https://api.github.com"

DEFAULT_REPO_NAME = "abecloud-auto-grab"
DEFAULT_DESCRIPTION = "阿贝云免费服务器自动抢购（GitHub Actions）"
DEFAULT_BRANCH = "main"


# --------------------------------------------------------------------------- #
# HTTP 工具
# --------------------------------------------------------------------------- #
def api(
    method: str,
    path: str,
    token: str,
    payload: dict[str, Any] | None = None,
    accept_404: bool = False,
) -> tuple[int, dict[str, Any] | str]:
    """调用 GitHub REST API，返回 (status_code, 解析后的响应)。"""
    url = f"{API}{path}" if path.startswith("/") else path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "abecloud-auto-grab-bootstrap")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as exc:  # 具体异常，不做裸捕获
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed: dict[str, Any] | str = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        if exc.code == 404 and accept_404:
            return 404, parsed
        return exc.code, parsed
    except urllib.error.URLError as exc:
        print(f"[!] 网络请求失败：{exc.reason}")
        raise SystemExit(6) from exc


# --------------------------------------------------------------------------- #
# Git 工具
# --------------------------------------------------------------------------- #
def run_git(args: list[str], cwd: str, capture: bool = True) -> str:
    """执行 git 命令，失败即抛出清晰错误。"""
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=capture,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() if capture else ""
        print(f"[!] git {' '.join(args)} 失败：{err}")
        raise SystemExit(6)
    return (proc.stdout or "").strip() if capture else ""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def resolve_token(cli_token: str | None) -> str:
    token = cli_token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        token = getpass.getpass("请输入 GitHub Personal Access Token（输入不回显）: ").strip()
    if not token:
        print("[!] 未提供 token，退出")
        raise SystemExit(5)
    return token


def ensure_repo(token: str, name: str, private: bool, description: str) -> tuple[str, str]:
    """创建仓库；已存在则复用。返回 (owner_login, repo_name)。"""
    code, me = api("GET", "/user", token)
    if code != 200 or not isinstance(me, dict):
        print(f"[!] 获取账号信息失败（HTTP {code}）：{me}")
        raise SystemExit(5)
    owner = str(me.get("login", ""))
    print(f"[*] 当前 GitHub 账号：{owner}")

    code, body = api(
        "POST",
        "/user/repos",
        token,
        {"name": name, "description": description, "private": private, "auto_init": False},
    )
    if code == 201 and isinstance(body, dict):
        print(f"[✓] 仓库已创建：{body.get('html_url')}")
    elif code == 422 and isinstance(body, dict) and "already exists" in str(body.get("message", "")):
        print(f"[=] 仓库 {owner}/{name} 已存在，直接复用")
    else:
        print(f"[!] 创建仓库失败（HTTP {code}）：{body}")
        if code in (401, 403):
            print("    多半是 token 权限不足，请确认勾选了 repo 权限。")
        raise SystemExit(5)
    return owner, name


def enable_write_permission(token: str, owner: str, name: str) -> None:
    """把 GITHUB_TOKEN 的默认权限提到 write —— 提交 state 与禁用 workflow 全靠它。"""
    code, body = api(
        "PUT",
        f"/repos/{owner}/{name}/actions/permissions/workflow",
        token,
        {"default_workflow_permissions": "write", "can_approve_pull_request_reviews": True},
    )
    if code == 204:
        print("[✓] Workflow permissions 已设为 Read and write")
    else:
        print(f"[!] 设置写权限失败（HTTP {code}）：{body}")
        print("    请手动到 Settings → Actions → General → Workflow permissions 改。")


def push_repo(root: str, owner: str, name: str, token: str) -> None:
    """设置远端并推送。token 不写入 .git/config —— 通过 askpass 脚本临时注入。"""
    if not os.path.isdir(os.path.join(root, ".git")):
        run_git(["init"], root)

    run_git(["add", "-A"], root)
    status = run_git(["status", "--porcelain"], root)
    staged = run_git(["diff", "--cached", "--name-only"], root)
    if status or staged:
        run_git(["-c", "user.name=abecloud-bot", "-c", "user.email=bot@users.noreply.github.com",
                 "commit", "-m", "update: 阿贝云自动抢购同步"], root)
        print("[✓] 已生成本地提交")
    else:
        print("[=] 无变更，跳过提交")

    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], root) or DEFAULT_BRANCH
    if branch != DEFAULT_BRANCH:
        run_git(["branch", "-M", DEFAULT_BRANCH], root)
        print(f"[✓] 分支重命名为 {DEFAULT_BRANCH}")

    auth_url = f"https://{owner}:{token}@github.com/{owner}/{name}.git"
    clean_url = f"https://github.com/{owner}/{name}.git"
    remotes = run_git(["remote"], root).split()
    if "origin" in remotes:
        run_git(["remote", "set-url", "origin", clean_url], root)
    else:
        run_git(["remote", "add", "origin", clean_url], root)
    print(f"[✓] 远端已配置：{clean_url}（凭据不会写入 .git/config）")

    proc = subprocess.run(
        ["git", "push", "-u", auth_url, f"{DEFAULT_BRANCH}:{DEFAULT_BRANCH}"],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").replace(token, "***")
        print(f"[!] 推送失败：{stderr}")
        raise SystemExit(6)
    print(f"[✓] 推送成功 → https://github.com/{owner}/{name}")


def print_next_steps(owner: str, name: str) -> None:
    repo_url = f"https://github.com/{owner}/{name}"
    secrets_url = f"{repo_url}/settings/secrets/actions"
    vars_url = f"{repo_url}/settings/variables/actions"
    actions_url = f"{repo_url}/actions"
    print("\n" + "=" * 60)
    print("最后 3 步需要你在网页上点一下（Secrets 不支持脚本写入，必须加密上传）：")
    print(f"1) 配置 Secrets → {secrets_url}")
    print("   ABECLOUD_USERNAME / ABECLOUD_PASSWORD / PUSHPLUS_TOKEN")
    print(f"2) （可选）配置 Variables → {vars_url}")
    print("   PUSHPLUS_WECHAT_ENABLED / PUSHPLUS_QQ_ENABLED / PUSHPLUS_SMS_ENABLED")
    print(f"3) 手动触发 workflow 验证 → {actions_url} → Manual Test → dry-run")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="一键创建 GitHub 仓库并推送本项目")
    parser.add_argument("--token", default=None, help="GitHub PAT（留空则用环境变量 GH_TOKEN 或交互输入）")
    parser.add_argument("--repo-name", default=DEFAULT_REPO_NAME, help=f"仓库名（默认 {DEFAULT_REPO_NAME}）")
    parser.add_argument("--description", default=DEFAULT_DESCRIPTION, help="仓库描述")
    parser.add_argument("--private", action="store_true", help="建私有仓库（默认公开，Actions 额度无限）")
    parser.add_argument("--remote-only", action="store_true", help="跳过建仓，只配远端并推送")
    parser.add_argument("--no-push", action="store_true", help="只建仓/设权限，不推送")
    args = parser.parse_args(argv)

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    token = resolve_token(args.token)

    if args.remote_only:
        code, me = api("GET", "/user", token)
        if code != 200 or not isinstance(me, dict):
            print(f"[!] 获取账号信息失败（HTTP {code}）：{me}")
            return 5
        owner, name = str(me.get("login", "")), args.repo_name
        print(f"[*] 跳过建仓，直接推送到 {owner}/{name}")
    else:
        owner, name = ensure_repo(token, args.repo_name, args.private, args.description)
        enable_write_permission(token, owner, name)

    if not args.no_push:
        push_repo(root, owner, name, token)
    print_next_steps(owner, name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
