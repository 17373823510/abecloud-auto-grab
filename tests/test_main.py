"""main.py 单元测试：CLI 参数解析与退出码常量。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main  # noqa: E402


def test_parser_all_flags() -> None:
    parser = main.build_parser()
    args = parser.parse_args([
        "--config", "my.yaml", "--dry-run", "--check-only",
        "--test-notify", "--force", "--reset", "--verbose",
    ])
    assert args.config == "my.yaml"
    assert args.dry_run is True
    assert args.check_only is True
    assert args.test_notify is True
    assert args.force is True
    assert args.reset is True
    assert args.verbose is True


def test_parser_defaults() -> None:
    args = main.build_parser().parse_args([])
    assert args.config is None
    assert args.dry_run is False
    assert args.check_only is False
    assert args.force is False
    assert args.reset is False


def test_exit_code_constants() -> None:
    assert main.EXIT_OK == 0
    assert main.EXIT_LOGIN_FAILED == 4
    assert main.EXIT_CONFIG_ERROR == 5
    assert main.EXIT_UNKNOWN == 6


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main.build_parser().parse_args(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert main.VERSION in out


def test_run_missing_credentials_returns_5(
        monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ABECLOUD_USERNAME", raising=False)
    monkeypatch.delenv("ABECLOUD_PASSWORD", raising=False)
    args = main.build_parser().parse_args(["--config", str(tmp_path / "none.yaml")])
    assert main.run(args) == main.EXIT_CONFIG_ERROR
