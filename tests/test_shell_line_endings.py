from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SHELL_SCRIPTS = [
    "start-backend-wsl.sh",
    "start-frontend-wsl.sh",
    "stop-backend-wsl.sh",
    "backend/agent/agentctl.sh",
    "backend/agent/install_agent.sh",
]


def test_linux_shell_scripts_use_lf_line_endings():
    offenders = []

    for relative_path in SHELL_SCRIPTS:
        contents = (REPO_ROOT / relative_path).read_bytes()
        if b"\r\n" in contents:
            offenders.append(relative_path)

    assert not offenders, (
        "Linux/WSL shell scripts must use LF line endings: "
        + ", ".join(offenders)
    )
