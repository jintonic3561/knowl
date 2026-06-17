#!/usr/bin/env python3
"""UserPromptSubmit hook: ブランチ別に raw.md へプロンプトを追記する。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

MODEL_ALIASES = ("opus", "sonnet", "haiku")


def normalize_model(model: str) -> str:
    """モデル ID を短縮名に正規化。該当しなければ原文をそのまま返す。"""
    lowered = model.lower()
    for alias in MODEL_ALIASES:
        if alias in lowered:
            return alias
    return model or "unknown"


def model_from_transcript(transcript_path: Path) -> str:
    """transcript JSONL を末尾から走査し model を抽出。"""
    if not transcript_path.exists():
        return "unknown"
    try:
        lines = transcript_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        message = entry.get("message")
        if isinstance(message, dict) and (model := message.get("model")):
            return normalize_model(model)
    return "unknown"


def git_output(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=True
        )
        return out.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0

    prompt = data.get("prompt", "")
    if not prompt:
        return 0

    transcript_path = Path(data.get("transcript_path") or "")
    raw_model = data.get("model", "")
    model = (
        normalize_model(raw_model)
        if raw_model
        else model_from_transcript(transcript_path)
    )

    session = transcript_path.stem or data.get("session_id", "unknown")
    branch = git_output("rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    repo_root = Path(git_output("rev-parse", "--show-toplevel") or Path.cwd())

    log_file = repo_root / ".logs" / "prompts" / branch / "raw.md"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    footer = f"[model: {model}, session: {session[:8]}]"
    has_content = log_file.exists() and log_file.stat().st_size > 0
    separator = "\n---\n\n" if has_content else ""

    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"{separator}{prompt}\n\n{footer}\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
