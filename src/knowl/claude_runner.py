"""``claude -p`` を起動する薄いラッパ.

- 本プロジェクトコンテナ内で実行する ``run_claude_local``
- 対象リポコンテナ内で実行する ``run_claude_in_container``

出力は JSON モード前提でパースし、本文テキストを抽出する。
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from knowl.config import ContainerConfig
from knowl.container import exec_in_container
from knowl.gate import evaluate_gate
from knowl.usage import UsageSnapshot

DEFAULT_MODEL = "claude-opus-4-7"
DEFAULT_TIMEOUT = 60.0 * 60.0 * 2.0  # 2h


class ClaudeError(RuntimeError):
    """claude CLI 起動またはレスポンス解釈失敗."""

    def __init__(
        self,
        message: str,
        *,
        limit_reached: bool = False,
        usage: UsageSnapshot | None = None,
    ) -> None:
        super().__init__(message)
        self.limit_reached = limit_reached
        # ClaudeError 発生時点の usage snapshot を任意で添える。 stderr の文言マッチが
        # 外れた (= ``_LIMIT_HINTS`` が CLI 仕様変更で当たらなくなった) 場合の保険として、
        # 呼び出し側が ``escalate_limit_reached`` で残量を再判定するために使う。
        self.usage: UsageSnapshot | None = usage


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


def escalate_limit_reached(
    exc: ClaudeError,
    snapshot: UsageSnapshot,
    *,
    session_threshold: float,
    weekly_threshold: float,
) -> None:
    """``ClaudeError`` の ``limit_reached`` を usage snapshot で補強する.

    ``_LIMIT_HINTS`` は CLI stderr の文言ハードコードに依存しており、文言が変わると
    ``limit_reached=False`` のまま次サイクルでも同じエラーで no-op が続くリスクがある。
    その保険として、 5h / 週次のいずれかの残量が閾値割れであれば ``limit_reached=True``
    に昇格させ、 cycle/adhoc 側で limit alert として扱えるようにする。

    - ``exc.usage`` が未設定なら ``snapshot`` を後付けする。 既に添えられている場合
      (将来 ``run_task`` 等が Claude 実行直前/直後に取り直した最新 snapshot を
      ``ClaudeError(..., usage=...)`` に詰める経路を想定) はそちらを尊重する。
      現 PR ではその経路はまだ無く、 cycle/adhoc 冒頭の snapshot が常に渡る。
    - 既に ``limit_reached=True`` (stderr ヒントマッチで判定済み) なら何もしない。
    - 閾値判定は ``gate.evaluate_gate`` を再利用するので、 gate ロジックを変えれば
      escalation 側も自動追随する。 閾値ちょうど (= gate 通過ライン) は False のまま。
    """
    if exc.usage is None:
        exc.usage = snapshot
    if exc.limit_reached:
        return
    decision = evaluate_gate(
        exc.usage,
        session_threshold=session_threshold,
        weekly_threshold=weekly_threshold,
    )
    if not decision.allowed:
        exc.limit_reached = True


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
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise ClaudeError(f"claude -p timed out after {timeout}s") from exc
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
