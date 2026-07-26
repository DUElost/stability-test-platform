"""校验 backend/requirements.lock 与 requirements.txt 保持同步。

为什么不在 CI 里跑 `pip-compile` 重新生成再 diff:那样每当上游发布新版本、
解析结果变化,CI 就会莫名其妙变红,而仓库里其实什么都没改。真正要抓的
失败是「改了 requirements.txt 却忘了重新生成 lock」—— 这个用离线的集合
比对就能确定性地抓到,不依赖网络。

lock 的实际安装可行性由 docker-build 保证:Dockerfile.backend 用
`pip install --require-hashes -r requirements.lock`,装不上会直接构建失败。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENTS = _REPO_ROOT / "backend" / "requirements.txt"
_LOCK = _REPO_ROOT / "backend" / "requirements.lock"


def _normalize(name: str) -> str:
    """PEP 503 规范化:大小写、-/_/. 视为等价。"""
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared_names() -> set[str]:
    names = set()
    for raw in _REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        # 去掉 extras 与版本约束:`psycopg[binary]>=3.1.0` → `psycopg`
        names.add(_normalize(re.split(r"[<>=!~\[;]", line, 1)[0].strip()))
    return names


def _lock_direct_names() -> set[str]:
    """lock 里标注了 `via -r requirements.txt` 的包 = 直接依赖。"""
    lines = _LOCK.read_text(encoding="utf-8").splitlines()
    direct, current = set(), None
    in_via = False
    for line in lines:
        pinned = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if pinned:
            current = _normalize(pinned.group(1))
            in_via = False
            continue
        stripped = line.strip()
        if stripped.startswith("# via"):
            in_via = True
            # 单行形式:`# via -r requirements.txt`
            if "-r requirements.txt" in stripped and current:
                direct.add(current)
        elif in_via and stripped.startswith("#"):
            # 多行形式的续行:`#   -r requirements.txt`
            if "-r requirements.txt" in stripped and current:
                direct.add(current)
        elif stripped and not stripped.startswith("--hash") and not stripped.startswith("#"):
            in_via = False
    return direct


def test_lock_exists_and_is_hashed():
    assert _LOCK.is_file(), "缺少 backend/requirements.lock"
    text = _LOCK.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text, "lock 必须带 hash(--generate-hashes)"


def test_lock_generated_for_ci_python_version():
    """lock 必须在 py3.11 下生成 —— CI 与 Dockerfile.backend 都是 3.11。

    跨版本生成的 hash 集可能在目标环境装不上(不同 Python 版本会解析出
    不同的 wheel 组合)。
    """
    # 扫整个抬头注释区,而不是固定字节数 —— 抬头说明长度会变
    head_lines = []
    for line in _LOCK.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            break
        head_lines.append(line)
    head = "\n".join(head_lines)
    assert "with Python 3.11" in head, (
        "requirements.lock 不是用 Python 3.11 生成的。重新生成命令见该文件抬头。"
    )


def test_lock_digest_matches_requirements():
    """lock 必须记录 requirements.txt 的内容摘要,且与当前内容一致。

    这是最要紧的一条:下面按包名的集合比对**看不见版本与 extras 的漂移**。
    `fastapi>=0.104` 改成 `fastapi==999.0`、`uvicorn[standard]` 掉成
    `uvicorn`,包名都没变 —— 集合比对全绿,而 CI 装的是新约束、Docker 装的
    是旧 lock,测试与生产的依赖就此分叉。
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "tools" / "dev" / "requirements_digest.py"),
            str(_REQUIREMENTS),
            str(_LOCK),
            "--check",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_lock_covers_every_declared_requirement():
    """requirements.txt 里的每个包都必须出现在 lock 中。

    抓的是「加了依赖但没重新生成 lock」—— 那样生产镜像会装不上新依赖,
    但问题要到部署时才暴露。
    """
    missing = sorted(_declared_names() - _lock_direct_names())
    assert not missing, (
        "以下依赖在 backend/requirements.txt 中声明,但 lock 里没有对应条目:\n  "
        + "\n  ".join(missing)
        + "\nlock 已过期,请按 backend/requirements.lock 抬头的命令重新生成。"
    )


def test_lock_has_no_stale_direct_requirements():
    """反向:lock 标为直接依赖、但 requirements.txt 已经删掉的包。

    留着会让生产镜像继续装已经不需要的东西。
    """
    stale = sorted(_lock_direct_names() - _declared_names())
    assert not stale, (
        "以下包在 lock 里标为直接依赖,但 requirements.txt 已不再声明:\n  "
        + "\n  ".join(stale)
        + "\nlock 已过期,请重新生成。"
    )


@pytest.mark.parametrize("pkg", ["pytest", "testcontainers", "ruff", "hypothesis"])
def test_test_only_deps_stay_out_of_runtime_lock(pkg: str):
    """测试/开发依赖不得进入生产镜像的依赖树。

    它们属于 requirements-dev.txt;混进 requirements.txt 会一并打进镜像,
    既是体积浪费也是无谓的攻击面。
    """
    assert _normalize(pkg) not in _lock_direct_names(), (
        f"{pkg} 是测试/开发依赖,不该出现在 requirements.txt(进而进入生产镜像)。"
        " 请移到 backend/requirements-dev.txt。"
    )
