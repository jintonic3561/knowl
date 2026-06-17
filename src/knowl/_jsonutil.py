"""モデル出力からの JSON オブジェクト抽出ユーティリティ.

Claude のテキスト返答中から、対応カッコ(ネスト・文字列内の波カッコ無視)で
JSON オブジェクトを切り出す。最初に見つかった有効候補、または末尾の有効候補を返す。
"""

from __future__ import annotations

import json
from typing import Any


def find_json_objects(text: str) -> list[str]:
    """テキスト中の "対応の取れた波カッコ" 範囲をすべて抽出する."""
    candidates: list[str] = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    candidates.append(text[start : i + 1])
                    start = -1
    return candidates


def _parse(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def extract_first_json_object(text: str) -> dict[str, Any] | None:
    """テキスト中で最初に出現する有効な JSON オブジェクトを返す."""
    for raw in find_json_objects(text):
        obj = _parse(raw)
        if obj is not None:
            return obj
    return None


def extract_last_json_object(text: str) -> dict[str, Any] | None:
    """テキスト末尾側に近い有効な JSON オブジェクトを返す."""
    for raw in reversed(find_json_objects(text)):
        obj = _parse(raw)
        if obj is not None:
            return obj
    return None
