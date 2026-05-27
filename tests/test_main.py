from collections.abc import Sequence

import pytest

import main as main_module


@pytest.mark.unit
def test_main_returns_cli_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    received_argv: list[Sequence[str] | None] = []

    def fake_run(argv: Sequence[str] | None = None) -> int:
        received_argv.append(argv)
        return 3

    monkeypatch.setattr(main_module, "run", fake_run)

    exit_code = main_module.main(["config/model-bank-example.json"])

    assert exit_code == 3
    assert received_argv == [["config/model-bank-example.json"]]
