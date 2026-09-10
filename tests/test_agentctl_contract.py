import contextlib
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTCTL_SCRIPT = REPO_ROOT / "backend/agent/agentctl.sh"
INSTALL_SCRIPT = REPO_ROOT / "backend/agent/install_agent.sh"


def test_install_script_deploys_tracked_agentctl_script():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert 'cat > "$INSTALL_DIR/agentctl"' not in text
    assert re.search(
        r'(install -m 755|cp)\s+"\$SCRIPT_DIR/agentctl\.sh"\s+"\$INSTALL_DIR/agentctl"',
        text,
    )


def test_install_script_defaults_to_fixed_host_id_mode():
    text = INSTALL_SCRIPT.read_text(encoding="utf-8")

    assert "HOST_ID=$HOST_ID" in text
    assert "AUTO_REGISTER_HOST=false" in text
    assert "HOST_ID=auto" not in text


def test_agentctl_health_returns_nonzero_for_critical_failures():
    text = AGENTCTL_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"health_check\(\) \{(?P<body>.*?)^\}", text, re.MULTILINE | re.DOTALL)

    assert match is not None, "health_check() not found"
    body = match.group("body")

    assert 'local exit_code=0' in body
    assert body.count("exit_code=1") >= 4
    assert '服务器连接: ${YELLOW}无法连接${NC}' in body
    assert re.search(
        r'if check_server_connection "\$API_URL"; then.*?else.*?exit_code=1',
        body,
        re.DOTALL,
    )
    assert 'return "$exit_code"' in body


class _ReadinessProbeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        status = self.server.health_status if self.path == "/health" else self.server.root_status
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format, *args):
        pass


@contextlib.contextmanager
def _readiness_server(*, health_status, root_status):
    server = HTTPServer(("127.0.0.1", 0), _ReadinessProbeHandler)
    server.health_status = health_status
    server.root_status = root_status
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _run_check_server_connection(api_url, tmp_path):
    """去掉入口 main 调用后 source，直接调用 check_server_connection。"""
    script_text = AGENTCTL_SCRIPT.read_text(encoding="utf-8")
    assert '\nmain "$@"\n' in script_text, "agentctl.sh 入口调用已变化，需同步本测试"
    functions_only = script_text.replace('\nmain "$@"\n', "\n")
    runner = tmp_path / "agentctl_functions.sh"
    runner.write_text(functions_only, encoding="utf-8")

    env = dict(os.environ)
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"

    return subprocess.run(
        ["bash", "-c", '. "$1"; check_server_connection "$2"', "--", str(runner), api_url],
        capture_output=True,
        text=True,
        timeout=30,
        env=env,
    ).returncode


def test_check_server_connection_fails_when_health_unavailable_even_if_homepage_ok(tmp_path):
    with _readiness_server(health_status=503, root_status=200) as api_url:
        assert _run_check_server_connection(api_url, tmp_path) != 0


def test_check_server_connection_succeeds_when_health_ready(tmp_path):
    with _readiness_server(health_status=200, root_status=200) as api_url:
        assert _run_check_server_connection(api_url, tmp_path) == 0


def test_check_server_connection_consumes_health_only():
    text = AGENTCTL_SCRIPT.read_text(encoding="utf-8")
    match = re.search(r"check_server_connection\(\) \{(?P<body>.*?)^\}", text, re.MULTILINE | re.DOTALL)

    assert match is not None, "check_server_connection() not found"
    body = match.group("body")
    assert "/health" in body
    assert body.count("curl ") == 1, "不得为首页等其它端点保留兜底请求"
