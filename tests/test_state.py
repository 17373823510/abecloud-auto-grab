"""状态模块单元测试：读写、成功/失败标记、损坏容错、心跳判定。"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import state as state_mod  # noqa: E402
from modules import utils  # noqa: E402


def make_cfg(tmp_path: Path, **state_overrides: object) -> SimpleNamespace:
    """构造最小 Config 替身（StateStore 只用到这几个字段）。"""
    st = {"state_file": "state/grab_state.json",
          "enable_git_commit": True,
          "enable_disable_workflow": True,
          "heartbeat_on_failure": True,
          "heartbeat_commit_hours": 6.0}
    st.update(state_overrides)
    return SimpleNamespace(base_dir=tmp_path, state=SimpleNamespace(**st),
                           github_token="t", github_repository="o/r",
                           github_workflow_ref="")


def test_load_missing_file_defaults(tmp_path: Path) -> None:
    store = state_mod.StateStore(make_cfg(tmp_path))
    state = store.load()
    assert state["success"] is False
    assert state["attempt_count"] == 0


def test_load_corrupt_json_treated_as_fresh(tmp_path: Path) -> None:
    p = tmp_path / "state" / "grab_state.json"
    p.parent.mkdir(parents=True)
    p.write_text("{ not valid json !!", encoding="utf-8")
    store = state_mod.StateStore(make_cfg(tmp_path))
    state = store.load()
    assert state["success"] is False  # 损坏按未成功处理


def test_load_non_mapping_treated_as_fresh(tmp_path: Path) -> None:
    p = tmp_path / "state" / "grab_state.json"
    p.parent.mkdir(parents=True)
    p.write_text("[1, 2, 3]", encoding="utf-8")
    store = state_mod.StateStore(make_cfg(tmp_path))
    assert store.load()["success"] is False


def test_mark_success_and_roundtrip(tmp_path: Path) -> None:
    store = state_mod.StateStore(make_cfg(tmp_path))
    store.load()
    store.mark_success("免费套餐 | 实例 i-123")
    store.save()

    reloaded = state_mod.StateStore(make_cfg(tmp_path))
    reloaded.load()
    assert reloaded.is_already_success() is True
    assert reloaded.state["success_detail"] == "免费套餐 | 实例 i-123"
    assert reloaded.state["success_time"]
    assert reloaded.state["consecutive_failures"] == 0


def test_mark_failure_accumulates(tmp_path: Path) -> None:
    store = state_mod.StateStore(make_cfg(tmp_path))
    store.load()
    store.mark_failure("sold_out", "已抢完")
    store.mark_failure("sold_out", "已抢完")
    assert store.state["attempt_count"] == 2
    assert store.state["consecutive_failures"] == 2
    assert store.state["last_result"] == "sold_out"


def test_mark_success_resets_failure_streak(tmp_path: Path) -> None:
    store = state_mod.StateStore(make_cfg(tmp_path))
    store.load()
    store.mark_failure("sold_out")
    store.mark_failure("sold_out")
    store.mark_success("ok")
    assert store.state["consecutive_failures"] == 0


def test_heartbeat_interval(tmp_path: Path) -> None:
    from datetime import timedelta  # noqa: PLC0415

    store = state_mod.StateStore(make_cfg(tmp_path))
    store.load()
    # 从未提交过心跳 → 应提交
    assert store.should_commit_heartbeat() is True

    # 模拟 3 小时前提交过 → 6 小时间隔内不应提交
    store.state["last_heartbeat_commit"] = utils.to_utc_iso(
        utils.now_utc() - timedelta(hours=3))
    assert store.should_commit_heartbeat() is False

    # 7 小时前提交过 → 应再次提交
    store.state["last_heartbeat_commit"] = utils.to_utc_iso(
        utils.now_utc() - timedelta(hours=7))
    assert store.should_commit_heartbeat() is True


def test_disable_workflow_disabled_by_config(tmp_path: Path) -> None:
    cfg = make_cfg(tmp_path, enable_disable_workflow=False)
    store = state_mod.StateStore(cfg)
    assert store.disable_workflow() is False  # 配置关闭，直接跳过


def test_module_level_helpers(tmp_path: Path) -> None:
    state = {"success": False, "attempt_count": 0}
    state_mod.save_state(make_cfg(tmp_path), dict(state))
    loaded = state_mod.load_state(make_cfg(tmp_path))
    assert loaded["success"] is False
    assert state_mod.is_already_success(make_cfg(tmp_path)) is False
