import builtins
import importlib
from collections.abc import Mapping, Sequence
from types import ModuleType

import pytest

import main as main_module
from conformance import cli

pytestmark = pytest.mark.unit


def test_main_returns_cli_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    received_argv: list[Sequence[str] | None] = []

    def fake_run(argv: Sequence[str] | None = None) -> int:
        received_argv.append(argv)
        return 3

    monkeypatch.setattr(cli, "run", fake_run)

    exit_code = main_module.main(["config.json"])

    assert exit_code == 3
    assert received_argv == [["config.json"]]


def test_main_import_does_not_load_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,  # noqa: A002 - builtin import hook signature.
        locals: Mapping[str, object] | None = None,  # noqa: A002 - builtin import hook signature.
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "conformance.cli":
            pytest.fail("The entry point must not import the CLI before announcing loading")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    importlib.reload(main_module)


def test_main_announces_loading_before_cli_import(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = builtins.__import__
    announcements: list[str] = []

    def observing_import(
        name: str,
        globals: Mapping[str, object] | None = None,  # noqa: A002 - builtin import hook signature.
        locals: Mapping[str, object] | None = None,  # noqa: A002 - builtin import hook signature.
        fromlist: Sequence[str] | None = (),
        level: int = 0,
    ) -> ModuleType:
        if name == "conformance.cli":
            announcements.append(capsys.readouterr().err)
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", observing_import)
    monkeypatch.setattr(cli, "run", lambda _argv: 2)

    assert main_module.main(["missing.json"]) == 2
    assert announcements == ["[CLI] Loading conformance suite...\n"]
