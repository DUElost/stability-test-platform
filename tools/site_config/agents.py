"""S5 Agent onboarding over the site's own control-plane API (I4).

Runs on the control-plane target after S4.  It drives the *existing* Host API and
Ansible install chain instead of adding a second installation path: the site tool
reconciles Host rows, triggers ``POST /hosts/{id}/install`` and then asserts the
deployed Agent identity.  It never edits Agent ``.env`` files directly, so
protected keys stay owned by the platform (S5 failure rules in design §5).

Every step is fail-closed and idempotent: re-running re-reads actual API state
instead of trusting recorded progress, a lost response is resolved by looking the
Host up again, and a second trigger attaches to the run already in progress
instead of starting another installation.
"""

from __future__ import annotations

import json
import pwd
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .bindings import BindingError, load_binding
from .manifest import load_release_manifest
from .stages import InstallContext
from .validation import ConfigValidationError, Check, blocked, failure, passed

REQUEST_TIMEOUT_SECONDS = 15.0
INSTALL_POLL_TIMEOUT_SECONDS = 900.0
INSTALL_POLL_INTERVAL_SECONDS = 5.0
# 部署摘要在安装脚本之后由 playbook 写入，Agent 于**下一次心跳**才上报；
# 断言必须给一个有界等待窗，否则会把「还没上报」误判成「没有身份」。
DIGEST_WAIT_SECONDS = 90.0
# Heartbeats arrive every POLL_INTERVAL (10s) and the platform marks a Host
# OFFLINE after HOST_HEARTBEAT_TIMEOUT_SECONDS (300s).  Assert on a window that
# is comfortably inside the platform's own liveness rule.
AGENT_HEARTBEAT_FRESHNESS_SECONDS = 120.0

_AGENT_BINDING_SHAPES = (
    frozenset({"USERNAME", "PASSWORD"}),
    frozenset({"USERNAME", "PRIVATE_KEY_PATH"}),
)


class ApiError(RuntimeError):
    """The site API could not be reached or answered with an unusable payload."""

    def __init__(self, code: str, *, status: int | None = None, message: str = ""):
        self.code = code
        self.status = status
        self.message = message
        super().__init__(code)


class ApiClient(Protocol):
    """The subset of the site API used by S5/S6 (injectable for tests)."""

    def login(self, username: str, password: str) -> str: ...

    def list_hosts(self) -> list[dict[str, Any]]: ...

    def create_host(self, payload: dict[str, Any]) -> tuple[int, Any]: ...

    def request_install(self, host_id: str, options: dict[str, str]) -> tuple[int, Any]: ...

    def install_status(self, host_id: str) -> dict[str, Any]: ...

    def get_host(self, host_id: str) -> dict[str, Any]: ...

    def list_devices(self) -> list[dict[str, Any]]: ...

    def install_audit(self, host_id: str) -> list[dict[str, Any]]: ...

    # S6（verify.py）复用的部分：受控主链驱动
    def list_specialties(self) -> list[dict[str, Any]]: ...

    def create_plan(self, payload: dict[str, Any]) -> tuple[int, Any]: ...

    def run_plan(self, plan_id: int, device_ids: list[int]) -> tuple[int, Any]: ...

    def plan_run(self, run_id: int) -> dict[str, Any]: ...

    def plan_run_jobs(self, run_id: int) -> list[dict[str, Any]]: ...

    def plan_run_events(self, run_id: int) -> list[dict[str, Any]]: ...

    def raw_write_probe(self, path: str) -> tuple[int, Any]: ...

    def scan_scripts(self) -> tuple[int, Any]: ...

    def fetch_navigation(self) -> tuple[int, str]: ...


class HttpApiClient:
    """Minimal JSON client for the site's public entry (standard library only)."""

    def __init__(
        self,
        base_url: str,
        *,
        origin: str | None = None,
        timeout: float = REQUEST_TIMEOUT_SECONDS,
        context: ssl.SSLContext | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        # CSRF middleware matches the browser Origin against CORS_ORIGINS; a
        # Bearer token is exempt by design, the header is sent anyway.
        self.origin = (origin or self.base_url).rstrip("/")
        self.timeout = timeout
        self.context = context if context is not None else ssl.create_default_context()
        self._token: str | None = None

    # ── transport ────────────────────────────────────────────────────────
    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        json_body: dict[str, Any] | None = None,
        form: dict[str, str] | None = None,
        with_origin: bool = True,
    ) -> tuple[int, Any]:
        url = f"{self.base_url}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if with_origin:
            headers["Origin"] = self.origin
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif form is not None:
            data = urllib.parse.urlencode(form).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout, context=self.context) as response:
                return response.status, _decode(response.read())
        except urllib.error.HTTPError as error:
            return error.code, _decode(error.read())
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError) as exc:
            raise ApiError("api_unreachable", message=str(exc)) from None

    # ── API surface ──────────────────────────────────────────────────────
    def login(self, username: str, password: str) -> str:
        status, payload = self._request(
            "POST", "/api/v1/auth/token", form={"username": username, "password": password},
        )
        if status != 200 or not isinstance(payload, dict):
            raise ApiError("api_auth", status=status)
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ApiError("api_auth", status=status)
        self._token = token
        return token

    def list_hosts(self) -> list[dict[str, Any]]:
        # include_retired: a retired Host would otherwise be invisible here and
        # surface later as an unexplained identity conflict on create.
        status, payload = self._request("GET", "/api/v1/hosts?include_retired=true", token=self._token)
        if status != 200 or not isinstance(payload, list):
            raise ApiError("api_response", status=status)
        return [item for item in payload if isinstance(item, dict)]

    def create_host(self, payload: dict[str, Any]) -> tuple[int, Any]:
        return self._request("POST", "/api/v1/hosts", json_body=payload, token=self._token)

    def request_install(self, host_id: str, options: dict[str, str]) -> tuple[int, Any]:
        body = {"install_options": options} if options else None
        return self._request(
            "POST", f"/api/v1/hosts/{host_id}/install", json_body=body, token=self._token,
        )

    def install_status(self, host_id: str) -> dict[str, Any]:
        status, payload = self._request(
            "GET", f"/api/v1/hosts/{host_id}/install/status", token=self._token,
        )
        if status != 200 or not isinstance(payload, dict):
            raise ApiError("api_response", status=status)
        return payload

    def get_host(self, host_id: str) -> dict[str, Any]:
        status, payload = self._request("GET", f"/api/v1/hosts/{host_id}", token=self._token)
        if status != 200 or not isinstance(payload, dict):
            raise ApiError("api_response", status=status)
        return payload

    def list_devices(self) -> list[dict[str, Any]]:
        status, payload = self._request("GET", "/api/v1/devices?skip=0&limit=1200", token=self._token)
        if status != 200:
            raise ApiError("api_response", status=status)
        items = payload.get("items", []) if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            raise ApiError("api_response", status=status)
        return [item for item in items if isinstance(item, dict)]

    def install_audit(self, host_id: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(
            {"action": "install_agent_request", "resource_id": host_id, "skip": 0, "limit": 50}
        )
        status, payload = self._request("GET", f"/api/v1/audit-logs?{query}", token=self._token)
        if status != 200:
            raise ApiError("api_response", status=status)
        items = payload.get("items", []) if isinstance(payload, dict) else []
        if not isinstance(items, list):
            raise ApiError("api_response", status=status)
        return [item for item in items if isinstance(item, dict)]

    # ── S6 controlled chain (used by verify.py) ──────────────────────────
    def _data(self, path: str, *, method: str = "GET", json_body: dict[str, Any] | None = None) -> Any:
        """Unwrap the ``ApiResponse`` envelope (``{"data": ..., "error": ...}``)."""
        status, payload = self._request(method, path, token=self._token, json_body=json_body)
        if not isinstance(payload, dict):
            return status, payload
        if payload.get("error"):
            return status, payload
        return status, payload.get("data")

    def list_specialties(self) -> list[dict[str, Any]]:
        status, data = self._data("/api/v1/specialties")
        if status != 200 or not isinstance(data, list):
            raise ApiError("api_response", status=status)
        return [item for item in data if isinstance(item, dict)]

    def create_plan(self, payload: dict[str, Any]) -> tuple[int, Any]:
        return self._data("/api/v1/plans", method="POST", json_body=payload)

    def run_plan(self, plan_id: int, device_ids: list[int]) -> tuple[int, Any]:
        return self._data(
            f"/api/v1/plans/{plan_id}/run", method="POST", json_body={"device_ids": device_ids},
        )

    def plan_run(self, run_id: int) -> dict[str, Any]:
        status, data = self._data(f"/api/v1/plan-runs/{run_id}")
        if status != 200 or not isinstance(data, dict):
            raise ApiError("api_response", status=status)
        return data

    def plan_run_jobs(self, run_id: int) -> list[dict[str, Any]]:
        """Job 明细必须走专用端点：PlanRun 详情内嵌的 jobs 不带 step_traces。"""
        status, data = self._data(f"/api/v1/plan-runs/{run_id}/jobs")
        if status != 200 or not isinstance(data, list):
            raise ApiError("api_response", status=status)
        return [job for job in data if isinstance(job, dict)]


    def plan_run_events(self, run_id: int) -> list[dict[str, Any]]:
        status, data = self._data(f"/api/v1/plan-runs/{run_id}/events")
        if status != 200 or not isinstance(data, dict):
            raise ApiError("api_response", status=status)
        events = data.get("events", [])
        if not isinstance(events, list):
            raise ApiError("api_response", status=status)
        return [event for event in events if isinstance(event, dict)]

    def raw_write_probe(self, path: str) -> tuple[int, Any]:
        """Unauthenticated write without Origin/Referer: the CSRF guard must refuse it."""
        return self._request("POST", path, json_body={}, with_origin=False)

    def scan_scripts(self) -> tuple[int, Any]:
        """Register the control plane's script root into the site catalog (idempotent)."""
        return self._data("/api/v1/scripts/scan", method="POST")

    def fetch_navigation(self) -> tuple[int, str]:
        """GET the site navigation page (read-only, unauthenticated, no Origin)."""
        try:
            with urllib.request.urlopen(
                f"{self.base_url}/site/", timeout=self.timeout, context=self.context,
            ) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as error:
            return error.code, ""
        except (urllib.error.URLError, ssl.SSLError, TimeoutError, OSError):
            raise ApiError("api_unreachable", message="navigation entry") from None


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError):
        return None


def _fail(check_id: str, code: str, *, location: str, role: str = "agent") -> Check:
    return failure(code, location=location, role=role, check_id=check_id)


# ── binding + target checks ──────────────────────────────────────────────


def _agent_binding_problem(directory: Path, ref: str) -> tuple[dict[str, str], str | None]:
    try:
        values = load_binding(directory, ref)
    except BindingError as error:
        return {}, error.code
    if frozenset(values) not in _AGENT_BINDING_SHAPES:
        return values, "binding_content"
    return values, None


def _key_permission_problem(path_text: str, deploy_user: str) -> str | None:
    """Reject private keys the control-plane service could not read (fail-closed)."""
    path = Path(path_text)
    if not path.is_absolute():
        return "binding_content"
    try:
        info = path.stat()
    except OSError:
        return "binding_content"
    if not path.is_file():
        return "binding_content"
    if info.st_mode & 0o077:
        return "agent_key_permissions"
    # 0600 means only the owner can read: the owner must be the service account
    # that Ansible runs as, otherwise the install fails later with a bare
    # "Permission denied (publickey)".
    try:
        owner = pwd.getpwnam(deploy_user).pw_uid
    except KeyError:
        return None
    if info.st_uid != owner:
        return "agent_key_permissions"
    return None


def host_field(host: dict[str, Any], *names: str) -> str:
    for name in names:
        value = host.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# ── Host reconciliation ──────────────────────────────────────────────────


def _create_payload(
    *, name: str, ip: str, binding: dict[str, str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "ip": ip, "ssh_port": 22, "ssh_user": binding["USERNAME"]}
    if "PASSWORD" in binding:
        payload["ssh_auth_type"] = "password"
        payload["ssh_password"] = binding["PASSWORD"]
    else:
        payload["ssh_auth_type"] = "key"
        payload["ssh_key_path"] = binding["PRIVATE_KEY_PATH"]
    return payload


def _match_host(hosts: list[dict[str, Any]], ip: str) -> dict[str, Any] | None:
    for host in hosts:
        if host_field(host, "ip", "ip_address") == ip:
            return host
    return None


def _match_name(hosts: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for host in hosts:
        if host_field(host, "name") == name:
            return host
    return None


def reconcile_host(
    api: ApiClient,
    *,
    name: str,
    ip: str,
    binding: dict[str, str],
    location: str = "$.agents",
) -> tuple[str | None, Check | None]:
    """Return an existing/created Host ID, or (None, failure).

    响应丢失后重试走同一条路径：先按实际 API 状态判定，只有确实不存在才创建。
    """
    try:
        existing = _match_host(api.list_hosts(), ip)
    except ApiError as error:
        return None, _fail("install.s5.host", error.code, location="$.control_plane.public_url",
                           role="control_plane")
    if existing is not None:
        return _reuse_host(existing, name=name, location=location)
    status, payload = api.create_host(_create_payload(name=name, ip=ip, binding=binding))
    if status == 200 and isinstance(payload, dict) and host_field(payload, "id"):
        return host_field(payload, "id"), None
    if status == 409:
        # 并发或响应丢失：重新查询一次，仍无匹配则按真实冲突处理。
        try:
            hosts = api.list_hosts()
        except ApiError as error:
            return None, _fail("install.s5.host", error.code, location="$.control_plane.public_url",
                               role="control_plane")
        raced = _match_host(hosts, ip)
        if raced is not None:
            return _reuse_host(raced, name=name, location=location)
        if _match_name(hosts, name) is not None:
            # 同名占用了另一个地址：站点命名冲突，不能改名绕过。
            return None, _fail("install.s5.host", "host_conflict", location=location)
    return None, _fail("install.s5.host", "host_create_failed", location=location)


def _reuse_host(host: dict[str, Any], *, name: str, location: str) -> tuple[str | None, Check | None]:
    if host_field(host, "retired_at"):
        return None, _fail("install.s5.host", "host_retired", location=location)
    if host_field(host, "name") != name:
        return None, _fail("install.s5.host", "host_conflict", location=location)
    return host_field(host, "id"), None


# ── install trigger + completion ─────────────────────────────────────────


def trigger_install(
    api: ApiClient,
    host_id: str,
    options: dict[str, str],
) -> tuple[str | None, Check | None]:
    """Start (or attach to) the Agent install run for one Host."""
    status, payload = api.request_install(host_id, options)
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if status == 200 and isinstance(payload, dict):
        console_run_id = payload.get("console_run_id")
        if isinstance(console_run_id, str) and console_run_id:
            return console_run_id, None
        return None, _fail("install.s5.install", "install_trigger_failed", location="$.agents")
    if status == 409 and isinstance(detail, dict) and detail.get("console_run_id"):
        # 已在安装中：接上现有 run，不启动第二个 ansible（幂等重试语义）。
        return str(detail["console_run_id"]), None
    if status == 400:
        code = detail.get("code") if isinstance(detail, dict) else None
        if code == "AGENT_INSTALL_NOT_CONFIGURED":
            return None, _fail("install.s5.install", "agent_install_unconfigured",
                               location="$.control_plane.public_url", role="control_plane")
        return None, _fail("install.s5.install", "install_trigger_failed", location="$.agents")
    if status == 501:
        return None, _fail("install.s5.install", "install_dependency",
                           location="$.control_plane.target", role="control_plane")
    if status == 404:
        return None, _fail("install.s5.install", "host_not_found", location="$.agents")
    return None, _fail("install.s5.install", "install_trigger_failed", location="$.agents")


def await_install(
    api: ApiClient,
    host_id: str,
    *,
    timeout: float,
    interval: float,
    sleep: Callable[[float], None],
    say: Callable[[str], None],
) -> Check | None:
    """Poll the install run until it reaches a terminal state."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            state = api.install_status(host_id)
        except ApiError as error:
            return _fail("install.s5.install", error.code,
                         location="$.control_plane.public_url", role="control_plane")
        console = state.get("console_status")
        saq = str(state.get("status") or "")
        if console in {"SUCCESS"}:
            return None
        if console in {"FAILED", "CANCELED"} or saq in {"failed", "aborted"}:
            return _fail("install.s5.install", "agent_install_failed", location="$.agents")
        if time.monotonic() >= deadline:
            return _fail("install.s5.install", "install_timeout", location="$.agents")
        say(f"waiting for {host_id} install ({console or saq or 'pending'})")
        sleep(interval)


# ── post-install assertions ──────────────────────────────────────────────


def heartbeat_fresh(host: dict[str, Any], *, now: float) -> bool:
    raw = host_field(host, "last_heartbeat")
    if not raw:
        return False
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now - stamp.timestamp()) <= AGENT_HEARTBEAT_FRESHNESS_SECONDS


def assert_agent(
    api: ApiClient,
    host_id: str,
    *,
    declared_name: str,
    expected_api_url: str,
    declared_digests: dict[str, str],
    now: float,
    digest_timeout: float = DIGEST_WAIT_SECONDS,
    poll_interval: float = INSTALL_POLL_INTERVAL_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    say: Callable[[str], None] | None = None,
) -> list[Check]:
    """Assert the deployed Agent points here, is alive and matches the release."""
    say = say or (lambda message: None)
    checks: list[Check] = []
    try:
        host = api.get_host(host_id)
    except ApiError as error:
        return [_fail("install.s5.heartbeat", error.code,
                      location="$.control_plane.public_url", role="control_plane")]

    online = host_field(host, "status") == "ONLINE" and heartbeat_fresh(host, now=now)
    if not online:
        checks.append(_fail("install.s5.heartbeat", "agent_offline", location="$.agents"))
        return checks
    checks.append(passed(
        "install.s5.heartbeat", "agent", "$.agents", "agent_online",
        f"Host {host_id} is ONLINE with a fresh heartbeat.",
        "Heartbeat proves the Agent reached this control plane, not that jobs can run.",
    ))

    instance_id = host_field(host, "agent_instance_id", "last_agent_instance_id")
    boot_id = host_field(host, "boot_id")
    if not instance_id or not boot_id:
        checks.append(_fail("install.s5.identity", "agent_identity", location="$.agents"))
        return checks
    if host_field(host, "name") != declared_name:
        checks.append(_fail("install.s5.identity", "host_conflict", location="$.agents"))
        return checks
    checks.append(passed(
        "install.s5.identity", "agent", "$.agents", "identity_recorded",
        "The Agent reported a stable instance identity and boot ID for the declared Host row.",
        "Identity changes mean a re-provisioned host; review the audit trail before reuse.",
    ))

    # 代码/schema/脚本一致：Agent 上报的部署摘要不得与声明发布物冲突。
    deadline = time.monotonic() + max(0.0, digest_timeout)
    while True:
        reported = {
            "agent-code": host_field(host, "agent_artifact_digest"),
            "host-resources": host_field(host, "agent_resources_digest"),
        }
        if any(reported.values()) or time.monotonic() >= deadline:
            break
        say(f"waiting for {host_id} deployment digest report")
        sleep(poll_interval)
        try:
            host = api.get_host(host_id)
        except ApiError as error:
            return checks + [_fail("install.s5.digest", error.code,
                                   location="$.control_plane.public_url", role="control_plane")]
    mismatched = [
        key for key, value in reported.items()
        if value and declared_digests.get(key) and value != declared_digests[key]
    ]
    if mismatched:
        checks.append(_fail("install.s5.digest", "agent_digest_mismatch",
                            location="$.release.manifest", role="site"))
        return checks
    verified = [key for key, value in reported.items() if value and declared_digests.get(key)]
    if not verified:
        # 摘要缺失（旧 Agent 或无 ARTIFACT_DIGEST 工件）不能当作「内容一致」。
        checks.append(_fail("install.s5.digest", "agent_digest_missing",
                            location="$.release.manifest", role="site"))
        return checks
    checks.append(passed(
        "install.s5.digest", "agent", "$.release.manifest", "digest_matched",
        f"Deployed Agent content matches the declared release digests ({', '.join(sorted(verified))}).",
        "A digest proves content integrity only; release origin still needs the pipeline attestation.",
    ))

    try:
        audit = api.install_audit(host_id)
    except ApiError as error:
        checks.append(_fail("install.s5.endpoint", error.code,
                            location="$.control_plane.public_url", role="control_plane"))
        return checks
    urls = {
        str(row.get("details", {}).get("agent_api_url", ""))
        for row in audit
        if isinstance(row.get("details"), dict)
    }
    if expected_api_url not in urls:
        checks.append(_fail("install.s5.endpoint", "agent_endpoint",
                            location="$.control_plane.public_url", role="control_plane"))
        return checks
    checks.append(passed(
        "install.s5.endpoint", "agent", "$.control_plane.public_url", "endpoint_recorded",
        "The audited install pushed this site's public entry into the Agent configuration.",
        "A reused Host must be re-installed when the site entry changes.",
    ))
    return checks


def assert_devices(api: ApiClient, host_id: str) -> Check:
    """Device discovery is reported, but a device-free site is not a failure."""
    try:
        devices = api.list_devices()
    except ApiError as error:
        return _fail("install.s5.devices", error.code,
                     location="$.control_plane.public_url", role="control_plane")
    owned = [item for item in devices if host_field(item, "host_id") == host_id]
    if not owned:
        return blocked(
            "install.s5.devices", "agent", "$.agents", "no_device_discovered",
            "No device is currently discovered on this Host.",
            "Attach an authorized test device and complete S6; do not report the device path as verified.",
        )
    serials = sorted(host_field(item, "serial") for item in owned if host_field(item, "serial"))
    return passed(
        "install.s5.devices", "agent", "$.agents", "devices_discovered",
        f"{len(owned)} device(s) are discovered on the Host ({', '.join(serials) or 'serial hidden'}).",
        "Discovery is not execution evidence; S6 still owns the controlled run.",
    )


# ── stage entry point ────────────────────────────────────────────────────


def stage_s5_agents(
    ctx: InstallContext,
    *,
    api: ApiClient | None = None,
    poll_timeout: float = INSTALL_POLL_TIMEOUT_SECONDS,
    poll_interval: float = INSTALL_POLL_INTERVAL_SECONDS,
    digest_timeout: float = DIGEST_WAIT_SECONDS,
    sleep: Callable[[float], None] = time.sleep,
    progress: Callable[[str], None] | None = None,
    now: float | None = None,
) -> list[Check]:
    """Onboard every declared Agent through the site API (S5)."""
    config = ctx.config
    checks: list[Check] = []
    # 进度走 stderr：stdout 只承载最终脱敏报告，便于操作者按 JSON 消费。
    say = progress or (lambda message: print(f"[s5] {message}", file=sys.stderr))
    bindings: list[dict[str, str]] = []

    for index, agent in enumerate(config.agents):
        location = f"$.agents[{index}]"
        values, problem = _agent_binding_problem(ctx.bindings_dir, agent.ssh_credential_ref)
        if problem is None and "PRIVATE_KEY_PATH" in values:
            problem = _key_permission_problem(values["PRIVATE_KEY_PATH"], config.control_plane.deploy_user)
        if problem is not None:
            checks.append(_fail("install.s5.binding", problem, location=location))
            return checks
        bindings.append(values)
    checks.append(passed(
        "install.s5.binding", "agent", "$.agents", "bindings_ready",
        "Every declared Agent has an SSH binding of the declared shape.",
        "Binding values stay in memory; they are never written to argv, logs or reports.",
    ))

    manifest = None
    if config.release.manifest is not None:
        try:
            manifest = load_release_manifest(config.release.manifest)
        except ConfigValidationError as error:
            checks.extend(error.checks)
            return checks
    if manifest is None:
        checks.append(_fail("install.s5.digest", "manifest_missing", location="$.release.manifest",
                            role="site"))
        return checks
    declared = {component.name: component.digest for component in manifest.components}

    if ctx.dry_run:
        checks.append(passed(
            "install.s5.plan", "agent", "$.agents", "installs_planned",
            "Host reconciliation and Agent installation are planned over this site's API only.",
            "Run without --dry-run on the confirmed target to create Hosts and install Agents.",
        ))
        return checks

    api = api or HttpApiClient(config.control_plane.public_url)
    admin = ctx.binding_values.get(config.security.initial_admin_ref, {})
    if not admin.get("USERNAME") or not admin.get("PASSWORD"):
        checks.append(_fail("install.s5.auth", "binding_content",
                            location="$.security.initial_admin_ref", role="control_plane"))
        return checks
    try:
        api.login(admin["USERNAME"], admin["PASSWORD"])
    except ApiError as error:
        checks.append(_fail("install.s5.auth", error.code,
                            location="$.security.initial_admin_ref", role="control_plane"))
        return checks
    checks.append(passed(
        "install.s5.auth", "control_plane", "$.security.initial_admin_ref", "admin_authenticated",
        "The initial administrator authenticated against this site's own API.",
        "Credentials are used once per run and stored nowhere.",
    ))

    for index, (agent, binding) in enumerate(zip(config.agents, bindings, strict=True)):
        location = f"$.agents[{index}]"
        say(f"onboarding agent {agent.key} ({agent.target})")
        result = _onboard_one(
            ctx, api, agent=agent, binding=binding, declared=declared,
            location=location, poll_timeout=poll_timeout, poll_interval=poll_interval,
            digest_timeout=digest_timeout, sleep=sleep, say=say, now=now,
        )
        checks.extend(result)
        if any(check.status == "FAIL" for check in result):
            # 失败即停：后续 Agent 不再创建/安装，避免半套站点被误当成功。
            return checks
    checks.append(passed(
        "install.s5", "agent", "$.agents", "agents_onboarded",
        "Every declared Agent reported a fresh heartbeat, a unique identity and the declared content.",
        "S6 (controlled run and storage probes) is still required before handover.",
    ))
    return checks


def _install_options(ctx: InstallContext, agent: Any) -> dict[str, str]:
    """把站点声明的路径下传给既有安装链（缺省不写、不覆盖目标既有值）。"""
    return {
        "agent_install_root": agent.install_root,
        "agent_local_aee_root": agent.local_aee_root,
        # 站点标准是控制面/Agent 同一分享同一字符串（设计 §3.3）；不写会让
        # Agent 因缺 STP_AEE_NFS_ROOT 启动即崩，直到下一次热更新才补齐。
        "agent_nfs_root": ctx.config.storage.mount_path,
    }


def _onboard_one(
    ctx: InstallContext,
    api: ApiClient,
    *,
    agent: Any,
    binding: dict[str, str],
    declared: dict[str, str],
    location: str,
    poll_timeout: float,
    poll_interval: float,
    digest_timeout: float,
    sleep: Callable[[float], None],
    say: Callable[[str], None],
    now: float | None,
) -> list[Check]:
    config = ctx.config
    name = f"{config.site.id}-{agent.key}"
    checks: list[Check] = []

    try:
        host_id, host_check = reconcile_host(
            api, name=name, ip=agent.target, binding=binding, location=location,
        )
    except ApiError as error:
        return [_fail("install.s5.host", error.code,
                      location="$.control_plane.public_url", role="control_plane")]
    if host_check is not None:
        return [host_check]
    assert host_id is not None
    checks.append(passed(
        "install.s5.host", "agent", location, "host_reconciled",
        f"Host {host_id} is registered for this site with the declared name.",
        "Host IDs are allocated by the site API; never re-create a Host to change its ID.",
    ))

    try:
        console_run_id, install_check = trigger_install(api, host_id, _install_options(ctx, agent))
    except ApiError as error:
        return checks + [_fail("install.s5.install", error.code,
                               location="$.control_plane.public_url", role="control_plane")]
    if install_check is not None:
        return checks + [install_check]
    assert console_run_id is not None

    timeout_check = await_install(
        api, host_id, timeout=poll_timeout, interval=poll_interval, sleep=sleep, say=say,
    )
    if timeout_check is not None:
        return checks + [timeout_check]
    checks.append(passed(
        "install.s5.install", "agent", location, "install_succeeded",
        f"Agent installation run {console_run_id} finished successfully.",
        "A successful run only means the installer exited zero; identity checks below are binding.",
    ))

    checks.extend(assert_agent(
        api, host_id,
        declared_name=name,
        expected_api_url=config.control_plane.public_url.rstrip("/"),
        declared_digests=declared,
        now=now if now is not None else time.time(),
        digest_timeout=digest_timeout,
        poll_interval=poll_interval,
        sleep=sleep,
        say=say,
    ))
    if any(check.status == "FAIL" for check in checks):
        return checks
    checks.append(assert_devices(api, host_id))
    return checks
