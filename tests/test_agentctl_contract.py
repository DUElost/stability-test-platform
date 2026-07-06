from pathlib import Path
import re


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
