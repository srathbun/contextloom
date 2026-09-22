"""Tests for the command-line interface."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextloom.cli import main


def test_init_then_status_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    capsys.readouterr()  # clear init output
    assert main(["status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == 1
    assert payload["counts"]["files"] == 0


def test_status_outside_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["status"]) == 1
    assert "init" in capsys.readouterr().err


def test_config_set_get_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert main(["config", "set", "fuzzy_level", "strict"]) == 0
    capsys.readouterr()  # clear init + set output
    assert main(["config", "get", "fuzzy_level", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"fuzzy_level": "strict"}


def test_config_set_rejects_invalid(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert main(["config", "set", "fuzzy_level", "nope"]) == 1
    assert "fuzzy_level" in capsys.readouterr().err
