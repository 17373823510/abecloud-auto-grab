"""AbeCloud Auto-Grab 程序入口。

主流程（规格 7.9）：
    读取 state → 已成功则跳过 → 工作窗口检查 → 轻量可抢检查
    → Playwright 登录 → 尝试抢购 → 成功则通知 + state + 禁用 workflow
退出码：0 成功/跳过/正常失败；4 登录失败；5 配置错误；6 未知错误。
除配置错误外均返回 0，避免 Actions 把"没抢到"标红。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# 保证以任意工作目录运行时都能 import modules
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modules import grabber, notify, state as state_mod, utils  # noqa: E402
from modules.auth import login  # noqa: E402
from modules.config import Config, ConfigError, load_config, validate_config  # noqa: E402
from modules.logger import setup_logger  # noqa: E402

VERSION = "1.0.0"

logger = logging.getLogger("main")

# 退出码常量（集中定义便于测试断言）
EXIT_OK = 0
EXIT_LOGIN_FAILED = 4
EXIT_CONFIG_ERROR = 5
EXIT_UNKNOWN = 6


def build_parser() -> argparse.ArgumentParser:
    """构造命令行解析器（独立函数便于测试）。"""
    parser = argparse.ArgumentParser(
        prog="abecloud-auto-grab",
        description="阿贝云免费服务器自动抢购（GitHub Actions 版）",
    )
    parser.add_argument("--config", metavar="PATH", default=None,
                        help="可选配置文件路径（默认 ./config.yaml）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只登录和检查，不实际抢购")
    parser.add_argument("--check-only", action="store_true",
                        help="只做轻量检查，不启动浏览器")
    parser.add_argument("--test-notify", action="store_true",
                        help="只发送测试通知后退出")
    parser.add_argument("--force", action="store_true",
                        help="忽略已成功标记，强制执行（调试用）")
    parser.add_argument("--reset", action="store_true",
                        help="重置本地 state 为初始值（需手动 push 同步远程）")
    parser.add_argument("--verbose", action="store_true",
                        help="DEBUG 日志")
    parser.add_argument("--version", action="version",
                        version=f"%(prog)s {VERSION}")
    return parser


def run(args: argparse.Namespace) -> int:
    """执行主流程，返回退出码（全部异常在此收敛）。"""
    cfg = load_config(args.config)
    level = logging.DEBUG if args.verbose else logging.INFO
    setup_logger(cfg.output.log_dir, level, cfg.abecloud_username)

    # --reset：重置本地 state 后退出
    if args.reset:
        store = state_mod.StateStore(cfg)
        store.load()
        store.reset()
        logger.info("state 已重置，请手动 commit & push 后在 Actions 页面启用 workflow")
        return EXIT_OK

    # --test-notify：向启用渠道发测试消息后退出
    if args.test_notify:
        results = notify.send_test(cfg)
        ok = all(r["ok"] for r in results) if results else False
        logger.info("测试通知完成：%s", results)
        return EXIT_OK if ok else EXIT_CONFIG_ERROR

    # ---- 1. 配置校验 ----
    valid, errors = validate_config(cfg)
    if not valid:
        for err in errors:
            logger.error("配置错误：%s", err)
        return EXIT_CONFIG_ERROR

    store = state_mod.StateStore(cfg)
    store.load()  # 损坏/缺失自动按未成功处理

    # ---- 2. 已成功兜底检查 ----
    if store.is_already_success() and not args.force:
        logger.skip(  # type: ignore[attr-defined]
            "state 标记已成功（%s），本次跳过。如需重抢请用 --reset 或手动编辑 state 文件",
            store.state.get("success_time", ""))
        return EXIT_OK

    # ---- 3. 工作时间窗口 ----
    if not utils.is_in_work_window(cfg):
        logger.skip("当前不在工作时间窗口内，跳过本次运行")  # type: ignore[attr-defined]
        store.mark_skip("work_window")
        store.save()
        return EXIT_OK

    # ---- 4. 轻量可抢检查（不启动浏览器） ----
    availability = grabber.check_availability(cfg)
    if not availability["available"]:
        logger.skip("暂不可抢：%s，等待下次调度",  # type: ignore[attr-defined]
                    availability["reason"])
        _maybe_heartbeat(store, cfg)
        return EXIT_OK

    if args.check_only:
        logger.info("--check-only：轻量检查判定可抢，按用户要求不启动浏览器")
        return EXIT_OK

    # ---- 5. 登录 ----
    logger.info("开始登录阿贝云…")
    login_result = login(cfg.abecloud_username, cfg.abecloud_password, cfg)
    if not login_result.get("success"):
        reason = login_result.get("reason", "unknown")
        error = login_result.get("error", "")
        logger.warning("登录失败：%s %s", reason, error)
        store.mark_failure(f"login:{reason}", error)
        store.save()
        _maybe_heartbeat(store, cfg)
        # 登录失败按退出码 4；但为避免 Actions 标红，规格 7.9 说明"返回 4"，
        # 同时 9.3 要求除配置错误外均 0 —— 折中：退出码 4（可见问题），
        # workflow 中用 || true 兜底不标红？规格 9.1 明确"不配置 continue-on-error，
        # main.py 除配置错误外均返回 0"。因此这里返回 0 并记录。
        # （保留 EXIT_LOGIN_FAILED 常量供本地调试 --verbose 人工判断）
        _notify_login_failure_if_needed(store, cfg, reason)
        return EXIT_OK

    session = login_result["session"]
    try:
        # ---- 6. dry-run：只到登录+检查为止 ----
        if args.dry_run:
            avail2 = grabber.check_availability(
                cfg, cookies=login_result.get("cookies"))
            logger.info("--dry-run：登录成功，可抢性=%s（不执行抢购）",
                        avail2)
            store.mark_skip("dry_run")
            store.save()
            return EXIT_OK

        # ---- 7. 实际抢购 ----
        logger.info("开始尝试抢购…")
        result = grabber.attempt_grab(session.context, cfg)

        if result.get("success"):
            detail = result.get("detail", "")
            logger.success("抢购成功！%s", detail)  # type: ignore[attr-defined]
            # 顺序：通知 → state 标记+提交 → 禁用 workflow（双保险）
            notify.send_success(detail, cfg, store.state)
            store.mark_success(detail)
            store.save()
            store.commit_state(f"feat: grab success - {detail}")
            store.disable_workflow()
            return EXIT_OK

        reason = result.get("reason", "unknown")
        detail = result.get("detail", "")
        logger.info("本次未抢到：%s %s", reason, detail)
        store.mark_failure(reason, detail)
        store.save()
        _maybe_heartbeat(store, cfg)
        _notify_failure_if_needed(store, cfg, reason, detail)
        return EXIT_OK
    finally:
        # 浏览器用完即关（成功/失败都释放）
        try:
            session.__exit__(None, None, None)
        except Exception as exc:  # noqa: BLE001
            logger.warning("关闭浏览器异常：%s", exc)
        # 清理旧截图
        utils.clean_old_screenshots(
            cfg.base_dir / cfg.output.screenshot_dir,
            cfg.output.keep_screenshots)


def _maybe_heartbeat(store: state_mod.StateStore, cfg: Config) -> None:
    """未成功时的心跳：更新本地 heartbeat_time，达到间隔则 commit。"""
    if not cfg.state.heartbeat_on_failure:
        return
    store.update_heartbeat()
    store.save()
    if store.should_commit_heartbeat():
        if store.commit_heartbeat():
            logger.debug("心跳已提交")
    else:
        logger.debug("未到心跳提交间隔，仅更新本地 heartbeat_time")


def _notify_login_failure_if_needed(store: state_mod.StateStore,
                                     cfg: Config, reason: str) -> None:
    """登录失败通知策略：验证码与凭据错误立即告警；其他按连续失败阈值。"""
    if reason == "captcha":
        # 验证码拦截是重要事件，立即通知（短信标题严格按规范）
        pp = cfg.pushplus
        if pp.sms_enabled:
            notify.send_raw(notify.SMS_TITLE_CAPTCHA,
                            "登录被验证码拦截，请人工处理", "sms", cfg)
        if pp.wechat_enabled or pp.qq_enabled:
            notify.send_failure("需要验证码", "登录环节被验证码拦截", cfg)
    elif reason == "wrong_credentials":
        notify.send_failure("登录失败", "账号或密码错误，请检查 Secrets", cfg)
    else:
        _notify_failure_if_needed(store, cfg, f"login:{reason}", "")


def _notify_failure_if_needed(store: state_mod.StateStore, cfg: Config,
                              reason: str, detail: str) -> None:
    """连续失败达到阈值（默认 12 次 ≈ 2 小时）才发失败通知，避免噪音。"""
    threshold = cfg.pushplus.failure_notify_threshold
    consecutive = int(store.state.get("consecutive_failures", 0))
    if consecutive > 0 and consecutive % threshold == 0:
        logger.info("连续失败 %d 次，发送失败通知", consecutive)
        notify.send_failure(reason, detail, cfg)


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析参数并执行，未知异常收敛为退出码 6。"""
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except ConfigError as exc:
        # 日志系统可能尚未初始化（配置加载失败），直接 stderr 输出
        print(f"[CONFIG ERROR] {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR
    except KeyboardInterrupt:
        print("用户中断", file=sys.stderr)
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 未知错误必须兜底（Actions 不标红）
        logging.getLogger("main").exception("未知错误：%s", exc)
        # 规格 7.9：除配置错误外均返回 0，避免 Actions 标红；
        # EXIT_UNKNOWN 常量仅供本地诊断参考（可用 --verbose 查看完整堆栈）
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
