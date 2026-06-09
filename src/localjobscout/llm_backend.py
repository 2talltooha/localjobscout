"""LLM client backend selector.

Two backends provide the small slice of the Anthropic SDK that this project
uses — ``client.messages.create(...)`` returning an object whose ``.content``
is a list of text blocks:

1. **API backend** (default): the real ``anthropic.Anthropic`` client, billed
   per token against ``ANTHROPIC_API_KEY``.
2. **CLI backend**: shells out to the ``claude`` CLI in headless print mode
   (``claude -p``), billed against the logged-in Claude subscription. No API
   key required. Enabled by setting ``LOCALJOBSCOUT_USE_CLI=1``.

Callers use ``make_client(api_key)`` instead of ``anthropic.Anthropic(...)`` and
gate on ``use_cli() or api_key`` instead of ``api_key`` alone. Everything else
(``.messages.create``, ``.content``, ``block.type``/``block.text``) is identical
across both backends, so the rest of the codebase is unchanged.

The CLI backend isolates each invocation with ``--setting-sources ""`` (skip
user/project/local settings, so no hooks fire) and ``--system-prompt`` (fully
replace the default system prompt, so project CLAUDE.md / memory never leak into
the response). A single outer ```` ``` ```` code fence wrapping the whole reply
is stripped so JSON callers parse cleanly.
"""

from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_TRUTHY = {"1", "true", "yes", "on"}


def use_cli() -> bool:
    """Return True when the ``claude`` CLI (subscription) backend is enabled."""
    return os.environ.get("LOCALJOBSCOUT_USE_CLI", "").strip().lower() in _TRUTHY


def make_client(api_key: str) -> Any:
    """Return an LLM client.

    CLI backend when ``LOCALJOBSCOUT_USE_CLI`` is set, else the real Anthropic
    SDK client. Raises ImportError (CLI off) if ``anthropic`` is not installed —
    callers already guard the import, so they catch it.
    """
    if use_cli():
        return _CliClient()
    import anthropic

    return anthropic.Anthropic(api_key=api_key)


# ─── CLI backend ────────────────────────────────────────────────────────────


@dataclass
class _TextBlock:
    text: str
    type: str = "text"


@dataclass
class _Response:
    content: list[_TextBlock] = field(default_factory=list)


def _model_alias(model: str) -> str | None:
    """Map an Anthropic model id to a CLI alias, or env override.

    ``LOCALJOBSCOUT_CLI_MODEL`` wins. Otherwise infer opus/haiku from the id;
    default to None (let the CLI use the subscription default — sonnet)."""
    override = os.environ.get("LOCALJOBSCOUT_CLI_MODEL", "").strip()
    if override:
        return override
    low = model.lower()
    if "opus" in low:
        return "opus"
    if "haiku" in low:
        return "haiku"
    if "sonnet" in low:
        return "sonnet"
    return None


def _flatten_content(content: Any) -> str:
    """Flatten a message ``content`` (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, dict):
            text = block.get("text")
            if text:
                parts.append(str(text))
        elif isinstance(block, str):
            parts.append(block)
    return "\n\n".join(parts)


def _strip_outer_fence(text: str) -> str:
    """Strip a single ```` ``` ```` fence wrapping the entire reply.

    Only strips when the whole output is fenced (common for JSON replies); leaves
    inner fences in markdown output untouched.
    """
    stripped = text.strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2:
        return text
    # Drop opening fence line (``` or ```json) and closing fence line.
    return "\n".join(lines[1:-1]).strip()


class _Messages:
    def create(
        self,
        *,
        model: str,
        max_tokens: int | None = None,  # noqa: ARG002 — CLI controls length
        system: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        **_: Any,
    ) -> _Response:
        prompt = "\n\n".join(
            _flatten_content(m.get("content", ""))
            for m in (messages or [])
            if m.get("role") == "user"
        )
        cmd = [
            "claude",
            "-p",
            "--output-format",
            "text",
            "--setting-sources",
            "",
        ]
        cmd += ["--system-prompt", system or "You are a precise assistant."]
        alias = _model_alias(model)
        if alias:
            cmd += ["--model", alias]

        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=180,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"claude CLI failed (exit {proc.returncode}): "
                f"{(proc.stderr or proc.stdout).strip()[:300]}"
            )
        return _Response(content=[_TextBlock(text=_strip_outer_fence(proc.stdout))])


class _CliClient:
    """Subset of ``anthropic.Anthropic`` backed by the ``claude`` CLI."""

    def __init__(self) -> None:
        self.messages = _Messages()
