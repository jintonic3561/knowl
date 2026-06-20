"""subprocess.run の共通ラッパ.

claude / gh / docker を呼ぶ各モジュールで個別に書かれていた
「``subprocess.run`` + ``CalledProcessError``/``TimeoutExpired`` → 独自 ``RuntimeError``」
のボイラープレートを 1 関数に集約する。 ``error_cls`` を呼び出し側から渡すので、
モジュールごとに独自の例外クラス (``ClaudeError`` / ``GitHubError`` / ``ContainerError``
/ ``UsageError``) をそのまま使える。
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


def run_checked(
    cmd: Sequence[str],
    *,
    error_cls: type[Exception],
    label: str,
    timeout: float | None = None,
    check: bool = True,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` を ``capture_output=True, text=True`` で実行する.

    - ``check=True`` のとき rc != 0 で ``CalledProcessError`` が出るが、それを
      ``error_cls(f"{label} failed: {stderr or stdout}")`` に詰め替えて投げる。
      ``check=False`` のときは詰め替えず ``CompletedProcess`` を返すので、呼び出し
      側で rc と stderr を見て (例: ``limit_reached`` 判定など) 詳細処理ができる。
    - ``TimeoutExpired`` は常に ``error_cls(f"{label} timed out")`` に詰め替える。
    - ``input`` は ``claude_runner`` で将来 stdin 経由のプロンプト渡しに使えるよう
      残してある。 ``None`` のときは ``subprocess.run`` にもデフォルトで ``None``
      が渡るので副作用はない。
    """
    try:
        result = subprocess.run(
            list(cmd),
            capture_output=True,
            text=True,
            check=check,
            timeout=timeout,
            input=input,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise error_cls(f"{label} failed: {detail}") from exc
    except subprocess.TimeoutExpired as exc:
        raise error_cls(f"{label} timed out") from exc
    return result


__all__ = ["run_checked"]
