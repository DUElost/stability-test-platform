"""Build a release bundle from a working tree (I5.5 — the local stand-in for R2).

The site installer consumes a *bundle*: a directory with the documented layout
plus a ``release-manifest.json`` whose digests are computed with the ADR-0040
implementation.  Until the release pipeline exists, this tool produces exactly
that from a checkout, so "clone → install" needs no hand-rolled packing.

Usage:
    python tools/release/build_bundle.py --repo-root . --out /srv/stp-bundle \
        [--version local-20260915] [--schema-target <alembic-head>] [--wheelhouse]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = "release-manifest.json"
TREE_LAYOUT = ("backend", "deploy", "tools", "frontend/dist-prod")
# Agent 载荷的资源目录不在 git（230MB 工具集）：缺了它 bundle 照样能构建，
# 但 Agent 的 host-resources 摘要会与清单不符（或功能缺失），要到 S5 才暴露。
AGENT_RESOURCES = "backend/agent/resources"
DEFAULT_VERSION_PREFIX = "local"
# #2269：整树复制会把**构建机本地状态**一并打包。最严重的是 `backend/.env`
# （gitignored，但 `deploy/install.sh` 以仓库根为源 → 它进 bundle → S2 落到站点
# `<deploy-root>/backend/.env` → 后端启动时 `env_source.load_dotenv` 会加载它）：
#   - 构建机的未托管键（如 STP_FILE_SERVER_ADDRESS / STP_AEE_NFS_ROOT）被站点继承；
#   - 且 `_digests` 只覆盖 backend/agent，该文件**不在任何摘要面内**，
#     事后无法从产物校验面发现。
# 同批排除其余构建机产物（缓存/字节码），它们同样不应随交付物分发。
#
# ⚠️ **必须保留 `*.example` 模板**：仓库入库的 `.env*` 文件**全部**是模板——
# `.env.server.example` / `.env.test.example` / `backend/.env.example` /
# `backend/agent/.env.example` / `deploy/control-plane/env/.env.backend.example` /
# `…/.env.backend.internal.example` / `deploy/postgres/.env.example` /
# `frontend/.env.example`（实测 8 个：`git ls-files | grep -E '(^|/)\.env'`）。
# 部署文档要求 `cp deploy/postgres/.env.example deploy/postgres/.env`（README.md:13），
# 控制面安装亦以 `deploy/control-plane/env/.env.backend.example` 为模板。
# 用宽泛的 `.env.*`（或只豁免 `.env.example` 这一种写法）会剥掉它们、破坏部署。
# 故规则为：**排除 `.env*` 中除 `*.example` 之外的全部**。
def _is_env_template(name: str) -> bool:
    """`*.example` 视为入库模板（既不排除、也不判违规）。"""
    return name.endswith(".example")


def _is_forbidden_env_file(name: str) -> bool:
    """`.env*` 且非模板 → 构建机本地 env，必须排除。"""
    return (name == ".env" or name.startswith(".env.")) and not _is_env_template(name)


_BUNDLE_IGNORE_PATTERNS = (
    "__pycache__",
    "*.pyc",
    "*.pyo",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
)
# 注：**不能**用 `shutil.ignore_patterns(".env", ".env.*")`——`.env.*` 会把入库模板
# `.env.example` / `.env.backend.example` 一并吃掉。故 env 一族走自定义判据
# `_is_forbidden_env_file`（排除 `.env*` 中除 `*.example` 之外者）。
_BUNDLE_IGNORE_GLOBS = shutil.ignore_patterns(*_BUNDLE_IGNORE_PATTERNS)


def bundle_ignore(directory: str, names: list[str]) -> set[str]:
    """`copytree(ignore=...)` 回调：缓存/字节码按 glob，`.env*` 按模板豁免判据。"""
    ignored = set(_BUNDLE_IGNORE_GLOBS(directory, names))
    ignored.update(name for name in names if _is_forbidden_env_file(name))
    return ignored


BUNDLE_IGNORE = bundle_ignore
# 不得出现在 bundle 内的项（供构建后自检与 S0 硬断言共用）。
# 同样**豁免 `*.env.example`**（入库模板，见上）。
FORBIDDEN_BUNDLE_NAMES = (".env", ".env.local", ".env.production", ".env.development", ".env.test", ".env.backend")
FORBIDDEN_BUNDLE_SUFFIXES = (".pyc", ".pyo")
FORBIDDEN_BUNDLE_DIRS = ("__pycache__",)
SUPPORTED_PLATFORMS = (
    {"distribution": "debian", "versions": ["13"], "cpu_arch": ["x86_64"]},
    # 22.04 实测纳入（2026-09-15）：当时的证据只有 Agent 侧 compileall，安装器/后端
    # venv 用的系统解释器是否够用没有判据（#2268：3.10 上 import 即失败）。
    # 现在每行矩阵都必须有同源解释器下限：唯一声明处是 `tools.site_config.preflight.MIN_PYTHON`，
    # 一致性由 tests/test_site_installer_python_floor.py 钉死，并在 CI 上用该下限
    # 解释器真跑导入闭包（pr-agent-tests 的 floor 步骤），不只是 compileall。
    {"distribution": "ubuntu", "versions": ["22.04", "24.04"], "cpu_arch": ["x86_64"]},
)
AGENT_PROTOCOL = ">=1.0,<2.0"
EVIDENCE_REF = "local_build"


class BundleError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}{': ' + detail if detail else ''}")


def find_forbidden_bundle_entries(root: Path) -> list[str]:
    """返回 bundle 内**不应存在**的条目（相对路径，已排序）。

    #2269：构建机本地状态（`.env` / 字节码 / 缓存）不得随交付物分发。
    构建后自检与站点侧 S0 断言共用本函数，避免两处判据漂移。
    """
    found: list[str] = []
    for path in sorted(root.rglob("*")):
        name = path.name
        if name in FORBIDDEN_BUNDLE_DIRS:
            found.append(str(path.relative_to(root)))
        elif path.is_file() and path.suffix in FORBIDDEN_BUNDLE_SUFFIXES:
            found.append(str(path.relative_to(root)))
        elif _is_forbidden_env_file(name):
            found.append(str(path.relative_to(root)))
    return found


def _copy_tree(source: Path, target: Path) -> None:
    # 与 S2 落地同语义：保持符号链接；先清掉与源链接冲突的旧项
    for root, _dirs, files in os.walk(source):
        for name in files:
            candidate = Path(root) / name
            if not candidate.is_symlink():
                continue
            destination = target / candidate.relative_to(source)
            if destination.is_symlink() or destination.exists():
                destination.unlink()
    shutil.copytree(
        source, target, dirs_exist_ok=True, symlinks=True, ignore=BUNDLE_IGNORE,
    )


def _git(repo_root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), *args], capture_output=True, text=True, check=False,
        )
    except OSError:
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _revision(repo_root: Path) -> str:
    revision = _git(repo_root, "rev-parse", "HEAD")
    if not re.fullmatch(r"[0-9a-f]{7,40}", revision or ""):
        raise BundleError("bundle_revision", "not a git checkout; pass --revision explicitly")
    return revision


def _schema_target(repo_root: Path) -> str:
    """Derive the alembic head from the migration files (dependency-free)."""
    versions = repo_root / "backend" / "alembic" / "versions"
    revisions: dict[str, str] = {}
    referenced: set[str] = set()
    for path in sorted(versions.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        head = re.search(r"^revision(?::[^=]+)?\s*=\s*[\"']([0-9A-Za-z_]+)[\"']", text, re.M)
        down = re.search(r"^down_revision(?::[^=]+)?\s*=\s*(.+)$", text, re.M)
        if head is None:
            continue
        name = head.group(1)
        revisions[name] = path.name
        for token in re.findall(r"[\"']([0-9A-Za-z_]+)[\"']", down.group(1) if down else ""):
            referenced.add(token)
    heads = sorted(name for name in revisions if name not in referenced)
    if len(heads) != 1:
        raise BundleError("bundle_schema_target", f"expected one head, found {heads}")
    return heads[0]


def _load_agent_digest_module(bundle: Path):
    """按路径加载 ADR-0040 实现（stdlib-only，不触发 backend 包链）。

    #2269：加载会**在 bundle 内**生成 `backend/agent/__pycache__`——即构建机产物
    被写进交付物（与 `.env` 同源问题，只是危害小）。故加载期间强制关闭字节码写入，
    使 bundle 保持与源树一致、不含构建副产物。
    """
    import importlib.util
    import sys

    module_path = bundle / "backend" / "agent" / "artifact_digest.py"
    spec = importlib.util.spec_from_file_location("stp_agent_artifact_digest", module_path)
    if spec is None or spec.loader is None:
        raise BundleError("bundle_digest", f"cannot load {module_path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = previous
    return module


def _digests(bundle: Path) -> dict[str, str]:
    """与 S0（install._digest_bundle）同基准：code / resources 两侧都算。"""
    module = _load_agent_digest_module(bundle)
    agent_dir = bundle / "backend" / "agent"
    extra = {
        "stp_schemas/pipeline_schema.json": str(bundle / "backend" / "schemas" / "pipeline_schema.json"),
    }
    return {
        kind: module.digest_entries(module.collect_artifact_entries(str(agent_dir), extra, kind=kind))
        for kind in ("code", "resources")
    }


def build_bundle(
    repo_root: str | Path,
    out: str | Path,
    *,
    version: str | None = None,
    schema_target: str | None = None,
    revision: str | None = None,
    wheelhouse: bool = False,
    python: str | None = None,
) -> dict:
    repo_root, out = Path(repo_root).resolve(), Path(out).resolve()
    python = python or sys.executable
    missing = [name for name in TREE_LAYOUT if not (repo_root / name).exists()]
    if not missing and not (repo_root / AGENT_RESOURCES).is_dir():
        raise BundleError(
            "bundle_resources",
            f"{AGENT_RESOURCES} is not in git; copy it from the build host before packaging",
        )
    if "frontend/dist-prod" in missing:
        raise BundleError(
            "bundle_frontend", "run `cd frontend && npm ci && npm run build:prod` first",
        )
    if missing:
        raise BundleError("bundle_layout", ",".join(missing))

    out.mkdir(parents=True, exist_ok=True)
    for name in TREE_LAYOUT:
        _copy_tree(repo_root / name, out / name)
    for extra in ("ruff.toml",):
        if (repo_root / extra).exists():
            shutil.copy2(repo_root / extra, out / extra)

    revision = revision or _revision(repo_root)
    short = revision[:8]
    version = version or f"{DEFAULT_VERSION_PREFIX}-{datetime.now(timezone.utc):%Y%m%d}-{short}"
    digests = _digests(out)

    # #2269 fail-closed 自检：即便 ignore 被误改/新增目录绕过，构建也不得产出含
    # 构建机凭据或缓存的 bundle。构建期即失败，优于交付后被站点继承。
    stray = find_forbidden_bundle_entries(out)
    if stray:
        raise BundleError(
            "bundle_forbidden_entries",
            "bundle must not carry build-host local state: " + ", ".join(stray[:5]),
        )

    manifest = {
        "manifest_version": 1,
        "product": {"version": version},
        "source": {"revision": revision},
        "components": [
            {"name": "agent-code", "digest": digests["code"]},
            {"name": "host-resources", "digest": digests["resources"]},
        ],
        "database": {"schema_target": schema_target or _schema_target(out)},
        "compatibility": {
            "agent_protocol": AGENT_PROTOCOL,
            "platforms": [dict(platform) for platform in SUPPORTED_PLATFORMS],
        },
        "provenance": {"attestation": "controlled_channel", "evidence_ref": EVIDENCE_REF},
    }
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )

    if wheelhouse:
        requirements = out / "backend" / "requirements.txt"
        target = out / "wheelhouse"
        target.mkdir(exist_ok=True)
        result = subprocess.run(
            [python, "-m", "pip", "download", "--disable-pip-version-check",
             "-r", str(requirements), "-d", str(target)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            raise BundleError("bundle_wheelhouse", result.stderr.strip()[:200])

    return {
        "out": str(out),
        "version": version,
        "revision": revision,
        "components": {
            "agent-code": digests["code"], "host-resources": digests["resources"],
        },
        "schema_target": manifest["database"]["schema_target"],
        "wheelhouse": wheelhouse,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a site release bundle from a working tree.")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[2]))
    parser.add_argument("--out", required=True, help="Bundle output directory (created if needed).")
    parser.add_argument("--version", default=None)
    parser.add_argument("--schema-target", default=None, help="Override the derived alembic head.")
    parser.add_argument("--revision", default=None)
    parser.add_argument("--wheelhouse", action="store_true", help="Also download wheels for offline installs.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = build_bundle(
            args.repo_root, args.out, version=args.version, schema_target=args.schema_target,
            revision=args.revision, wheelhouse=args.wheelhouse,
        )
    except BundleError as error:
        print(f"FAILED: {error}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"Bundle written: {result['out']}")
        print(f"  version={result['version']} revision={result['revision'][:8]} schema_target={result['schema_target']}")
        for name, digest in result["components"].items():
            print(f"  {name}={digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
