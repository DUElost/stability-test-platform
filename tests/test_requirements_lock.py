"""校验 backend/requirements.lock 与 requirements.txt 保持同步。

为什么不在 CI 里跑 `pip-compile` 重新生成再 diff:那样每当上游发布新版本、
解析结果变化,CI 就会莫名其妙变红,而仓库里其实什么都没改。真正要抓的
失败是「改了 requirements.txt 却忘了重新生成 lock」—— 这个用离线的集合
比对就能确定性地抓到,不依赖网络。

lock 的实际安装可行性由 docker-build 保证:Dockerfile.backend 用
`pip install --require-hashes -r requirements.lock`,装不上会直接构建失败。
"""
from __future__ import annotations

import hashlib
import importlib.util
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REQUIREMENTS = _REPO_ROOT / "backend" / "requirements.txt"
_LOCK = _REPO_ROOT / "backend" / "requirements.lock"
_DIGEST_TOOL = _REPO_ROOT / "tools" / "dev" / "requirements_digest.py"


def _load_digest_module():
    """按路径加载工具模块(文件名带下划线但不在包里)。"""
    spec = importlib.util.spec_from_file_location("requirements_digest", _DIGEST_TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _digest_of(text: str) -> str:
    entries = _load_digest_module().normalize(text)
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


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
        names.add(_normalize(re.split(r"[<>=!~\[;]", line, maxsplit=1)[0].strip()))
    return names


# 来源标记 = 直接依赖。按**文件名子串**匹配,而不是整条 `-r requirements.txt`:
#   pip-compile(2026-08-30 之前,在 backend/ 里跑) 写 `-r requirements.txt`
#   uv(2026-08-30 起,从仓库根跑)                 写 `-r backend/requirements.txt`
# 精确匹配会在换工具后静默把所有包判成「非直接依赖」,让下面的覆盖测试
# 报出一堆其实存在的包缺失 —— 换工具时踩过一次。
_VIA_MARKER = "requirements.txt"


def _lock_direct_names() -> set[str]:
    """lock 里来源标记为 requirements.txt 的包 = 直接依赖。"""
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
            # 单行形式:`# via -r backend/requirements.txt`
            if _VIA_MARKER in stripped and current:
                direct.add(current)
        elif in_via and stripped.startswith("#"):
            # 多行形式的续行:`#   -r backend/requirements.txt`
            if _VIA_MARKER in stripped and current:
                direct.add(current)
        elif stripped and not stripped.startswith("--hash") and not stripped.startswith("#"):
            in_via = False
    return direct


# ── 摘要函数的直接单元测试 ──────────────────────────────────────────────
#
# 只断言"仓库当前文件能对上"是不够的 —— 那只覆盖了 happy path,
# 摘要漏掉某类语义变化时它照样绿。下面直接喂构造输入验证行为。

@pytest.mark.parametrize(
    "before,after,label",
    [
        ("fastapi>=0.104.0,<1.0", "fastapi==999.0", "版本约束"),
        ("uvicorn[standard]>=0.24.0", "uvicorn>=0.24.0", "extras 丢失"),
        ("psycopg[binary]>=3.1.0", "psycopg[binary,pool]>=3.1.0", "extras 增加"),
        ("redis[asyncio]>=5.0.0,<6.0", "redis[asyncio]>=5.0.0,<7.0", "上界放宽"),
        (
            "demo @ https://example.invalid/demo.whl#sha256=aaa",
            "demo @ https://example.invalid/demo.whl#sha256=bbb",
            "直接 URL 的 fragment",
        ),
        (
            "pkg @ git+https://example.invalid/r.git#subdirectory=a",
            "pkg @ git+https://example.invalid/r.git#subdirectory=b",
            "VCS subdirectory",
        ),
        ('pkg>=1.0; python_version < "3.12"', 'pkg>=1.0; python_version < "3.13"', "环境标记"),
    ],
)
def test_digest_detects_semantic_change(before: str, after: str, label: str):
    assert _digest_of(before) != _digest_of(after), (
        f"{label} 变化未改变摘要 —— lock 过期将无法被发现"
    )


@pytest.mark.parametrize(
    "a,b,label",
    [
        ("fastapi>=1.0  # 尾随注释", "fastapi>=1.0", "尾随注释"),
        ("fastapi>=1.0\n# 整行注释", "fastapi>=1.0", "整行注释"),
        ("fastapi>=1.0\n\n\nrequests>=2.0", "fastapi>=1.0\nrequests>=2.0", "空行"),
        ("fastapi>=1.0\nrequests>=2.0", "requests>=2.0\nfastapi>=1.0", "条目顺序"),
        ("fastapi>=1.0   ", "fastapi>=1.0", "尾随空白"),
    ],
)
def test_digest_ignores_non_semantic_change(a: str, b: str, label: str):
    assert _digest_of(a) == _digest_of(b), f"{label} 不该改变摘要(会造成误报)"


def test_digest_strips_comment_but_keeps_url_fragment():
    """注释与 URL fragment 都含 `#`,判定规则必须与 pip 一致。

    pip 的 COMMENT_RE 是 `(^|\\s+)#.*$` —— `#` 只有在行首或前接空白才是注释。
    朴素的 `split("#")` 会把 whl 的 sha256 fragment 一并切掉。
    """
    normalize = _load_digest_module().normalize
    assert normalize("pkg>=1.0  # c") == ["pkg>=1.0"]
    assert normalize("# 整行") == []
    url = "demo @ https://example.invalid/demo.whl#sha256=aaa"
    assert normalize(url) == [url], "URL fragment 被误当成注释切掉了"
    assert normalize(f"{url}  # 真注释") == [url]


def test_lock_exists_and_is_hashed():
    assert _LOCK.is_file(), "缺少 backend/requirements.lock"
    text = _LOCK.read_text(encoding="utf-8")
    assert "--hash=sha256:" in text, "lock 必须带 hash(--generate-hashes)"


def test_lock_generated_for_ci_python_version():
    """lock 必须按 py3.11 解析 —— CI 与 Dockerfile.backend 都是 3.11。

    跨版本解析出的 wheel 组合可能在目标环境装不上(不同 Python 版本会挑到
    不同的 wheel)。

    两种生成工具的写法都要认:
      pip-compile(2026-08-30 之前) 写 `# This file was autogenerated ...
                                       with Python 3.11`
      uv(2026-08-30 起)            写 `#    uv pip compile
                                       --python-version 3.11 ...`
    本 lock 已于 2026-08-30 迁到 uv: pip-compile 在这份输入上实测 30 分钟
    仍不能完成,CI 里无法用它自动补 lock。
    """
    # 扫整个抬头注释区,而不是固定字节数 —— 抬头说明长度会变
    head_lines = []
    for line in _LOCK.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            break
        head_lines.append(line)
    head = "\n".join(head_lines)
    assert re.search(r"(with Python 3\.11|--python-version 3\.11)", head), (
        "requirements.lock 不是按 Python 3.11 解析的(抬头里既没有 "
        "`with Python 3.11` 也没有 `--python-version 3.11`)。"
        "重新生成命令见该文件抬头。"
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
