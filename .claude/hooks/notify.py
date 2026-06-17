#!/usr/bin/env python3
"""Slack notification hook for Claude Code / Codex.

Claude Code は env (CLAUDE_CODE_HOOK_EVENT) でイベント種別を渡す。
Codex は stdin JSON で hook_event_name / tool_name を渡す。両方に対応する。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SLACK_API = "https://slack.com/api/chat.postMessage"
DEFAULT_CHANNEL = "#claude-code"

# (event, codex_tool) → (icon, status label)
STATUS_PRESETS: dict[tuple[str, str], tuple[str, str]] = {
    ("PreToolUse", "request_user_input"): ("❓", "質問待ち"),
    ("PreToolUse", "request_permissions"): ("⏸️", "承認待ち"),
    ("Notification", ""): ("⏸️", "承認待ち"),
    ("PermissionRequest", ""): ("⏸️", "承認待ち"),
    ("Stop", ""): ("✅", "タスク完了"),
}


def load_dotenv(path: Path) -> None:
    """.env を環境変数にロード（既存値は上書きしない）。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def read_stdin_json() -> dict:
    if sys.stdin.isatty():
        return {}
    try:
        raw = sys.stdin.read()
    except OSError:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        return {}


def git_branch() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() or "N/A"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "N/A"


def resolve_event(env_event: str | None, stdin_data: dict) -> tuple[str, str]:
    """(event_name, codex_tool_name) を返す。"""
    if env_event:
        return env_event, ""
    event = stdin_data.get("hook_event_name") or "Notification"
    codex_tool = (stdin_data.get("tool_name") or "").removeprefix("functions.")
    return event, codex_tool


def build_text(event: str, codex_tool: str, cwd: Path) -> str:
    icon, status = STATUS_PRESETS.get((event, codex_tool)) or STATUS_PRESETS.get(
        (event, ""), ("ℹ️", event)
    )
    return f"{icon} *{status}* — `{cwd.name}@{git_branch()}`"


def post_to_slack(token: str, channel: str, text: str) -> None:
    payload = json.dumps(
        {"channel": channel, "text": text, "mrkdwn": True}
    ).encode("utf-8")
    request = urllib.request.Request(
        SLACK_API,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except urllib.error.URLError as exc:
        print(f"Warning: Slack post failed: {exc}", file=sys.stderr)


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")

    token = os.environ.get("SLACK_BOT_TOKEN")
    if not token:
        print(
            "Warning: SLACK_BOT_TOKEN not set. Slack notification skipped.",
            file=sys.stderr,
        )
        return 0

    env_event = os.environ.get("CLAUDE_CODE_HOOK_EVENT")
    stdin_data = {} if env_event else read_stdin_json()
    event, codex_tool = resolve_event(env_event, stdin_data)

    text = build_text(event, codex_tool, Path.cwd())
    channel = os.environ.get("SLACK_CHANNEL", DEFAULT_CHANNEL)
    post_to_slack(token, channel, text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
