"""``claude -p`` を起動する薄いラッパ.

- 本プロジェクトコンテナ内で実行する ``run_claude_local``
- 対象リポコンテナ内で実行する ``run_claude_in_container``

出力は JSON モード前提でパースし、本文テキストを抽出する。
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from knowl._proc import run_checked
from knowl.config import ContainerConfig
from knowl.container import exec_in_container

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT = 60.0 * 60.0 * 2.0  # 2h


class ClaudeError(RuntimeError):
    """claude CLI 起動またはレスポンス解釈失敗."""

    def __init__(self, message: str, *, limit_reached: bool = False) -> None:
        super().__init__(message)
        self.limit_reached = limit_reached


@dataclass(frozen=True, slots=True)
class ClaudeResult:
    text: str
    payload: dict[str, Any]


_LIMIT_HINTS = (
    "usage limit",
    "rate limit",
    "weekly limit",
    "5-hour limit",
    "five-hour limit",
    "quota exceeded",
)


def _looks_like_limit(message: str) -> bool:
    lowered = message.lower()
    return any(hint in lowered for hint in _LIMIT_HINTS)


def extract_text(payload: dict[str, Any]) -> str:
    """``claude -p --output-format json`` 出力からアシスタント本文を抽出する."""
    if isinstance(payload.get("result"), str) and payload.get("type") == "result":
        return str(payload["result"])
    messages = payload.get("messages")
    if isinstance(messages, list):
        for msg in reversed(messages):
            if not isinstance(msg, dict) or msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                texts: list[str] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str):
                            texts.append(text)
                if texts:
                    return "".join(texts)
    raise ClaudeError("could not extract assistant text from claude output")


def _build_argv(
    prompt: str,
    *,
    model: str,
    extra_args: Sequence[str],
) -> list[str]:
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        *extra_args,
        prompt,
    ]


def _parse_stdout(stdout: str) -> dict[str, Any]:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError(f"claude returned non-JSON output: {exc}") from exc
    if not isinstance(data, dict):
        raise ClaudeError("claude output must be a JSON object")
    if data.get("is_error"):
        msg = str(data.get("result") or data.get("error") or "claude reported error")
        raise ClaudeError(msg, limit_reached=_looks_like_limit(msg))
    return data


def run_claude_local(
    prompt: str,
    *,
    model: str = DEFAULT_MODEL,
    extra_args: Sequence[str] = (),
    timeout: float = DEFAULT_TIMEOUT,
) -> ClaudeResult:
    """本プロジェクトコンテナ(ローカル)で ``claude -p`` を起動する."""
    cmd = _build_argv(prompt, model=model, extra_args=extra_args)
    result = run_checked(
        cmd,
        error_cls=ClaudeError,
        label=f"claude -p (timeout={timeout}s)",
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise ClaudeError(
            f"claude -p exited {result.returncode}: {stderr}",
            limit_reached=_looks_like_limit(stderr),
        )
    payload = _parse_stdout(result.stdout)
    return ClaudeResult(text=extract_text(payload), payload=payload)


def run_claude_in_container(
    container: ContainerConfig,
    prompt: str,
    *,
    workdir: str,
    model: str = DEFAULT_MODEL,
    extra_args: Sequence[str] = (),
    env: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ClaudeResult:
    """対象リポコンテナ内で ``claude -p`` を起動する."""
    argv = _build_argv(prompt, model=model, extra_args=extra_args)
    exec_result = exec_in_container(
        container,
        argv,
        workdir=workdir,
        env=env,
        timeout=timeout,
    )
    if exec_result.returncode != 0:
        stderr = exec_result.stderr.strip() or exec_result.stdout.strip()
        raise ClaudeError(
            f"claude -p in {container.name} exited {exec_result.returncode}: {stderr}",
            limit_reached=_looks_like_limit(stderr),
        )
    payload = _parse_stdout(exec_result.stdout)
    return ClaudeResult(text=extract_text(payload), payload=payload)
