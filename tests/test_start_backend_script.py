from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_start_backend_script_requires_explicit_reload_opt_in():
    script = (REPO_ROOT / "start-backend.bat").read_text(encoding="utf-8")
    wsl_script = (REPO_ROOT / "start-backend-wsl.sh").read_text(encoding="utf-8")

    assert "STP_BACKEND_RELOAD" in script
    assert "STP_BACKEND_RELOAD" in wsl_script
    assert "--reload" in script
    assert "--reload" in wsl_script
    assert "python -m uvicorn backend.main:app --reload" not in script
    assert "exec uvicorn backend.main:app --reload" not in wsl_script


def test_start_backend_script_checks_port_before_launch():
    script = (REPO_ROOT / "start-backend.bat").read_text(encoding="utf-8")
    wsl_script = (REPO_ROOT / "start-backend-wsl.sh").read_text(encoding="utf-8")

    assert 'if not "%~1"=="" set "BACKEND_PORT=%~1"' in script
    assert "if not defined BACKEND_PORT set" in script
    assert 'BACKEND_PORT="${1:-${BACKEND_PORT:-8000}}"' in wsl_script
    assert '--port "$BACKEND_PORT"' in wsl_script
    assert "netstat -ano" in script
    assert "already in use by PID" in script
    assert "stop-backend.bat" in script


def test_stop_backend_script_stops_windows_port_before_wsl():
    script = (REPO_ROOT / "stop-backend.bat").read_text(encoding="utf-8")

    assert 'if not "%~1"=="" set "BACKEND_PORT=%~1"' in script
    assert "netstat -ano" in script
    assert "taskkill /PID" in script
    assert "/F /T" in script
    assert "wsl -e bash" in script


def test_frontend_launchers_target_configured_backend_port():
    windows_script = (REPO_ROOT / "start-frontend-windows.bat").read_text(
        encoding="utf-8"
    )
    wsl_script = (REPO_ROOT / "start-frontend-wsl.sh").read_text(encoding="utf-8")

    assert 'if not "%~1"=="" set "BACKEND_PORT=%~1"' in windows_script
    assert "VITE_API_BASE_URL=http://localhost:%BACKEND_PORT%" in windows_script
    assert "VITE_WS_BASE_URL=ws://localhost:%BACKEND_PORT%" in windows_script
    assert 'BACKEND_PORT="${1:-${BACKEND_PORT:-8000}}"' in wsl_script
    assert (
        'VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:${BACKEND_PORT}}"'
        in wsl_script
    )
    assert (
        'VITE_WS_BASE_URL="${VITE_WS_BASE_URL:-ws://localhost:${BACKEND_PORT}}"'
        in wsl_script
    )
