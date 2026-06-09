"""Tests for the LLM client backend selector (API vs claude CLI subscription)."""

from __future__ import annotations

import subprocess
import sys
import types

import pytest

from localjobscout import llm_backend

# ─── use_cli ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "value,expected",
    [("1", True), ("true", True), ("YES", True), ("on", True),
     ("0", False), ("false", False), ("", False), ("nope", False)],
)
def test_use_cli_env_parsing(
    monkeypatch: pytest.MonkeyPatch, value: str, expected: bool
) -> None:
    monkeypatch.setenv("LOCALJOBSCOUT_USE_CLI", value)
    assert llm_backend.use_cli() is expected


def test_use_cli_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCALJOBSCOUT_USE_CLI", raising=False)
    assert llm_backend.use_cli() is False


# ─── make_client routing ─────────────────────────────────────────────────────


def test_make_client_returns_api_client_when_cli_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOCALJOBSCOUT_USE_CLI", raising=False)

    captured = {}
    fake = types.ModuleType("anthropic")

    class _Client:
        def __init__(self, api_key: str) -> None:
            captured["api_key"] = api_key

    fake.Anthropic = _Client  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)

    client = llm_backend.make_client("secret-key")
    assert isinstance(client, _Client)
    assert captured["api_key"] == "secret-key"


def test_make_client_returns_cli_client_when_cli_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LOCALJOBSCOUT_USE_CLI", "1")
    client = llm_backend.make_client("ignored")
    assert isinstance(client, llm_backend._CliClient)


# ─── helpers ─────────────────────────────────────────────────────────────────


def test_flatten_content_str() -> None:
    assert llm_backend._flatten_content("hello") == "hello"


def test_flatten_content_blocks() -> None:
    blocks = [
        {"type": "text", "text": "a", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "b"},
    ]
    assert llm_backend._flatten_content(blocks) == "a\n\nb"


def test_strip_outer_fence_json() -> None:
    assert llm_backend._strip_outer_fence('```json\n{"x": 1}\n```') == '{"x": 1}'


def test_strip_outer_fence_plain_fence() -> None:
    assert llm_backend._strip_outer_fence("```\nhi\n```") == "hi"


def test_strip_outer_fence_leaves_unfenced() -> None:
    assert llm_backend._strip_outer_fence("no fences here") == "no fences here"


def test_strip_outer_fence_leaves_inner_fences() -> None:
    # Markdown body that merely contains a code block must not be mangled.
    text = "Here is code:\n```python\nx = 1\n```\nDone."
    assert llm_backend._strip_outer_fence(text) == text


@pytest.mark.parametrize(
    "model,override,expected",
    [
        ("claude-haiku-4-5-20251001", None, "haiku"),
        ("claude-opus-4-8", None, "opus"),
        ("claude-sonnet-4-6", None, "sonnet"),
        ("some-other-model", None, None),
        ("claude-haiku-4-5", "opus", "opus"),
    ],
)
def test_model_alias(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    override: str | None,
    expected: str | None,
) -> None:
    if override:
        monkeypatch.setenv("LOCALJOBSCOUT_CLI_MODEL", override)
    else:
        monkeypatch.delenv("LOCALJOBSCOUT_CLI_MODEL", raising=False)
    assert llm_backend._model_alias(model) == expected


# ─── _CliClient.messages.create (subprocess mocked) ──────────────────────────


def test_cli_create_builds_command_and_parses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {}

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls["cmd"] = cmd
        calls["input"] = kwargs.get("input")
        out = '```json\n{"ok": true}\n```'
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.delenv("LOCALJOBSCOUT_CLI_MODEL", raising=False)

    client = llm_backend._CliClient()
    resp = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=120,
        system="SYS",
        messages=[{"role": "user", "content": "hello"}],
    )

    assert resp.content[0].text == '{"ok": true}'
    assert resp.content[0].type == "text"
    # Isolation flags present, system prompt passed, haiku alias selected.
    assert "--setting-sources" in calls["cmd"]
    assert "--system-prompt" in calls["cmd"]
    assert "SYS" in calls["cmd"]
    assert "--model" in calls["cmd"]
    assert "haiku" in calls["cmd"]
    assert calls["input"] == "hello"


def test_cli_create_raises_on_nonzero_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Not logged in")

    monkeypatch.setattr(subprocess, "run", fake_run)
    client = llm_backend._CliClient()
    with pytest.raises(RuntimeError, match="Not logged in"):
        client.messages.create(
            model="m", messages=[{"role": "user", "content": "x"}]
        )
