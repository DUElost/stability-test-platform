"""守卫：control-plane-deploy SOP 的发布根 venv 必须按 lock 装，不得退回区间安装（#3569）。

背景：ADR-0051 Phase 1 之后生产走 bundle 发布根 + 每 rev 一个 venv。SOP 曾规定
`pip install -r backend/requirements.txt`，而该文件全文件仅 1 处 `==`，其余是区间约束
⇒ 每次部署都重新解析到当时最新。2026-10-01 部署 rev 8b2aa0fb（区间内 requirements.txt 与
requirements.lock 均零变更）时实测 5 个包漂移：fastapi 0.142.1→0.142.2、cryptography
50.0.1→50.0.2 等——纯代码部署顺带升了 fastapi 小版本，回滚 rev ≠ 回滚依赖。

根 `Dockerfile.backend` 一直用 `pip install --require-hashes -r requirements.lock`，本仓
两条部署路径曾因此语义相反。本守卫把 bundle 路径钉到与 Dockerfile 同源的口径上。

这里守的是**文档形态**而非运行结果：发布根 venv 在本机、CI 跑不到；但 SOP 是部署的唯一
操作依据，命令写回区间安装就等于缺陷复发，故用静态断言钉住。
"""
from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SKILL = _REPO_ROOT / ".claude" / "skills" / "control-plane-deploy" / "SKILL.md"
_DOCKERFILE = _REPO_ROOT / "Dockerfile.backend"


def _skill_text() -> str:
    assert _SKILL.is_file(), f"缺少 {_SKILL}"
    return _SKILL.read_text(encoding="utf-8")


def test_skill_declares_locked_venv_install():
    """SOP 必须给出 lock + --require-hashes 的安装命令。"""
    text = _skill_text()
    assert "backend/requirements.lock" in text, "SOP 未提及 backend/requirements.lock"
    assert "--require-hashes" in text, "SOP 的 venv 安装命令缺 --require-hashes"


def test_skill_has_no_unpinned_venv_install():
    """SOP 不得出现按区间安装发布根 venv 的命令。

    匹配 `pip install ... -r backend/requirements.txt`（不含 .lock）。文档里若要解释
    「为什么不用 requirements.txt」，措辞不应构成可复制的命令，故按命令行形态匹配。
    """
    text = _skill_text()
    offenders = [
        line
        for line in text.splitlines()
        if re.search(r"pip\s+install\b.*-r\s+backend/requirements\.txt(?!\.lock)", line)
    ]
    assert not offenders, f"SOP 退回未锁安装：{offenders}"


def test_skill_venv_command_uses_lock_not_txt():
    """SOP §1 步 3 的 venv 命令行本身必须指向 lock。"""
    text = _skill_text()
    venv_lines = [
        line
        for line in text.splitlines()
        if "python3 -m venv" in line or ("pip install" in line and "venv" in line)
    ]
    assert venv_lines, "SOP 未找到 venv 安装命令"
    joined = "\n".join(venv_lines)
    assert "requirements.lock" in joined, f"venv 安装命令未指向 lock：{venv_lines}"


def test_dockerfile_and_skill_same_lock_semantics():
    """两条部署路径必须消费同一份 lock（防其中一条再次漂移）。"""
    text = _skill_text()
    dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
    assert "--require-hashes" in dockerfile, "Dockerfile.backend 缺 --require-hashes"
    assert "requirements.lock" in dockerfile, "Dockerfile.backend 未按 lock 安装"
    assert "--require-hashes" in text and "requirements.lock" in text, (
        "SOP 与 Dockerfile.backend 的依赖安装口径不一致"
    )


def test_lock_maintenance_is_referenced():
    """SOP 必须指出 lock 有自动维护，避免「以为要人工记得更新」。"""
    text = _skill_text()
    assert "regenerate-locks" in text or "regenerate-lock" in text, (
        "SOP 未说明 lock 的自动维护路径"
    )
