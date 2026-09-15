"""Install stages S1–S4 for the local site installer (I3).

Semantics follow the P1 design: no formatting, no overwriting unmanaged data,
no secret values in argv/logs/reports, idempotent re-runs that never rotate
keys, and a private administrator bootstrap before the service is exposed.
"""

from __future__ import annotations

import base64
import html
import json
import os
import re
import secrets
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from .bindings import BindingError, load_binding, require_keys
from .models import SiteConfig
from .ops import Ops
from .validation import Check, failure

BASE_DEPENDENCIES = ("python3", "systemctl", "nginx")

ENV_TEMPLATES = {
    "internal": "deploy/control-plane/env/.env.backend.internal.example",
    "production": "deploy/control-plane/env/.env.backend.example",
}

UNIT_TEMPLATES = (
    "deploy/control-plane/systemd/stability-backend-nomigrate.service",
    "deploy/control-plane/systemd/stability-backend-migrate.service",
)
# 站点导航页（S7/I5）：模板在发布物里，渲染结果落到 nginx 可读的站点目录。
NAVIGATION_TEMPLATE = "deploy/control-plane/navigation/index.html"
# 相对 ``ctx.system_root``；部署根 0750 不可被 nginx(www-data) 穿越，故独立目录。
NAVIGATION_SITE_DIR = "var/www/stability-site"
NAVIGATION_PLACEHOLDERS = (
    "<site-id>",
    "<site-display-name>",
    "<public-url>",
    "<site-contact>",
    "<documentation-url>",
    "<release-version>",
    "<rendered-at>",
)
# HTML 标签名不含短横线、env 注释含非 ASCII 尖括号：残留的 kebab-case 尖括号
# 必然是未替换的模板占位符（unit/nginx/navigation/env 同一词汇表）。
_UNRESOLVED_TEMPLATE_PLACEHOLDER = re.compile(r"<[a-z][a-z0-9]*(?:-[a-z0-9]+)+>")

# ── 站点本地监控栈（#2197）───────────────────────────────────────────────
# /storage 页的数据源：Prometheus（后端默认查 127.0.0.1:9091，见
# STP_PROMETHEUS_URL）+ node-exporter。复用发行版包与其 unit，只通过
# /etc/default 的 $ARGS 收窄监听面、打开页面需要的采集器——装完即出数据。
MONITORING_PACKAGES = ("prometheus", "prometheus-node-exporter")
# 判据用可执行文件名（发行版包同名）；只看包管理器状态会漏掉「已知但未安装」。
MONITORING_BINARIES = ("prometheus", "prometheus-node-exporter")
PROMETHEUS_UNIT = "prometheus"
NODE_EXPORTER_UNIT = "prometheus-node-exporter"
SAMPLER_UNIT = "stp-mem-top.timer"
# nfs-kernel-server 提供的导出命令：S1 靠它判断 NFS 服务端是否可用。
EXPORT_COMMAND = "exportfs"
PROMETHEUS_RETENTION = "30d"
NODE_EXPORTER_PORT = 9100
# 采样器落点，与 stp-mem-top.service 的 ReadWritePaths 保持一致。
TEXTFILE_DIR = "var/lib/prometheus/node-exporter"
# 发行版 unit 必须通过 EnvironmentFile 读 $ARGS；否则我们写的启动参数会被静默忽略，
# Prometheus 退回发行版默认（:9090 + 自带 prometheus.yml），页面空而报告是绿的。
UNIT_ARGS_MARKERS = ("$ARGS", "${ARGS}")
UNIT_DIRS = ("usr/lib/systemd/system", "lib/systemd/system")
MONITORING_CONFIGS = (
    ("deploy/prometheus/prometheus.yml", "etc/stp/prometheus/prometheus.yml", 0o644),
    ("deploy/prometheus/prometheus.default", "etc/default/prometheus", 0o644),
    ("deploy/prometheus/node-exporter.default", "etc/default/prometheus-node-exporter", 0o644),
)
# 宿主进程内存采样器：与上面同一批安装（textfile collector 的写入端）。
MONITORING_SAMPLER = (
    ("deploy/control-plane/node-exporter/stp-mem-top.sh", "usr/local/sbin/stp-mem-top", 0o755),
    ("deploy/control-plane/systemd/stp-mem-top.service", "etc/systemd/system/stp-mem-top.service", 0o644),
    ("deploy/control-plane/systemd/stp-mem-top.timer", "etc/systemd/system/stp-mem-top.timer", 0o644),
)

NGINX_SITES = {
    "internal": "deploy/control-plane/nginx/stability-platform.conf",
    "production": "deploy/control-plane/nginx/stability-platform-https.conf",
}
LOGROTATE_TEMPLATE = "deploy/control-plane/logrotate/stability-backend"
# #2088：本站写进共享系统路径的资产名（全局固定、无站点后缀——同机第二站点必然同名）。
NGINX_SITE_NAME = "stability-platform"
DEFAULT_SITE_NAME = "default"
# 发行版默认站点只停用不删除：移出 sites-enabled 即失效，放回原位即恢复。
DISABLED_DEFAULT_SITE_NAME = "stp-disabled-default"
LOGROTATE_TEMPLATE_NAME = Path(LOGROTATE_TEMPLATE).name
# 重跑覆盖共享路径前，旧内容留一份可回放的副本（state 目录 0700，不新增系统路径资产）。
PREVIOUS_ASSETS_DIR = "shared-path-prev"

def _effective_env_keys(text: str) -> set[str]:
    """systemd EnvironmentFile 真正会读取的键（注释与空行不算）。

    模板里的 `# STP_SCRIPT_RUNTIME_ROOT=…` 是合法注释、后端读不到；
    用子串比较会把这种注释当成"键已存在"（238 实测：假 PASS → 脚本同步
    admission 失败 `script_sync_config_error`）。
    """
    keys: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if key and not key[0].isdigit() and all(char.isalnum() or char == "_" for char in key):
            keys.add(key)
    return keys


def _append_env_keys(path: Path, values: dict[str, str]) -> None:
    """Append missing keys; existing values (secrets) stay untouched."""
    with path.open("a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={value}\n")
    os.chmod(path, 0o600)


# 自建中心存储：把本机子树以 NFS 导出给本站 Agent（Agent 的 STP_AEE_NFS_ROOT）。
# 写入身份统一映射到 EXPORT_ANON_UID（与 Agent 默认运行账号 android 一致），
# 只放开导出根这一层的属组与组写位——绝不递归改动既有数据。
EXPORT_ANON_UID = 1000
EXPORT_OPTIONS = (
    f"rw,sync,no_subtree_check,all_squash,anonuid={EXPORT_ANON_UID},anongid={EXPORT_ANON_UID}"
)
NFS_SERVER_PACKAGE = "nfs-kernel-server"
NFS_SERVER_UNIT = "nfs-server"
EXPORTS_DIR = "etc/exports.d"


def _export_client_specs(agents) -> tuple[list[str], int]:
    """Agent 目标 → NFS 客户端规格（IPv4 收敛到 /24）；返回 (规格, 无法推导数)。"""
    specs: list[str] = []
    unmapped = 0
    for agent in agents:
        parts = str(agent.target).split(".")
        if len(parts) == 4 and all(part.isdigit() for part in parts):
            spec = ".".join(parts[:3]) + ".0/24"
            if spec not in specs:
                specs.append(spec)
        else:
            unmapped += 1
    return specs, unmapped


def render_exports(config) -> str:
    """站点导出文件内容；尚无 Agent 时为空（首台接入后重跑即导出）。"""
    specs, unmapped = _export_client_specs(config.agents)
    if unmapped:
        # 主机名目标推不出网段：如实放宽到所有客户端（内网站点），检查消息里说明
        specs.append("*")
    if not specs:
        return ""
    clients = " ".join(f"{spec}({EXPORT_OPTIONS})" for spec in specs)
    return (
        f"# generated by stability-platform for site {config.site.id};"
        f" rewritten on every install run\n{config.storage.mount_path} {clients}\n"
    )


MANAGED_ENV_KEYS = (
    "DATABASE_URL",
    "REDIS_URL",
    "CORS_ORIGINS",
    "STP_ALLOW_REGISTER",
    "STP_SCRIPT_ROOT",
    "STP_SCRIPT_RUNTIME_ROOT",
    "STP_AEE_NFS_ROOT",
    # I4：Agent 首次安装的回连地址（控制面驱动 install 的注入源）。
    # 缺失时 POST /hosts/{id}/install 返回 400，不会静默装出连错站点的 Agent。
    "STP_AGENT_INSTALL_API_URL",
    # 站点级秘密：首次生成（见 GENERATED_SECRET_KEYS）且从不轮换
    "JWT_SECRET_KEY",
    "AGENT_SECRET",
    "WS_TOKEN",
    # SSH 口令加密键来自受保护绑定，留空会让密码型 Host 创建 503
    "SSH_CREDENTIALS_FERNET_KEY",
)

# 首次生成配置时由安装器生成一次的站点级秘密（重跑不轮换）。
# 与 plan 输出的 generated_secret_keys 同源；占位值不得留到可用站点里。
GENERATED_SECRET_KEYS = ("JWT_SECRET_KEY", "AGENT_SECRET", "WS_TOKEN")

SERVICE_UNIT = "stability-backend-nomigrate.service"


def _is_fernet_key(value: str) -> bool:
    """Fernet 键形状校验（32 字节 urlsafe-base64）——不引入 cryptography 依赖。"""
    try:
        return len(base64.urlsafe_b64decode(value.encode("ascii"))) == 32
    except (UnicodeError, ValueError, TypeError):
        return False


def generate_site_secret() -> str:
    return secrets.token_urlsafe(48)


@dataclass
class InstallContext:
    config: SiteConfig
    config_path: Path
    bundle: Path
    bindings_dir: Path
    state_dir: Path
    ops: Ops
    dry_run: bool = False
    db_probe: object | None = None
    render_root: Path | None = None
    system_root: Path = Path("/")
    binding_values: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def deploy_root(self) -> Path:
        return Path(self.config.control_plane.deploy_root)


def _pass(check_id: str, role: str, location: str, code: str, message: str, remediation: str) -> Check:
    return Check(check_id, role, "PASS", location, code, message, remediation)


def _safe(checks: list[Check], code: str, *, location: str, role: str, check_id: str) -> list[Check]:
    checks.append(failure(code, location=location, role=role, check_id=check_id))
    return checks


def load_bindings(ctx: InstallContext) -> list[Check]:
    """Read only the bindings the local install actually consumes."""
    config = ctx.config
    wanted: dict[str, set[str]] = {
        config.dependencies.database_ref: {"DATABASE_URL"},
        config.dependencies.redis_ref: {"REDIS_URL"},
        config.security.initial_admin_ref: {"USERNAME", "PASSWORD"},
        # SSH 口令以 Fernet 落库；键留空会让首次 POST /hosts（密码型）以 503 失败，
        # 即站点装好但无法用密码添加主机——所以绑定在 S0 就必须存在且形状合法。
        config.security.ssh_encryption_key_ref: {"SSH_CREDENTIALS_FERNET_KEY"},
    }
    if urlsplit(config.control_plane.public_url).scheme == "https" and config.control_plane.tls_ref:
        wanted[config.control_plane.tls_ref] = {"TLS_CERT_PATH", "TLS_KEY_PATH"}
    checks: list[Check] = []
    for ref, required in wanted.items():
        try:
            values = load_binding(ctx.bindings_dir, ref)
            require_keys(values, required)
        except BindingError as error:
            return _safe(checks, error.code, location="$.security", role="site", check_id="install.bindings")
        if "SSH_CREDENTIALS_FERNET_KEY" in values and not _is_fernet_key(values["SSH_CREDENTIALS_FERNET_KEY"]):
            return _safe(
                checks, "binding_content", location="$.security.ssh_encryption_key_ref",
                role="site", check_id="install.bindings",
            )
        ctx.binding_values[ref] = values
    checks.append(_pass(
        "install.bindings", "site", "$.security", "bindings_read",
        "Required bindings were read from the protected directory; values are never printed.",
        "Keep the bindings directory at 0700 with 0600 files.",
    ))
    return checks


def navigation_site_dir(ctx: InstallContext) -> Path:
    return ctx.system_root / NAVIGATION_SITE_DIR


def navigation_substitutions(ctx: InstallContext) -> dict[str, str]:
    """导航页替换值：只发布获准信息（站点身份/公开入口/负责人/文档/发布/时间）。

    全部 HTML 转义——显示名与负责人是自由文本，未转义会破坏页面结构。
    """
    config = ctx.config
    return {
        "<site-id>": html.escape(config.site.id, quote=True),
        "<site-display-name>": html.escape(config.site.display_name, quote=True),
        "<public-url>": html.escape(config.control_plane.public_url.rstrip("/"), quote=True),
        "<site-contact>": html.escape(config.navigation.contact, quote=True),
        "<documentation-url>": html.escape(config.navigation.documentation_url, quote=True),
        "<release-version>": html.escape(config.release.expected_release, quote=True),
        "<rendered-at>": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"),
    }


def render_navigation_page(template: str, ctx: InstallContext) -> str | None:
    """渲染导航页；仍有未替换占位符时返回 None（fail-closed）。"""
    text = _render(template, navigation_substitutions(ctx))
    return None if _UNRESOLVED_TEMPLATE_PLACEHOLDER.search(text) else text


def _render(text: str, substitutions: dict[str, str]) -> str:
    for placeholder, value in substitutions.items():
        text = text.replace(placeholder, value)
    return text


def _has_unresolved_placeholder(text: str) -> bool:
    return "<" in text and ">" in text


def template_substitutions(ctx: InstallContext) -> dict[str, str]:
    config = ctx.config
    substitutions = {
        "<deploy-root>": config.control_plane.deploy_root,
        "<deploy-user>": config.control_plane.deploy_user,
    }
    if urlsplit(config.control_plane.public_url).scheme == "https":
        tls = ctx.binding_values.get(config.control_plane.tls_ref or "", {})
        substitutions["<server-name>"] = urlsplit(config.control_plane.public_url).hostname or ""
        substitutions["<tls-cert-path>"] = tls.get("TLS_CERT_PATH", "")
        substitutions["<tls-key-path>"] = tls.get("TLS_KEY_PATH", "")
    if config.monitoring.enabled:
        # 监控栈模板（#2197）的占位符：只在这些文件里出现，其它模板不受影响。
        substitutions["<prometheus-port>"] = str(config.monitoring.prometheus_port)
        substitutions["<prometheus-retention>"] = PROMETHEUS_RETENTION
        substitutions["<site-id>"] = config.site.id
    return substitutions


def _unit_reads_args(ctx: InstallContext, unit: str) -> bool:
    """发行版 unit 是否真的通过 $ARGS 读 EnvironmentFile（写 /etc/default 的前提）。"""
    for directory in UNIT_DIRS:
        path = ctx.system_root / directory / f"{unit}.service"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if any(marker in text for marker in UNIT_ARGS_MARKERS):
            return True
    return False


def monitoring_artifacts() -> tuple[tuple[str, str, int], ...]:
    """监控栈要落地的 (发布物相对路径, system_root 相对路径, mode) 列表。"""
    return (*MONITORING_CONFIGS, *MONITORING_SAMPLER)


def await_monitoring(port: int, timeout_seconds: int = 30, interval_seconds: float = 3.0) -> bool:
    """Prometheus 是否已就绪（`/-/ready`）——装完但页面空着不算装上。"""
    target = f"http://127.0.0.1:{port}/-/ready"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with urllib.request.urlopen(target, timeout=5) as response:  # noqa: S310 (loopback)
                if response.status == 200:
                    return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_seconds)


def _write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    os.chmod(path, mode)


def _read_marker(root: Path) -> dict | None:
    marker_file = root / ".stp-site.json"
    if not marker_file.is_file():
        return None
    try:
        payload = json.loads(marker_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"site_id": ""}
    return payload if isinstance(payload, dict) else {"site_id": ""}


def stage_s1_basics(ctx: InstallContext) -> list[Check]:
    """Directories, service account, declared dependencies and mounted storage."""
    checks: list[Check] = []
    config = ctx.config
    release = ctx.ops.os_release()
    machine = ctx.ops.machine()
    expected = config.control_plane.os
    version_matches = release.get("VERSION_ID", "").split(".")[0] == expected.version.split(".")[0]
    if not version_matches or machine != config.platform.cpu_arch:
        return _safe(checks, "install_platform", location="$.platform", role="site", check_id="install.s1.platform")

    root = ctx.deploy_root
    marker = _read_marker(root)
    if root.exists() and any(root.iterdir()):
        if marker is None or marker.get("site_id") != config.site.id:
            return _safe(
                checks, "install_root_taken",
                location="$.control_plane.deploy_root", role="control_plane", check_id="install.s1.deploy_root",
            )
    if not ctx.dry_run:
        ctx.ops.ensure_dir(root, 0o750, config.control_plane.deploy_user)
        ctx.ops.ensure_dir(root / "logs", 0o750, config.control_plane.deploy_user)
        marker_payload = {
            "site_id": config.site.id,
            "display_name": config.site.display_name,
            "release": config.release.expected_release,
        }
        _write_text(root / ".stp-site.json", json.dumps(marker_payload, ensure_ascii=False, indent=2), mode=0o600)
        ctx.ops.chown(root / ".stp-site.json", config.control_plane.deploy_user)
    checks.append(_pass(
        "install.s1.deploy_root", "control_plane", "$.control_plane.deploy_root", "deploy_root_ready",
        "The dedicated deploy root and log directory exist with the declared owner.",
        "Keep the deploy root dedicated; never point it at a shared root.",
    ))

    if not ctx.ops.user_exists(config.control_plane.deploy_user):
        if not ctx.dry_run:
            ctx.ops.create_user(config.control_plane.deploy_user, config.control_plane.deploy_root)
        code, message = "service_user_created", "The dedicated non-root service account was created."
    else:
        code, message = "service_user_present", "The declared service account already exists."
    checks.append(_pass(
        "install.s1.service_user", "control_plane", "$.control_plane.deploy_user", code, message,
        "Do not reuse accounts that own other services.",
    ))

    if not ctx.ops.is_mount(Path(config.storage.mount_path)):
        return _safe(checks, "install_storage", location="$.storage.mount_path", role="storage", check_id="install.s1.storage")
    checks.append(_pass(
        "install.s1.storage", "storage", "$.storage.mount_path", "storage_mounted",
        "The declared central storage path is a mounted share.",
        "The installer never formats or creates shares; mount first.",
    ))

    missing = [name for name in BASE_DEPENDENCIES if not ctx.ops.command_exists(name)]
    if missing:
        return _safe(checks, "install_dependency", location="$.platform", role="site", check_id="install.s1.dependencies")
    checks.append(_pass(
        "install.s1.dependencies", "site", "$.platform", "dependencies_present",
        "Declared base dependencies are present on the target.",
        "Install missing base packages with the site profile before re-running.",
    ))

    if config.monitoring.enabled:
        # 站点本地监控栈（#2197）：包与 unit 都用发行版提供的那套，安装器只负责
        # 收窄监听面与打开页面需要的采集器（见 S2/S4）。
        if ctx.dry_run:
            checks.append(_pass(
                "install.s1.monitoring", "control_plane", "$.monitoring", "monitoring_planned",
                "Installing the site monitoring stack (Prometheus + node-exporter) is planned.",
                "The stack listens on loopback only; unpublish it from the site entry if not needed.",
            ))
        else:
            missing = [name for name in MONITORING_BINARIES if not ctx.ops.command_exists(name)]
            if missing and ctx.ops.run(
                ["apt-get", "install", "-y", *MONITORING_PACKAGES]
            ).returncode != 0:
                return _safe(
                    checks, "install_monitoring", location="$.monitoring.enabled",
                    role="control_plane", check_id="install.s1.monitoring",
                )
            if not all(ctx.ops.command_exists(name) for name in MONITORING_BINARIES):
                # 装完仍然找不到可执行文件：如实 FAIL，绝不让 S4 拿着不存在的服务往下走
                return _safe(
                    checks, "install_monitoring", location="$.monitoring.enabled",
                    role="control_plane", check_id="install.s1.monitoring",
                )
            checks.append(_pass(
                "install.s1.monitoring", "control_plane", "$.monitoring", "monitoring_installed",
                "Prometheus and node-exporter are installed and callable on the control plane.",
                "Keep them on loopback; the storage page reads them locally.",
            ))

    if config.storage.export_to_agents:
        # 自建中心存储：装上 NFS 服务端，并把导出根交给约定的写入身份（只此一层）。
        # 判据是「可执行文件在不在」而不是包管理器的状态：dpkg -l <pkg> 对「已知但
        # 未安装」的包同样返回 0，238 现场因此跳过安装、S2 调 exportfs 直接崩掉。
        if ctx.dry_run:
            checks.append(_pass(
                "install.s1.export", "storage", "$.storage.export_to_agents", "export_planned",
                "Installing the NFS server and handing the export root to the agreed identity are planned.",
                "Do not broaden the export beyond the declared Agents; keep it inside the site LAN.",
            ))
        else:
            if not ctx.ops.command_exists(EXPORT_COMMAND):
                installed = ctx.ops.run(["apt-get", "install", "-y", NFS_SERVER_PACKAGE])
                if installed.returncode != 0 or not ctx.ops.command_exists(EXPORT_COMMAND):
                    return _safe(
                        checks, "install_export", location="$.storage.export_to_agents",
                        role="storage", check_id="install.s1.export",
                    )
            ctx.ops.run(["chown", f"root:{EXPORT_ANON_UID}", config.storage.mount_path])
            ctx.ops.run(["chmod", "0775", config.storage.mount_path])
            checks.append(_pass(
                "install.s1.export", "storage", "$.storage.export_to_agents", "export_prepared",
                "The NFS server is callable and the export root is writable by the agreed identity.",
                "Do not broaden the export beyond the declared Agents; keep it inside the site LAN.",
            ))
    return checks


def _land_tree(source: Path, target: Path) -> None:
    """把发布子树原样落地（符号链接保持为链接）。

    ``shutil.copytree`` 默认解引用符号链接：落地树会多出源树里以链接存在的
    文件，ADR-0040 内容摘要随即与清单基准不一致（I4 实验室实测：
    backend/agent/CLAUDE.md 被实体化后部署摘要 ≠ 清单声明）。重跑时先清掉与
    源链接冲突的旧实体，否则 copytree 建链接会因目标已存在而失败。
    """
    for root, _dirs, files in os.walk(source):
        for name in files:
            candidate = Path(root) / name
            if not candidate.is_symlink():
                continue
            destination = target / candidate.relative_to(source)
            # copytree 建链接不做覆盖（同向链接也会 File exists），先删再建。
            if destination.is_symlink() or destination.exists():
                destination.unlink()
    shutil.copytree(source, target, dirs_exist_ok=True, symlinks=True)


def stage_s2_release_env(ctx: InstallContext) -> list[Check]:
    """Release tree landing, virtualenv/dependencies, generated env, templates."""
    checks: list[Check] = []
    config = ctx.config
    root = ctx.deploy_root
    bundle = ctx.bundle
    for relpath in ("release-manifest.json", "backend", "backend/agent", "backend/schemas", "frontend/dist-prod", "deploy", "tools"):
        if not (bundle / relpath).exists():
            return _safe(checks, "release_tree", location="$.release.bundle", role="site", check_id="install.s2.release")

    if not ctx.dry_run:
        for subdir in ("backend", "deploy", "tools", "frontend/dist-prod"):
            target = root / subdir
            target.parent.mkdir(parents=True, exist_ok=True)
            _land_tree(bundle / subdir, target)
    checks.append(_pass(
        "install.s2.release", "control_plane", "$.release.bundle", "release_landed",
        "Release tree landed under the deploy root with the documented layout.",
        "Re-run after fixing the bundle layout; never point the installer at an unpacked archive.",
    ))

    venv_python = root / "venv" / "bin" / "python"
    if venv_python.is_file():
        checks.append(_pass(
            "install.s2.venv", "control_plane", "$.dependencies", "venv_present",
            "Existing virtualenv is reused; no dependency step was repeated.",
            "Never delete the virtualenv to force upgrades; upgrade in place.",
        ))
    elif ctx.dry_run:
        checks.append(_pass(
            "install.s2.venv", "control_plane", "$.dependencies", "venv_planned",
            "Virtualenv creation and dependency installation are planned.",
            "Offline mode requires a wheelhouse inside the bundle.",
        ))
    else:
        result = ctx.ops.run(["/usr/bin/python3", "-m", "venv", str(root / "venv")])
        if result.returncode != 0:
            return _safe(checks, "install_command", location="$.dependencies", role="control_plane", check_id="install.s2.venv")
        wheelhouse = bundle / "wheelhouse"
        pip = str(root / "venv" / "bin" / "pip")
        args = [pip, "install", "--disable-pip-version-check", "-q"]
        if wheelhouse.is_dir():
            args += ["--no-index", "--find-links", str(wheelhouse)]
        elif config.network.dependency_mode == "offline":
            return _safe(checks, "release_tree", location="$.network.dependency_mode", role="site", check_id="install.s2.venv")
        args += ["-r", str(root / "backend" / "requirements.txt")]
        if ctx.ops.run(args, cwd=root).returncode != 0:
            return _safe(checks, "install_command", location="$.dependencies", role="control_plane", check_id="install.s2.venv")
        checks.append(_pass(
            "install.s2.venv", "control_plane", "$.dependencies", "venv_ready",
            "Virtualenv and Python dependencies were installed from the declared source.",
            "Offline installs must carry a wheelhouse.",
        ))

    substitutions = template_substitutions(ctx)
    if any(value == "" for value in substitutions.values()):
        return _safe(checks, "install_conflict", location="$.control_plane.public_url", role="control_plane", check_id="install.s2.env")
    env_values = {
        "DATABASE_URL": ctx.binding_values[config.dependencies.database_ref]["DATABASE_URL"],
        "REDIS_URL": ctx.binding_values[config.dependencies.redis_ref]["REDIS_URL"],
        "CORS_ORIGINS": config.control_plane.public_url.rstrip("/"),
        "STP_ALLOW_REGISTER": "0",
        "STP_SCRIPT_ROOT": str(root / "backend" / "agent" / "scripts"),
        # Agent 未声明时不写该键（先装控制面的路径）；首台 Agent 接入后再渲染。
        **(
            {"STP_SCRIPT_RUNTIME_ROOT": str(Path(config.agents[0].install_root) / "agent" / "scripts")}
            if config.agents else {}
        ),
        "STP_AEE_NFS_ROOT": config.storage.mount_path,
        # 站点级秘密：仅在首次生成时写一次，重跑走 env_reused 不轮换
        **{key: generate_site_secret() for key in GENERATED_SECRET_KEYS},
        "SSH_CREDENTIALS_FERNET_KEY": ctx.binding_values[
            config.security.ssh_encryption_key_ref
        ]["SSH_CREDENTIALS_FERNET_KEY"],
        # Agent 回连站点入口走公开地址（同 CORS_ORIGINS），不是 loopback。
        "STP_AGENT_INSTALL_API_URL": config.control_plane.public_url.rstrip("/"),
    }
    env_file = root / ".env.backend"
    if env_file.is_file():
        text = env_file.read_text(encoding="utf-8")
        managed = MANAGED_ENV_KEYS if config.agents else tuple(
            key for key in MANAGED_ENV_KEYS if key != "STP_SCRIPT_RUNTIME_ROOT"
        )
        present = _effective_env_keys(text)
        missing = [key for key in managed if key not in present]
        if not missing:
            checks.append(_pass(
                "install.s2.env", "control_plane", "$.security", "env_reused",
                "Existing environment already carries every managed key; keys were not rotated.",
                "Use a reviewed diff for changes; never delete the env to regenerate it.",
            ))
        elif missing == ["STP_SCRIPT_RUNTIME_ROOT"] and "STP_SCRIPT_RUNTIME_ROOT" in env_values:
            # 首装无 Agent 时该键保持模板注释形态；首台 Agent 接入后必须补成有效行，
            # 否则后端读不到 → 脚本同步 admission 失败（script_sync_config_error，
            # 238 实测）。只追加缺失键，既有秘密与值一个都不动。
            if not ctx.dry_run:
                _append_env_keys(env_file, {"STP_SCRIPT_RUNTIME_ROOT": env_values["STP_SCRIPT_RUNTIME_ROOT"]})
            checks.append(_pass(
                "install.s2.env", "control_plane", "$.security", "env_extended",
                "The script runtime root was appended now that an Agent is declared; S4 restarts the service.",
                "Existing values were preserved; only the missing key was added.",
            ))
        else:
            return _safe(checks, "install_conflict", location="$.security", role="control_plane", check_id="install.s2.env")
    elif ctx.dry_run:
        checks.append(_pass(
            "install.s2.env", "control_plane", "$.security", "env_planned",
            "Environment file generation is planned with owner-only permissions.",
            "Secrets are generated once and never rotated by re-runs.",
        ))
    else:
        template = (bundle / ENV_TEMPLATES[config.control_plane.security_profile]).read_text(encoding="utf-8")
        rendered_env = _apply_env_keys(template, env_values)
        # #2017 残口：占位值不得留到可用站点里。managed key 与模板键失配时，未替换的
        # 占位符会原样落进 .env.backend 且 S2 仍报 PASS——与 unit/nginx 同一守卫。
        if _UNRESOLVED_TEMPLATE_PLACEHOLDER.search(rendered_env):
            return _safe(
                checks, "install_conflict", location="$.security", role="control_plane",
                check_id="install.s2.env",
            )
        _write_text(env_file, rendered_env, mode=0o600)
        ctx.ops.chown(env_file, config.control_plane.deploy_user)
        checks.append(_pass(
            "install.s2.env", "control_plane", "$.security", "env_created",
            "Site environment was created with generated secrets and bound values.",
            "Re-runs never rotate keys; change values only through a reviewed diff.",
        ))

    render_root = ctx.render_root or (root / ".install-rendered")
    if not ctx.dry_run:
        render_root.mkdir(parents=True, exist_ok=True)
        sources = [*UNIT_TEMPLATES, NGINX_SITES[config.control_plane.security_profile], LOGROTATE_TEMPLATE]
        for relpath in sources:
            text = _render((bundle / relpath).read_text(encoding="utf-8"), substitutions)
            if _has_unresolved_placeholder(text):
                return _safe(checks, "install_conflict", location="$.control_plane.deploy_root", role="control_plane", check_id="install.s2.templates")
            _write_text(render_root / Path(relpath).name, text)
        marker = {"site_id": config.site.id, "display_name": config.site.display_name, "release": config.release.expected_release}
        _write_text(root / ".stp-site.json", json.dumps(marker, ensure_ascii=False, indent=2), mode=0o600)
        ctx.ops.chown(root / ".stp-site.json", config.control_plane.deploy_user)
        if config.monitoring.enabled:
            # 监控栈配置（#2197）。发行版 unit 必须以 $ARGS 读 /etc/default：读不到就
            # 不写——否则启动参数被静默忽略，Prometheus 退回发行版默认端口与配置，
            # /storage 永空而安装报告是绿的。
            for unit in (PROMETHEUS_UNIT, NODE_EXPORTER_UNIT):
                if not _unit_reads_args(ctx, unit):
                    return _safe(
                        checks, "install_monitoring", location="$.monitoring.enabled",
                        role="control_plane", check_id="install.s2.monitoring",
                    )
            for source, _destination, _mode in monitoring_artifacts():
                text = _render((bundle / source).read_text(encoding="utf-8"), substitutions)
                # 采样器是 shell 脚本（`<`/`>` 是重定向），故用精确的 kebab-case
                # 占位符判据，而不是 unit/nginx 模板那条「有尖括号即未替换」。
                if _UNRESOLVED_TEMPLATE_PLACEHOLDER.search(text):
                    return _safe(
                        checks, "install_conflict", location="$.monitoring.enabled",
                        role="control_plane", check_id="install.s2.monitoring",
                    )
                _write_text(render_root / Path(source).name, text)
        if config.storage.export_to_agents:
            exports_dir = ctx.system_root / EXPORTS_DIR
            exports_dir.mkdir(parents=True, exist_ok=True)
            _write_text(
                exports_dir / f"stp-{config.site.id}.exports",
                render_exports(config),
                mode=0o644,
            )
            if ctx.ops.run(["exportfs", "-ra"]).returncode != 0:
                return _safe(
                    checks, "install_export", location="$.storage.export_to_agents",
                    role="storage", check_id="install.s2.export",
                )
            if ctx.ops.run(["systemctl", "enable", "--now", NFS_SERVER_UNIT]).returncode != 0:
                return _safe(
                    checks, "install_export", location="$.storage.export_to_agents",
                    role="storage", check_id="install.s2.export",
                )
            # 仍以导出文件是否真的写出客户端为准：首装无 Agent 时它是空文件。
            published = bool(render_exports(config))
            checks.append(_pass(
                "install.s2.export", "storage", "$.storage.export_to_agents",
                "export_published" if published else "export_deferred",
                "The site exports its own storage subtree to the declared Agents over NFS."
                if published else
                "No Agent is declared yet; the export is deferred until the first one is onboarded.",
                "Re-run the install after adding Agents so their networks are allowed.",
            ))
    if config.monitoring.enabled:
        if ctx.dry_run:
            checks.append(_pass(
                "install.s2.monitoring", "control_plane", "$.monitoring", "monitoring_planned",
                "Scrape configuration, listener arguments and the host memory sampler are planned.",
                "Run without --dry-run to render and enable them.",
            ))
        else:
            checks.append(_pass(
                "install.s2.monitoring", "control_plane", "$.monitoring", "monitoring_rendered",
                "Scrape configuration and listener arguments were rendered from the site input.",
                "Keep Prometheus on loopback; the backend queries it at 127.0.0.1.",
            ))
    if ctx.dry_run and config.storage.export_to_agents:
        checks.append(_pass(
            "install.s2.export", "storage", "$.storage.export_to_agents", "export_planned",
            "Writing the site's export file and reloading the NFS server are planned.",
            "Re-run without --dry-run to publish; the client list follows the declared Agents.",
        ))
    checks.append(_pass(
        "install.s2.templates", "control_plane", "$.control_plane.deploy_root", "templates_rendered",
        "Templates were rendered without leftover placeholders and the site marker was written.",
        "Never copy templates verbatim; always render the placeholder set.",
    ))

    # 站点导航页（S7/I5）：只发布获准信息、无凭据；nginx 以 /site/ 提供它。
    nav_template = bundle / NAVIGATION_TEMPLATE
    if not nav_template.is_file():
        return _safe(checks, "release_tree", location="$.release.bundle", role="site", check_id="install.s2.navigation")
    rendered_nav = render_navigation_page(nav_template.read_text(encoding="utf-8"), ctx)
    if rendered_nav is None:
        return _safe(checks, "install_conflict", location="$.navigation", role="site", check_id="install.s2.navigation")
    if ctx.dry_run:
        checks.append(_pass(
            "install.s2.navigation", "site", "$.navigation", "navigation_planned",
            "Site navigation rendering is planned from the declared contact and documentation URL.",
            "Run without --dry-run on the confirmed target to publish the site entry.",
        ))
    else:
        site_dir = navigation_site_dir(ctx)
        site_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(site_dir, 0o755)
        _write_text(site_dir / "index.html", rendered_nav)
        os.chmod(site_dir / "index.html", 0o644)
        checks.append(_pass(
            "install.s2.navigation", "site", "$.navigation", "navigation_rendered",
            "The site navigation page was rendered with site identity, owner and documentation link only.",
            "Serve it read-only; never add credentials or internal addresses to the navigation page.",
        ))

    if not ctx.dry_run:
        ctx.ops.chown(root, config.control_plane.deploy_user)
    return checks


def _apply_env_keys(text: str, values: dict[str, str]) -> str:
    lines = text.splitlines()
    seen: set[str] = set()
    updated: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in values:
                updated.append(f"{key}={values[key]}")
                seen.add(key)
                continue
        updated.append(line)
    for key, value in values.items():
        if key not in seen:
            updated.append(f"{key}={value}")
    return "\n".join(updated) + "\n"


def stage_s3_database_admin(ctx: InstallContext, database_state: str, code_head: str | None) -> list[Check]:
    """Explicit migration and the controlled first-administrator bootstrap."""
    checks: list[Check] = []
    config = ctx.config
    if database_state == "unmanaged":
        return _safe(checks, "db_unmanaged", location="$.dependencies.database_ref", role="control_plane", check_id="install.s3.db")
    if database_state == "driver_missing":
        return _safe(checks, "db_driver", location="$.dependencies.database_ref", role="control_plane", check_id="install.s3.db")
    if database_state == "unreachable":
        return _safe(checks, "db_unreachable", location="$.dependencies.database_ref", role="control_plane", check_id="install.s3.db")
    if code_head and database_state == "at_head":
        checks.append(_pass(
            "install.s3.db", "control_plane", "$.dependencies.database_ref", "schema_at_head",
            "Declared database is already at the migrated state; no migration was applied.",
            "Never downgrade or wipe data to force a fresh install.",
        ))
    elif database_state in {"empty", "behind"}:
        if ctx.dry_run:
            checks.append(_pass(
                "install.s3.db", "control_plane", "$.dependencies.database_ref", "migration_planned",
                "Schema migration is planned through the existing Alembic chain.",
                "Never run migrations against an unmanaged database.",
            ))
        else:
            python = ctx.deploy_root / "venv" / "bin" / "python"
            env = _deploy_env(ctx, {
                "DATABASE_URL": ctx.binding_values[config.dependencies.database_ref]["DATABASE_URL"],
                "STP_SKIP_INFRA_CHECK": "1",
            })
            result = ctx.ops.run([str(python), "-m", "alembic", "upgrade", "head"], cwd=ctx.deploy_root / "backend", env=env)
            if result.returncode != 0:
                return _safe(checks, "db_migrate_failed", location="$.dependencies.database_ref", role="control_plane", check_id="install.s3.migrate")
            checks.append(_pass(
                "install.s3.migrate", "control_plane", "$.dependencies.database_ref", "migration_applied",
                "The existing migration chain completed against the declared database.",
                "A failed migration must stop the install before any service start.",
            ))
    else:
        checks.append(_pass(
            "install.s3.db", "control_plane", "$.dependencies.database_ref", "schema_at_head",
            "Declared database is already at the migrated state; no migration was applied.",
            "Never downgrade or wipe data to force a fresh install.",
        ))

    if ctx.dry_run:
        checks.append(_pass(
            "install.s3.admin", "site", "$.security.initial_admin_ref", "bootstrap_planned",
            "First-administrator bootstrap is planned as a controlled, audited action.",
            "Bootstrap never resets existing accounts or elevates ordinary users.",
        ))
        return checks
    python = ctx.deploy_root / "venv" / "bin" / "python"
    admin = ctx.binding_values[config.security.initial_admin_ref]
    env = _deploy_env(ctx, {
        "DATABASE_URL": ctx.binding_values[config.dependencies.database_ref]["DATABASE_URL"],
        "STP_INITIAL_ADMIN_USER": admin["USERNAME"],
        "STP_INITIAL_ADMIN_PASSWORD": admin["PASSWORD"],
        "STP_SKIP_INFRA_CHECK": "1",
    })
    result = ctx.ops.run([str(python), "backend/scripts/bootstrap_admin.py"], cwd=ctx.deploy_root, env=env)
    if result.returncode == 3:
        return _safe(checks, "admin_conflict", location="$.security.initial_admin_ref", role="site", check_id="install.s3.admin")
    if result.returncode != 0:
        return _safe(checks, "install_command", location="$.security.initial_admin_ref", role="site", check_id="install.s3.admin")
    status = "created" if "created" in result.stdout else "exists"
    if result.stdout:
        try:
            payload = json.loads(result.stdout.splitlines()[-1])
            status = str(payload.get("status", status))
        except (ValueError, IndexError):
            pass
    checks.append(_pass(
        "install.s3.admin", "site", "$.security.initial_admin_ref",
        "admin_created" if status == "created" else "admin_present",
        "First administrator ensured exactly once with an audit record; existing accounts untouched.",
        "Bootstrap is idempotent and never resets or elevates existing users.",
    ))
    return checks


def _subprocess_env(values: dict[str, str]) -> dict[str, str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(values)
    return env


def _deploy_env(ctx: InstallContext, overrides: dict[str, str]) -> dict[str, str]:
    """Minimal env for target-side commands, built from the rendered site env.

    The rendered ``.env.backend`` carries the site's generated secrets and
    settings; target-side scripts must import cleanly with the same
    environment the service uses.  Values stay in the subprocess environment
    and are never printed.
    """
    env = _subprocess_env({})
    env_file = ctx.deploy_root / ".env.backend"
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    except OSError:
        pass
    env.update(overrides)
    return env


def shared_path_is_foreign(ctx: InstallContext, destination: Path) -> bool:
    """共享系统路径上的既有文件是否属于本站（#2088，fail-closed）。

    unit / nginx / logrotate 三类产物都由 ``<deploy-root>`` 渲染，故「引用本站部署根」
    既是归属证据也是可重跑判据；读不到或不含本站部署根即不是本站资产。服务名与站点名
    全局固定，同机第二站点必然同名——无归属判据的覆盖会把别站服务改指本站部署根。
    """
    try:
        text = destination.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return destination.exists()
    return ctx.deploy_root.as_posix() not in text


def install_shared_asset(ctx: InstallContext, source: Path, destination: Path) -> None:
    """装一个共享系统路径资产：本站既有内容先留副本到 state 目录，再覆盖。"""
    if destination.exists():
        backup_dir = ctx.state_dir / PREVIOUS_ASSETS_DIR
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(destination, backup_dir / destination.name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)


def stage_s4_entry(ctx: InstallContext) -> list[Check]:
    """Install units/nginx, start the nomigrate service and verify health."""
    checks: list[Check] = []
    config = ctx.config
    render_root = ctx.render_root or (ctx.deploy_root / ".install-rendered")
    profile = config.control_plane.security_profile
    shared_assets = [
        *(
            (render_root / Path(relpath).name, ctx.system_root / "etc/systemd/system" / Path(relpath).name)
            for relpath in UNIT_TEMPLATES
        ),
        (
            render_root / Path(NGINX_SITES[profile]).name,
            ctx.system_root / "etc/nginx/sites-available" / NGINX_SITE_NAME,
        ),
        (render_root / LOGROTATE_TEMPLATE_NAME, ctx.system_root / "etc/logrotate.d" / LOGROTATE_TEMPLATE_NAME),
        *(
            (render_root / Path(source).name, ctx.system_root / destination)
            for source, destination, _mode in (
                monitoring_artifacts() if config.monitoring.enabled else ()
            )
        ),
    ]
    # #2088：先全量判归属再写入——服务名与站点名全局固定，同机第二站点必然同名；无归属
    # 判据的覆盖会把别站服务改指本站部署根，违本模块「no overwriting unmanaged data」。
    if any(shared_path_is_foreign(ctx, destination) for _, destination in shared_assets):
        return _safe(
            checks, "install_conflict", location="$.control_plane.deploy_root",
            role="control_plane", check_id="install.s4.shared_paths",
        )
    if ctx.dry_run:
        return [
            _pass(
                "install.s4.units", "control_plane", "$.control_plane.deploy_root", "units_planned",
                "systemd unit installation and service start are planned.",
                "Run without --dry-run on the declared target.",
            ),
            _pass(
                "install.s4.nginx", "control_plane", "$.control_plane.public_url", "nginx_planned",
                "Nginx site installation and reload are planned after the syntax check.",
                "The service starts before the public entry is exposed.",
            ),
        ]
    # Migration and other target-side commands ran as root; hand the tree back to
    # the service account before the service starts, or the app cannot write
    # its own caches and crash-loops.
    ctx.ops.chown(ctx.deploy_root, config.control_plane.deploy_user)
    for relpath in UNIT_TEMPLATES:
        destination = ctx.system_root / "etc/systemd/system" / Path(relpath).name
        install_shared_asset(ctx, render_root / Path(relpath).name, destination)
    if config.monitoring.enabled:
        # 监控栈产物（配置 + 采样器脚本/单元）与 unit 同批落地，随后统一 daemon-reload。
        for source, destination, mode in monitoring_artifacts():
            target = ctx.system_root / destination
            install_shared_asset(ctx, render_root / Path(source).name, target)
            os.chmod(target, mode)
    if ctx.ops.run(["systemctl", "daemon-reload"]).returncode != 0:
        return _safe(checks, "install_units", location="$.control_plane.deploy_root", role="control_plane", check_id="install.s4.units")
    checks.append(_pass(
        "install.s4.units", "control_plane", "$.control_plane.deploy_root", "units_installed",
        "Migration oneshot and nomigrate service units are installed.",
        "Units must be rendered from the template placeholder set.",
    ))

    nginx_target = ctx.system_root / "etc/nginx/sites-available" / NGINX_SITE_NAME
    install_shared_asset(ctx, render_root / Path(NGINX_SITES[profile]).name, nginx_target)
    enabled = ctx.system_root / "etc/nginx/sites-enabled" / NGINX_SITE_NAME
    enabled.parent.mkdir(parents=True, exist_ok=True)
    if enabled.is_symlink() or enabled.exists():
        enabled.unlink()
    enabled.symlink_to(nginx_target)
    default_site = ctx.system_root / "etc/nginx/sites-enabled" / DEFAULT_SITE_NAME
    if default_site.is_symlink() or default_site.exists():
        # 发行版资产不是本站资产：只从 sites-enabled 移出（nginx 只 include 该目录），
        # 放回原位即恢复，不删除内容。
        default_site.replace(ctx.system_root / "etc/nginx/sites-available" / DISABLED_DEFAULT_SITE_NAME)
    logrotate_target = ctx.system_root / "etc/logrotate.d" / LOGROTATE_TEMPLATE_NAME
    install_shared_asset(ctx, render_root / LOGROTATE_TEMPLATE_NAME, logrotate_target)
    ensure_frontend_readable(ctx)
    if ctx.ops.run(["nginx", "-t"]).returncode != 0:
        return _safe(checks, "install_nginx", location="$.control_plane.public_url", role="control_plane", check_id="install.s4.nginx")
    if ctx.ops.run(["systemctl", "enable", "--now", "nginx"]).returncode != 0:
        return _safe(checks, "install_nginx", location="$.control_plane.public_url", role="control_plane", check_id="install.s4.nginx")
    if ctx.ops.run(["systemctl", "reload", "nginx"]).returncode != 0:
        return _safe(checks, "install_nginx", location="$.control_plane.public_url", role="control_plane", check_id="install.s4.nginx")
    checks.append(_pass(
        "install.s4.nginx", "control_plane", "$.control_plane.public_url", "nginx_ready",
        "Nginx site passed the syntax check and was reloaded.",
        "Public entry stays same-origin with the control plane.",
    ))

    if ctx.ops.run(["systemctl", "enable", SERVICE_UNIT]).returncode != 0:
        return _safe(checks, "install_units", location="$.control_plane.deploy_root", role="control_plane", check_id="install.s4.service")
    # restart 而非 enable --now：EnvironmentFile 只在启动时读取，S2 可能刚补齐了
    # 脚本同步键（env_extended）——不重启就会带着旧 env 跑，S6 受控链必挂。
    if ctx.ops.run(["systemctl", "restart", SERVICE_UNIT]).returncode != 0:
        return _safe(checks, "install_units", location="$.control_plane.deploy_root", role="control_plane", check_id="install.s4.service")
    if not await_health():
        return _safe(checks, "install_health", location="$.control_plane.public_url", role="control_plane", check_id="install.s4.health")
    if not await_frontend(ctx):
        return _safe(checks, "install_frontend", location="$.control_plane.public_url", role="control_plane", check_id="install.s4.frontend")
    checks.append(_pass(
        "install.s4.frontend", "control_plane", "$.control_plane.public_url", "frontend_served",
        "The site entry serves the front-end bundle with the declared public URL.",
        "Keep the deploy root traversable for the web server; never widen file permissions.",
    ))
    checks.append(_pass(
        "install.s4.health", "control_plane", "$.control_plane.public_url", "health_ok",
        "Health reports ready workers and a schema aligned with the installed code.",
        "Health is partial evidence: log in and run the controlled drill before handover.",
    ))

    if config.monitoring.enabled:
        # 监控栈收口（#2197）：采样器落点、三个单元、`/-/ready` 实测——只 enable
        # 不算装上，页面读的是 Prometheus 的查询接口。
        textfile_dir = ctx.system_root / TEXTFILE_DIR
        textfile_dir.mkdir(parents=True, exist_ok=True)
        for unit in (NODE_EXPORTER_UNIT, PROMETHEUS_UNIT, SAMPLER_UNIT):
            if ctx.ops.run(["systemctl", "enable", "--now", unit]).returncode != 0:
                return _safe(
                    checks, "install_monitoring", location="$.monitoring.enabled",
                    role="control_plane", check_id="install.s4.monitoring",
                )
        if not await_monitoring(config.monitoring.prometheus_port):
            return _safe(
                checks, "install_monitoring", location="$.monitoring.enabled",
                role="control_plane", check_id="install.s4.monitoring",
            )
        checks.append(_pass(
            "install.s4.monitoring", "control_plane", "$.monitoring", "monitoring_ready",
            "Prometheus answers on loopback and the local node-exporter is scraped.",
            "The storage page reads this endpoint; keep the stack on loopback.",
        ))
    return checks


def ensure_frontend_readable(ctx: InstallContext) -> None:
    """让 nginx(www-data) 能穿越到前端产物：只放开目录的穿越位，文件权限不动。

    部署根默认 0750、属服务账号；nginx 以非特权用户运行时，`GET /` 会因
    stat 权限不足返回 404（I5 实验室实测）。`.env.backend` 等 0600 文件仍不可读。
    """
    directories = [ctx.deploy_root, ctx.deploy_root / "frontend"]
    dist = ctx.deploy_root / "frontend" / "dist-prod"
    if dist.is_dir():
        directories.append(dist)
        directories.extend(sorted(path for path in dist.rglob("*") if path.is_dir()))
    for directory in directories:
        try:
            os.chmod(directory, 0o755)
        except OSError:
            continue


def await_frontend(ctx: InstallContext, timeout_seconds: int = 30, interval_seconds: float = 3.0) -> bool:
    """站点入口的前端是否真的可服务（S4 收口：`/` 必须 200）。"""
    target = ctx.config.control_plane.public_url.rstrip("/") + "/"
    deadline = time.monotonic() + timeout_seconds
    while True:
        try:
            with urllib.request.urlopen(target, timeout=5) as response:  # noqa: S310 (declared site entry)
                if response.status == 200:
                    return True
        except Exception:
            pass
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval_seconds)


def await_health(timeout_seconds: int = 90, interval_seconds: float = 3.0) -> bool:
    import time

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5) as response:  # noqa: S310 (loopback)
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, ValueError, OSError):
            time.sleep(interval_seconds)
            continue
        data = payload.get("data", payload) if isinstance(payload, dict) else {}
        if isinstance(data, dict) and data.get("status") == "healthy" and data.get("saq_ready") is True:
            revision, head = data.get("alembic_revision"), data.get("alembic_head")
            if not (revision and head and revision != head):
                return True
        time.sleep(interval_seconds)
    return False
