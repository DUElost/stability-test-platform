"""I4：install_agent.sh / install_agent.yml 非交互安装契约。

控制面驱动安装（POST /hosts/{id}/install）不可能有终端：API_URL 与 HOST_ID
必须经环境变量注入，读不到就 fail-closed 退出 1，绝不能停在 read 上等输入。
AEE 两键（STP_AEE_NFS_ROOT / STP_AEE_LOCAL_ROOT）是 Agent 启动必需项，
安装时必须落盘——且更新路径的空值不得覆盖既有非空值。

测行为而非文案：把脚本里的三个真实代码块抽出来，在临时目录里用 bash 执行，
断言退出码与生成的 .env 内容；playbook 侧用 YAML 结构断言。
"""

from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_SCRIPT = REPO_ROOT / "backend/agent/install_agent.sh"
INSTALL_PLAYBOOK = REPO_ROOT / "tools/ansible/playbooks/install_agent.yml"

_API_URL_BLOCK_START = "# API_URL 解析（#I4 非交互化）"
_HOST_ID_BLOCK_START = "# 获取本机信息用于生成唯一标识"
_ENV_BLOCK_START = 'if [ ! -f "$INSTALL_DIR/.env" ]; then'
_ENV_BLOCK_END = 'chmod 640 "$INSTALL_DIR/.env"'
# #2181：AEE 落点与共享挂载点准备块（夹在 .env 落盘与 7.5 守门之间）
_DIRS_BLOCK_START = "# 7.4 AEE 落点与中心共享挂载点（#2181）"
_DIRS_BLOCK_END = "# 7.5 STP_AEE_LOCAL_ROOT 静态守门"


def _block(start: str, end: str | None = None) -> str:
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")
    begin = text.index(start)
    if end is None:
        return text[begin:]
    return text[begin : text.index(end, begin)]


def _run_block(
    block: str,
    *,
    tmp_path: Path,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    log_helpers = textwrap.dedent(
        """
        echo_info() { echo "[INFO] $1"; }
        echo_warn() { echo "[WARN] $1"; }
        echo_error() { echo "[ERROR] $1"; }
        """
    )
    merged = dict(os.environ)
    for key in ("AGENT_API_URL", "AGENT_HOST_ID", "AGENT_NFS_ROOT", "AGENT_LOCAL_AEE_ROOT"):
        merged.pop(key, None)
    merged.update(env or {})
    return subprocess.run(
        ["bash", "-c", log_helpers + block],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        env=merged,
    )


def _api_url_block() -> str:
    return _block(_API_URL_BLOCK_START, _HOST_ID_BLOCK_START)


def _host_id_block(
    stub: str = 'generate_unique_host_id() { echo "generated-default"; }\n',
) -> str:
    # generate_unique_host_id 属上一段（curl + python3），测试里用桩替换。
    return stub + _block(_HOST_ID_BLOCK_START, "upsert_env_key")


def _env_block() -> str:
    stub = textwrap.dedent(
        """
        upsert_env_key() {
            local key="$1" value="$2" file="$INSTALL_DIR/.env"
            if grep -q "^${key}=" "$file"; then
                sed -i "s|^${key}=.*|${key}=${value}|" "$file"
            else
                printf '%s=%s\\n' "$key" "$value" >> "$file"
            fi
        }
        """
    )
    return stub + _block(_ENV_BLOCK_START, _ENV_BLOCK_END)


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


class TestApiUrlResolution:
    def test_env_supplies_api_url_without_tty(self, tmp_path):
        out = _run_block(
            _api_url_block() + '\necho "RESOLVED=$API_URL"\n',
            tmp_path=tmp_path,
            env={"AGENT_API_URL": "https://stp.example.com"},
        )
        assert out.returncode == 0, out.stderr
        assert "RESOLVED=https://stp.example.com" in out.stdout

    def test_env_value_is_trimmed(self, tmp_path):
        out = _run_block(
            _api_url_block() + '\necho "RESOLVED=$API_URL"\n',
            tmp_path=tmp_path,
            env={"AGENT_API_URL": "  https://stp.example.com\n"},
        )
        assert out.returncode == 0, out.stderr
        assert "RESOLVED=https://stp.example.com" in out.stdout

    def test_missing_api_url_without_tty_exits_nonzero(self, tmp_path):
        """无 TTY 且未注入 → 立刻退出 1（不得停在 read 上阻塞安装）。"""
        out = _run_block(_api_url_block(), tmp_path=tmp_path)
        assert out.returncode == 1, out.stdout + out.stderr
        assert "AGENT_API_URL" in out.stdout + out.stderr

    def test_stdin_eof_does_not_silently_pass(self, tmp_path):
        """stdin 是管道（非 TTY）时空输入也算缺失——旧 printf|read 依赖已移除。"""
        out = _run_block(
            _api_url_block(),
            tmp_path=tmp_path,
            env={"AGENT_API_URL": "   "},
        )
        assert out.returncode == 1


class TestHostIdResolution:
    def test_env_host_id_wins_and_skips_generation(self, tmp_path):
        marker = tmp_path / "generated"
        stub = f'generate_unique_host_id() {{ touch "{marker}"; echo "generated-default"; }}\n'
        out = _run_block(
            _host_id_block(stub) + '\necho "RESOLVED=$HOST_ID"\n',
            tmp_path=tmp_path,
            env={"AGENT_HOST_ID": "198-51-100-6"},
        )
        assert out.returncode == 0, out.stderr
        assert "RESOLVED=198-51-100-6" in out.stdout
        # 控制面已按 Host 行注入时不再调用 API 探测（省一次网络依赖）
        assert not marker.exists()

    def test_generated_fallback_used_without_tty(self, tmp_path):
        out = _run_block(
            _host_id_block() + '\necho "RESOLVED=$HOST_ID"\n',
            tmp_path=tmp_path,
        )
        assert out.returncode == 0, out.stderr
        assert "RESOLVED=generated-default" in out.stdout


class TestEnvFileWrites:
    def _env(self, tmp_path: Path, **overrides: str) -> dict[str, str]:
        base = {
            "INSTALL_DIR": str(tmp_path / "agent-install"),
            "USER": "android",
            "GROUP": "android",
            "API_URL": "https://stp.example.com",
            "HOST_ID": "198-51-100-6",
            "AGENT_SECRET": "s" * 20,
        }
        base.update(overrides)
        return base

    def test_fresh_install_writes_aee_keys(self, tmp_path):
        (tmp_path / "agent-install").mkdir()
        out = _run_block(
            _env_block(), tmp_path=tmp_path, env=self._env(
                tmp_path, AGENT_NFS_ROOT="/mnt/nfs/aee_events",
                AGENT_LOCAL_AEE_ROOT="/mnt/hdd/aee_events",
            ),
        )
        assert out.returncode == 0, out.stderr
        values = _read_env(tmp_path / "agent-install/.env")
        assert values["STP_AEE_NFS_ROOT"] == "/mnt/nfs/aee_events"
        assert values["STP_AEE_LOCAL_ROOT"] == "/mnt/hdd/aee_events"
        assert values["API_URL"] == "https://stp.example.com"
        assert values["HOST_ID"] == "198-51-100-6"

    def test_fresh_install_without_aee_paths_leaves_keys_empty(self, tmp_path):
        (tmp_path / "agent-install").mkdir()
        out = _run_block(_env_block(), tmp_path=tmp_path, env=self._env(tmp_path))
        assert out.returncode == 0, out.stderr
        values = _read_env(tmp_path / "agent-install/.env")
        # 键存在但为空：安装脚本的静态守门会告警，不阻断安装
        assert values["STP_AEE_NFS_ROOT"] == ""
        assert values["STP_AEE_LOCAL_ROOT"] == ""

    def test_update_empty_values_do_not_clear_existing(self, tmp_path):
        """反例守卫：空值不得覆盖既有非空值（机器本地路径尤其不能被抹掉）。"""
        install_dir = tmp_path / "agent-install"
        install_dir.mkdir()
        (install_dir / ".env").write_text(
            "API_URL=https://old.example.com\n"
            "HOST_ID=old-id\n"
            "STP_AEE_NFS_ROOT=/mnt/nfs/kept\n"
            "STP_AEE_LOCAL_ROOT=/mnt/hdd/kept\n",
            encoding="utf-8",
        )
        out = _run_block(_env_block(), tmp_path=tmp_path, env=self._env(tmp_path))
        assert out.returncode == 0, out.stderr
        values = _read_env(install_dir / ".env")
        assert values["API_URL"] == "https://stp.example.com"
        assert values["HOST_ID"] == "198-51-100-6"
        assert values["STP_AEE_NFS_ROOT"] == "/mnt/nfs/kept"
        assert values["STP_AEE_LOCAL_ROOT"] == "/mnt/hdd/kept"

    def test_update_non_empty_values_are_written(self, tmp_path):
        install_dir = tmp_path / "agent-install"
        install_dir.mkdir()
        (install_dir / ".env").write_text(
            "API_URL=https://old.example.com\n"
            "HOST_ID=old-id\n"
            "STP_AEE_LOCAL_ROOT=/mnt/old\n",
            encoding="utf-8",
        )
        out = _run_block(
            _env_block(),
            tmp_path=tmp_path,
            env=self._env(
                tmp_path, AGENT_NFS_ROOT="/mnt/nfs/new",
                AGENT_LOCAL_AEE_ROOT="/mnt/hdd/new",
            ),
        )
        assert out.returncode == 0, out.stderr
        values = _read_env(install_dir / ".env")
        assert values["STP_AEE_NFS_ROOT"] == "/mnt/nfs/new"
        assert values["STP_AEE_LOCAL_ROOT"] == "/mnt/hdd/new"

    def test_fresh_install_reports_the_share_as_a_mount_point(self, tmp_path):
        """MOUNT_POINTS 跟随中心存储：留空会让 /storage 与 S5 断言都看不到挂载。"""
        (tmp_path / "agent-install").mkdir()
        out = _run_block(
            _env_block(), tmp_path=tmp_path,
            env=self._env(tmp_path, AGENT_NFS_ROOT="/mnt/nfs/aee_events"),
        )
        assert out.returncode == 0, out.stderr
        assert _read_env(tmp_path / "agent-install/.env")["MOUNT_POINTS"] == "/mnt/nfs/aee_events"

    def test_fresh_install_without_a_share_leaves_mount_points_empty(self, tmp_path):
        (tmp_path / "agent-install").mkdir()
        out = _run_block(_env_block(), tmp_path=tmp_path, env=self._env(tmp_path))
        assert out.returncode == 0, out.stderr
        assert _read_env(tmp_path / "agent-install/.env")["MOUNT_POINTS"] == ""

    def test_update_fills_an_empty_mount_points_and_keeps_a_custom_one(self, tmp_path):
        """既有空值跟随中心存储；多挂载点站点自己写的值绝不覆盖。"""
        install_dir = tmp_path / "agent-install"
        install_dir.mkdir()
        (install_dir / ".env").write_text(
            "API_URL=https://old.example.com\nHOST_ID=old-id\nMOUNT_POINTS=\n", encoding="utf-8",
        )
        out = _run_block(
            _env_block(), tmp_path=tmp_path,
            env=self._env(tmp_path, AGENT_NFS_ROOT="/mnt/nfs/aee_events"),
        )
        assert out.returncode == 0, out.stderr
        assert _read_env(install_dir / ".env")["MOUNT_POINTS"] == "/mnt/nfs/aee_events"

        (install_dir / ".env").write_text(
            "API_URL=https://old.example.com\nHOST_ID=old-id\n"
            "MOUNT_POINTS=/mnt/data,/mnt/nfs/aee_events\n",
            encoding="utf-8",
        )
        out = _run_block(
            _env_block(), tmp_path=tmp_path,
            env=self._env(tmp_path, AGENT_NFS_ROOT="/mnt/nfs/aee_events"),
        )
        assert out.returncode == 0, out.stderr
        assert _read_env(install_dir / ".env")["MOUNT_POINTS"] == "/mnt/data,/mnt/nfs/aee_events"


class TestAeeDirectories:
    """#2181：安装链必须建好 AEE 本地根与共享挂载点，否则 Agent 静默回退 SSD。"""

    @staticmethod
    def _dirs_block() -> str:
        return _block(_DIRS_BLOCK_START, _DIRS_BLOCK_END)

    def _env(self, tmp_path: Path, **overrides: str) -> dict[str, str]:
        base = {
            "INSTALL_DIR": str(tmp_path / "agent-install"),
            "USER": os.environ.get("USER", "nobody"),
            "GROUP": os.environ.get("USER", "nobody"),
        }
        base.update(overrides)
        return base

    def _seed_env(self, tmp_path: Path, body: str) -> None:
        install_dir = tmp_path / "agent-install"
        install_dir.mkdir(exist_ok=True)
        (install_dir / ".env").write_text(body, encoding="utf-8")

    def test_prepares_the_local_root_and_the_mount_point(self, tmp_path):
        self._seed_env(
            tmp_path,
            f"STP_AEE_LOCAL_ROOT={tmp_path / 'var/aee_events'}\n"
            f"STP_AEE_NFS_ROOT={tmp_path / 'mnt/share'}\n",
        )
        out = _run_block(self._dirs_block(), tmp_path=tmp_path, env=self._env(tmp_path))

        assert out.returncode == 0, out.stderr
        assert (tmp_path / "var/aee_events").is_dir()
        assert (tmp_path / "mnt/share").is_dir()

    def test_unconfigured_paths_create_nothing(self, tmp_path):
        self._seed_env(tmp_path, "STP_AEE_LOCAL_ROOT=\nSTP_AEE_NFS_ROOT=\n")
        out = _run_block(self._dirs_block(), tmp_path=tmp_path, env=self._env(tmp_path))

        assert out.returncode == 0, out.stderr
        assert not (tmp_path / "var").exists() and not (tmp_path / "mnt").exists()

    def test_the_share_is_never_chowned_from_the_agent(self, tmp_path):
        """挂载点属主是存储端 S1 的约定；从 Agent 侧 chown 会把改动写进分享里。"""
        mount_point = tmp_path / "mnt/share"
        self._seed_env(
            tmp_path,
            f"STP_AEE_LOCAL_ROOT={tmp_path / 'var/aee_events'}\nSTP_AEE_NFS_ROOT={mount_point}\n",
        )
        log = tmp_path / "chown.log"

        out = _run_block(
            f'chown() {{ echo "$@" >> "{log}"; }}\n' + self._dirs_block(),
            tmp_path=tmp_path, env=self._env(tmp_path),
        )

        assert out.returncode == 0, out.stderr
        recorded = log.read_text(encoding="utf-8")
        assert str(tmp_path / "var/aee_events") in recorded
        assert str(mount_point) not in recorded


class TestScriptNonInteractiveSource:
    def test_no_prompt_outside_tty_guard(self):
        """read 只能出现在 [ -t 0 ] 分支内（无 TTY 时不得阻塞在提示上）。"""
        text = INSTALL_SCRIPT.read_text(encoding="utf-8")
        prompts = ('read -r -p "API_URL: "', 'read -r -p "请输入 HOST_ID')
        assert text.count("[ -t 0 ]") == len(prompts)
        for prompt in prompts:
            assert text.count(prompt) == 1
            idx = text.index(prompt)
            assert text.rfind("[ -t 0 ]", 0, idx) != -1, f"{prompt} 未包在 [ -t 0 ] 守卫内"


class TestInstallPlaybookContract:
    @staticmethod
    def _plays() -> list[dict]:
        return yaml.safe_load(INSTALL_PLAYBOOK.read_text(encoding="utf-8"))

    def _task(self, name: str) -> dict:
        for play in self._plays():
            for task in play.get("tasks", []) + play.get("pre_tasks", []):
                if task.get("name") == name:
                    return task
        raise AssertionError(f"task not found: {name}")

    def test_playbook_asserts_api_url_before_connecting(self):
        names = {
            task.get("name")
            for play in self._plays()
            for task in play.get("pre_tasks", [])
        }
        assert "Validate agent_api_url is injected" in names
        pre = self._task("Validate agent_api_url is injected")
        conditions = pre["ansible.builtin.assert"]["that"]
        assert any("agent_api_url" in str(c) for c in conditions)

    def test_install_task_passes_parameters_via_environment(self):
        task = self._task("Run install script non-interactively")
        env = task["environment"]
        assert env["AGENT_API_URL"] == "{{ agent_api_url }}"
        assert "agent_host_id" in env["AGENT_HOST_ID"]
        assert "agent_nfs_root" in env["AGENT_NFS_ROOT"]
        assert "agent_local_aee_root" in env["AGENT_LOCAL_AEE_ROOT"]

    def test_preinstall_staging_does_not_require_the_agent_user(self):
        """暂存目录不得 chown 到 agent 用户：该账号由安装脚本创建，此前不存在。

        全新主机上带 owner/group 会让第一个任务就以
        "failed to look up user android" 中断（I4 容器实验室实测）。
        """
        task = self._task("Ensure remote temp directory exists")
        options = task["ansible.builtin.file"]
        assert "owner" not in options and "group" not in options

    def test_install_playbook_publishes_deployment_digests(self):
        """全新安装也要落 ADR-0040 部署身份（否则 S5 只能看到空摘要）。"""
        compute = self._task("Compute deployment artifact digests (ADR-0040 P2)")
        cmd = compute["ansible.builtin.command"]["cmd"]
        # 与 update_agent.yml 同基准：同一脚本 + 同一 source-dir/schema-file
        assert "tools/ansible/compute_deploy_digest.py" in cmd
        assert "--source-dir {{ agent_source_dir }}" in cmd
        assert "--schema-file {{ stp_repo_root }}/backend/schemas/pipeline_schema.json" in cmd
        assert compute["delegate_to"] == "localhost"

    def test_share_is_mounted_after_the_install_script(self):
        """挂载点由安装脚本创建、运行账号也由它创建：挂载必须在脚本之后。"""
        names = [
            task.get("name")
            for play in self._plays()
            for task in play.get("tasks", [])
        ]
        script = names.index("Run install script non-interactively")
        assert script < names.index("Check whether the central share is already mounted")
        assert script < names.index("Mount the central share (best effort; the local fallback still works)")
        # Agent 服务在挂载后才重启，首次心跳就能上报挂载状态
        assert names.index("Mount the central share (best effort; the local fallback still works)") < \
            names.index("Restart agent service to load updated .env")

    def test_fstab_is_only_written_for_a_share_that_actually_mounted(self):
        """给从未连通的分享写 fstab 会留下永远失败的开机挂载；nofail 不解决记录本身错。"""
        persist = self._task("Persist the central share (nofail: boot must not depend on it)")
        conditions = " ".join(str(item) for item in persist["when"])
        assert "stp_nfs_check.rc" in conditions and "stp_nfs_mount.rc" in conditions
        assert "agent_nfs_server" in conditions
        line = persist["ansible.builtin.lineinfile"]["line"]
        assert "nofail" in line and "_netdev" in line

    def test_mounting_requires_a_declared_server(self):
        mount = self._task("Mount the central share (best effort; the local fallback still works)")
        conditions = " ".join(str(item) for item in mount["when"])
        assert "agent_nfs_server" in conditions and "agent_nfs_root" in conditions
        # regex_search 带分组返回**列表**：不取 first 会把 ["sha256:…"] 写进
        # ARTIFACT_DIGEST，Agent 校验失败即上报空摘要（I4 实验室实测）。
        extract = self._task("Extract deployment digests")
        facts = extract["ansible.builtin.set_fact"]
        for key in ("agent_code_artifact_digest", "agent_resources_artifact_digest"):
            assert facts[key].rstrip('"').endswith("| first") or "| first }}" in facts[key], facts[key]
        for name, digest in (
            ("Write agent ARTIFACT_DIGEST (ADR-0040 D2)", "agent_code_artifact_digest"),
            ("Write agent ARTIFACT_DIGEST_RESOURCES (ADR-0040 P2)", "agent_resources_artifact_digest"),
        ):
            task = self._task(name)
            options = task["ansible.builtin.copy"]
            assert digest in options["content"]
            assert options["owner"] == "{{ agent_user }}"
            assert options["mode"] == "0644"

    def test_install_task_is_not_a_pipe_into_read(self):
        text = INSTALL_PLAYBOOK.read_text(encoding="utf-8")
        assert "printf '%s\\n%s\\n'" not in text
        assert "| bash install_agent.sh" not in text
        task = self._task("Run install script non-interactively")
        assert task["ansible.builtin.command"]["cmd"] == "bash install_agent.sh"
