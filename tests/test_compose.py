"""docker/docker-compose.yml の volume 構成回帰テスト.

knowl 自身の PR が auto-merge された後、稼働中コンテナに src / templates の変更を
伝播させるため bind mount を必須としている (issue #20)。
mount が外れると次サイクルが古いコードのまま静かに進行し続けるため、
構造を test でロックしておく。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_COMPOSE_PATH = Path(__file__).resolve().parents[1] / "docker" / "docker-compose.yml"


def _knowl_service() -> dict[str, Any]:
    raw = yaml.safe_load(_COMPOSE_PATH.read_text(encoding="utf-8"))
    services = raw["services"]
    assert isinstance(services, dict)
    knowl = services["knowl"]
    assert isinstance(knowl, dict)
    return knowl


def _short_syntax_binds() -> set[tuple[str, str, bool]]:
    """compose short syntax の volume を (source, target, read_only) へ正規化.

    `:ro` や `:ro,nocopy` のような option 列、`../src/` のような末尾スラッシュ違いを
    吸収して、論理的に等価な書き換えで回帰テストが誤検出しないようにする。
    """
    volumes = _knowl_service()["volumes"]
    assert isinstance(volumes, list)
    binds: set[tuple[str, str, bool]] = set()
    for entry in volumes:
        if not isinstance(entry, str):
            continue  # long syntax (dict) は今のところ非対応。必要になったら拡張。
        parts = entry.split(":")
        if len(parts) < 2:
            continue
        source, target = parts[0], parts[1]
        options = parts[2].split(",") if len(parts) > 2 else []
        binds.add((source.rstrip("/"), target.rstrip("/"), "ro" in options))
    return binds


def test_src_bind_mount_present_readonly() -> None:
    assert ("../src", "/opt/knowl/src", True) in _short_syntax_binds()


def test_templates_bind_mount_present_readonly() -> None:
    # templates は config.yaml と同じディレクトリ基準で resolve されるため、
    # config (/etc/knowl/config.yaml) と sibling な /etc/knowl/templates にマウントする。
    assert ("../templates", "/etc/knowl/templates", True) in _short_syntax_binds()
