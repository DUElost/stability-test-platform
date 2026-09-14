"""#1997 / ADR-0040 P2 尾项：Ansible 通道与部署 digest 契约锁定。

三面：
1. **parity**：`tools/ansible/compute_deploy_digest.py`（playbook 控制机
   侧计算，stdlib-only 加载 Agent 镜像算法）的输出与控制面 services digest
   字节级等价（两 kind）；
2. **排除集契约**：`agent_deploy/defaults/main.yml` 的 rsync 策略与 digest
   输入集对齐（test_*.py 宽模式、venv//logs/、mtbf/ 与双身份文件
   exclude+protect）；
3. **playbook 簿记**：`update_agent.yml` 含 compute + 双写入任务，且位于
   health 验证之后（失败/回滚路径天然不写）。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

# parity 测试在进程内导入 backend.services.*（backend.core 导入链要求
# DATABASE_URL 先就位——root tests 惯例，见 shared-worktree 教训档）
os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://localhost/stp_contract")

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCRIPT = _REPO_ROOT / "tools/ansible/compute_deploy_digest.py"
_DEFAULTS = _REPO_ROOT / "tools/ansible/roles/agent_deploy/defaults/main.yml"
_PLAYBOOK = _REPO_ROOT / "tools/ansible/playbooks/update_agent.yml"


def _write(path: Path, content: bytes | str, exec_bit: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    if exec_bit:
        path.chmod(0o755)


def _build_tree(base: Path) -> Path:
    """合成 agent 树：代码文件 + resources（含 mtbf）+ 宿主侧目录/元数据。"""
    _write(base / "main.py", "print('main')\n")
    _write(base / "tools" / "flash.sh", "#!/bin/sh\n", exec_bit=True)
    _write(base / "tests" / "test_x.py", "junk\n")
    _write(base / "test_top.py", "junk\n")
    _write(base / "venv" / "lib.py", "junk\n")
    _write(base / "logs" / "a.log", "junk\n")
    _write(base / "VERSION", "deadbeef\n")
    _write(base / "ARTIFACT_DIGEST", "sha256:" + "0" * 64 + "\n")
    _write(base / "resources" / "aimonkey" / "monkey.bin", b"BIN", exec_bit=True)
    _write(base / "resources" / "mtbf" / "AutoTestTool.apk", b"MTB")
    return base


def _run_script(*args: str) -> dict[str, str]:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, check=True, cwd=str(_REPO_ROOT),
    )
    return dict(line.split("=", 1) for line in proc.stdout.strip().splitlines())


class TestComputeScriptParity:
    """compute 脚本输出 == 控制面 services digest（字节级，两 kind）。"""

    def test_parity_code_and_resources(self, tmp_path, monkeypatch):
        import backend.services.artifact_digest as ad
        import backend.services.host_updater as hu

        tree = _build_tree(tmp_path / "agent")
        schema = tmp_path / "pipeline_schema.json"
        schema.write_text('{"version": 1}')

        monkeypatch.setattr(hu, "_AGENT_SOURCE_DIR", tree)
        monkeypatch.setattr(hu, "_PIPELINE_SCHEMA_FILE", schema)
        code_expected = ad.compute_desired_artifact_digest(kind="code")
        res_expected = ad.compute_desired_artifact_digest(kind="resources")

        out = _run_script("--source-dir", str(tree), "--schema-file", str(schema))
        assert out["CODE_DIGEST"] == code_expected
        assert out["RESOURCES_DIGEST"] == res_expected

    def test_empty_resources_reports_empty(self, tmp_path):
        tree = tmp_path / "agent"
        _write(tree / "main.py", "x\n")
        out = _run_script("--source-dir", str(tree))
        assert out["RESOURCES_DIGEST"] == ""
        assert out["CODE_DIGEST"].startswith("sha256:")


class TestRsyncPolicyContract:
    """agent_deploy defaults 的 rsync 策略与 digest 输入集契约对齐。"""

    def test_excludes_and_host_local_paths(self):
        policy = yaml.safe_load(_DEFAULTS.read_text(encoding="utf-8"))
        excludes = policy["agent_install_excludes"]
        host_local = policy["agent_host_local_paths"]

        # 契约对齐（#1997）：测试文件宽模式 + 宿主侧目录
        assert "test_*.py" in excludes
        assert "venv/" in excludes
        assert "logs/" in excludes
        assert "tests/" in excludes
        # 旧窄模式不得回流（与 digest 契约分叉）
        for stale in ("test_agent*.py", "test_aimonkey*.py", "test_main*.py"):
            assert stale not in excludes

        # 主机本地身份：mtbf 资源 + 双 digest 文件（exclude+protect，防 --delete）
        assert "resources/mtbf/" in host_local
        assert "ARTIFACT_DIGEST" in host_local
        assert "ARTIFACT_DIGEST_RESOURCES" in host_local


class TestPlaybookBookkeeping:
    """playbook 含 compute + 双写入任务，且位于 health 之后（失败不写）。"""

    def test_digest_tasks_after_health(self):
        text = _PLAYBOOK.read_text(encoding="utf-8")
        compute_at = text.index("Compute deployment artifact digests")
        write_code_at = text.index("Write agent ARTIFACT_DIGEST (ADR-0040 D2)")
        write_res_at = text.index("Write agent ARTIFACT_DIGEST_RESOURCES")
        health_at = text.index("Run agentctl health after restart")
        rescue_at = text.index("rescue:")

        assert health_at < compute_at < write_code_at < write_res_at < rescue_at

    def test_write_guards_use_script(self):
        text = _PLAYBOOK.read_text(encoding="utf-8")
        assert "compute_deploy_digest.py" in text
        # resources 空集不写（#1975 空集守卫的对偶）
        assert "agent_resources_artifact_digest | length > 0" in text
        # code 层 deploy 时才写（rsync 未跑则无身份可写）
        assert "agent_code_change_lines | length > 0" in text
        # 写入目标：agent 安装目录下的双身份文件
        assert "agent/ARTIFACT_DIGEST" in text
        assert "agent/ARTIFACT_DIGEST_RESOURCES" in text
