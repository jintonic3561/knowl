"""knowl._jsonutil のテスト."""

from __future__ import annotations

from knowl._jsonutil import (
    extract_first_json_object,
    extract_last_json_object,
    find_json_objects,
)


def test_find_json_objects_simple() -> None:
    assert find_json_objects('hello {"a":1} world') == ['{"a":1}']


def test_find_json_objects_nested() -> None:
    text = 'pre {"a":{"b":2,"c":[1,2]}} post'
    assert find_json_objects(text) == ['{"a":{"b":2,"c":[1,2]}}']


def test_find_json_objects_brace_in_string_is_ignored() -> None:
    text = '{"a":"x{y}z"}'
    assert find_json_objects(text) == ['{"a":"x{y}z"}']


def test_find_json_objects_escaped_quote_in_string() -> None:
    text = '{"a":"he said \\"hi\\""}'
    assert find_json_objects(text) == [text]


def test_find_json_objects_multiple() -> None:
    assert find_json_objects("{} {}") == ["{}", "{}"]


def test_extract_first_skips_garbage_object() -> None:
    text = '{not json} {"a":1}'
    # 最初の "{not json}" は invalid なので skip され、次の有効候補を返す
    assert extract_first_json_object(text) == {"a": 1}


def test_extract_last_object() -> None:
    text = '{"a":1} {"b":2}'
    assert extract_last_json_object(text) == {"b": 2}


def test_extract_returns_none_when_no_object() -> None:
    assert extract_first_json_object("no json here") is None
    assert extract_last_json_object("no json here") is None


def test_extract_handles_nested_with_string_braces() -> None:
    text = 'choice: {"repo":"a/b","number":1,"reason":"close {parens}"}'
    assert extract_first_json_object(text) == {
        "repo": "a/b",
        "number": 1,
        "reason": "close {parens}",
    }
