"""#3353：compose 口令插值不得静默回落 + 弱口令必须显式告警。

**本质问题**：`docker-compose.yml` 的 `${VAR:-默认}` 只读**根目录 `.env` 与 shell 环境**，
不读 `env_file` 的 `.env.server`；而 `environment:` 又优先于 `env_file`。于是
「按文档把口令写进 `.env.server`」的人实际拿到的是 compose 默认值
（`POSTGRES_PASSWORD=change-me-local`、`STP_ADMIN_PASSWORD=admin123`）——**部分键静默失效**，
比全失效更难发现（#3353 在第二套隔离栈上排查 401 才定位）。

本文件钉三处：

1. `docker-compose.yml`：两个口令键是强制引用（含 `:?`），且不再携带弱默认值；
2. `.env.server.example`：不再发 `STP_ADMIN_PASSWORD=admin123`——模板是给人抄的，
   抄下去就等于把弱口令当配置；同一段注释必须说明插值只认根目录 `.env`；
3. `init_dev_db.py`：弱口令（`admin123`）在 seed 时打印显式告警，不静默生效。

判别力自证：把变异输入喂给同一个抽取/断言函数，要求当场被抓。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "docker-compose.yml"
ENV_SERVER_EXAMPLE = ROOT / ".env.server.example"
INIT_DEV_DB = ROOT / "backend" / "scripts" / "init_dev_db.py"
LOCAL_DEV_DOC = ROOT / "docs" / "development" / "local-development.md"

#: 弱默认值一旦回到 compose/模板里，本门禁必须当场变红。
_WEAK_DEFAULTS = ("admin123", "change-me-local")
_REQUIRED_PASSWORD_KEYS = (
    ("postgres", "POSTGRES_PASSWORD"),
    ("server", "STP_ADMIN_PASSWORD"),
)


def _compose_env(compose_text: str) -> dict[str, dict[str, str]]:
    """{service: {env_key: raw_value}}（raw_value 保留 `${...}` 原样）。"""
    doc = yaml.safe_load(compose_text)
    out: dict[str, dict[str, str]] = {}
    for service, body in (doc.get("services") or {}).items():
        env = (body or {}).get("environment") or {}
        out[service] = {str(k): str(v) for k, v in env.items()}
    return out


def _active_env_template_lines(env_template: str) -> list[str]:
    """模板里**生效**的行（去注释/空行）——注释里说明「这些键在 .env」是允许的。"""
    out: list[str] = []
    for line in env_template.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(stripped)
    return out


def _password_problems(compose_text: str, env_template: str) -> list[str]:
    """把输入折成问题清单（空 = 通过）；变异自证复用同一函数。"""
    problems: list[str] = []
    env = _compose_env(compose_text)
    for service, key in _REQUIRED_PASSWORD_KEYS:
        raw = env.get(service, {}).get(key, "")
        if not raw:
            problems.append(f"{service}.{key} 不在 compose environment 里")
            continue
        if ":?" not in raw:
            problems.append(f"{service}.{key} 未强制引用（缺 `:?`）：{raw}")
        for weak in _WEAK_DEFAULTS:
            if f":-{weak}" in raw:
                problems.append(f"{service}.{key} 仍带弱默认值 `:-{weak}`")
    # DATABASE_URL 里嵌的口令引用同样不得回落（否则 postgres 服务之外还能被绕）
    db_url = env.get("server", {}).get("DATABASE_URL", "")
    if db_url and "POSTGRES_PASSWORD:?" not in db_url:
        problems.append("server.DATABASE_URL 的 POSTGRES_PASSWORD 引用未强制（缺 `:?`）")

    for line in _active_env_template_lines(env_template):
        key = line.split("=", 1)[0].strip()
        if key == "STP_ADMIN_PASSWORD":
            problems.append(".env.server.example 仍声明 STP_ADMIN_PASSWORD（插值不读它，抄下去=弱口令）")
        for weak in _WEAK_DEFAULTS:
            if weak in line:
                problems.append(f".env.server.example 生效行仍含弱默认值 {weak!r}: {line}")
    return problems


def test_compose_password_keys_fail_closed():
    problems = _password_problems(
        COMPOSE.read_text(encoding="utf-8"),
        ENV_SERVER_EXAMPLE.read_text(encoding="utf-8"),
    )
    assert problems == [], "\n".join(problems)


def test_compose_guard_detects_weak_fallback_and_template_regression():
    """自证：把历史形状（`:-admin123` / `:-change-me-local` / 模板带弱口令）喂回去必须红。"""
    current = COMPOSE.read_text(encoding="utf-8")
    mutated = (
        current
        .replace("${STP_ADMIN_PASSWORD:?", "${STP_ADMIN_PASSWORD:-admin123")
        .replace("${POSTGRES_PASSWORD:?", "${POSTGRES_PASSWORD:-change-me-local")
    )
    assert mutated != current, "变异没生效——compose 形状变了，更新本自证"
    problems = _password_problems(mutated, ENV_SERVER_EXAMPLE.read_text(encoding="utf-8"))
    assert any("弱默认值" in p for p in problems), problems

    problems = _password_problems(current, "STP_ADMIN_PASSWORD=admin123\n")
    assert any(".env.server.example" in p for p in problems), problems


def test_init_dev_db_warns_on_weak_password():
    source = INIT_DEV_DB.read_text(encoding="utf-8")
    # 判据落在「比较 + 告警标记」两处，避免只留注释也能过
    assert re.search(r'password\s*==\s*"admin123"', source), (
        "init_dev_db.py 未检测 admin123——弱口令会静默成为控制台口令"
    )
    assert "dev_db_admin_WEAK_PASSWORD" in source, "弱口令告警标记丢失"


def test_local_development_docs_state_interpolation_source():
    doc = LOCAL_DEV_DOC.read_text(encoding="utf-8")
    assert "compose 插值" in doc, "文档未写明 compose 插值只读 .env 与 shell（#3353 口径）"
    assert "不读 .env.server" in doc or "不读 env_file" in doc, (
        "文档未写明 .env.server 不参与插值——「按文档做」的人仍会踩同一个坑"
    )
    assert "STP_ADMIN_PASSWORD" in doc


# ── 真值复核（docker 可用时跑；CI PR 通道无 docker，静态守卫恒跑）──────────────

def _docker_available() -> bool:
    return shutil.which("docker") is not None and subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


@pytest.mark.skipif(not _docker_available(), reason="本机无 docker（CI PR 通道常态）")
def test_docker_compose_config_requires_the_two_passwords(tmp_path):
    """端到端真值：`docker compose config` 在未设口令时**以非零退出**并带上我们的提示。

    静态守卫只证明文本形状；这一条证明 compose 真的按 `:?` 语义 fail-closed。
    在 tmp 目录里用 compose 文件副本 + 空 `.env.server` 跑（不触碰仓库/生产栈。
    `config` 只做插值与校验，不启动任何容器）。
    """
    workdir = tmp_path / "compose-probe"
    workdir.mkdir()
    (workdir / "docker-compose.yml").write_text(
        COMPOSE.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (workdir / ".env.server").write_text("", encoding="utf-8")
    env = {k: v for k, v in os.environ.items()
           if k not in ("POSTGRES_PASSWORD", "STP_ADMIN_PASSWORD")}

    failed = subprocess.run(
        ["docker", "compose", "config"], cwd=workdir, env=env, capture_output=True, text=True,
    )
    assert failed.returncode != 0, "未设口令时 compose config 竟通过——静默回落又回来了"
    assert any(
        name in (failed.stderr + failed.stdout)
        for name in ("POSTGRES_PASSWORD", "STP_ADMIN_PASSWORD")
    ), "报错里没点名缺失的变量"
    # 两个键都得被强制；compose 报哪一个取决于服务迭代顺序（实测非确定），
    # 所以按「只设其一」逐个验证，判据只钉**变量名**（自定义提示文案可能来自任一引用点）。
    only_postgres = subprocess.run(
        ["docker", "compose", "config"], cwd=workdir,
        env={**env, "POSTGRES_PASSWORD": "pw-probe"}, capture_output=True, text=True,
    )
    assert only_postgres.returncode != 0
    assert "STP_ADMIN_PASSWORD" in (only_postgres.stderr + only_postgres.stdout)

    only_admin = subprocess.run(
        ["docker", "compose", "config"], cwd=workdir,
        env={**env, "STP_ADMIN_PASSWORD": "pw-probe"}, capture_output=True, text=True,
    )
    assert only_admin.returncode != 0
    assert "POSTGRES_PASSWORD" in (only_admin.stderr + only_admin.stdout)

    ok = subprocess.run(
        ["docker", "compose", "config"], cwd=workdir,
        env={**env, "POSTGRES_PASSWORD": "pw-probe", "STP_ADMIN_PASSWORD": "pw-probe"},
        capture_output=True, text=True,
    )
    assert ok.returncode == 0, ok.stderr
