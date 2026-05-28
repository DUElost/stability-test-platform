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


def test_start_backend_scripts_prepare_env_before_launch():
    script = (REPO_ROOT / "start-backend.bat").read_text(encoding="utf-8")
    no_reload_script = (REPO_ROOT / "start-backend-no-reload.bat").read_text(encoding="utf-8")
    wsl_script = (REPO_ROOT / "start-backend-wsl.sh").read_text(encoding="utf-8")

    assert 'python tools\\prepare_env.py --template backend\\.env.example --target backend\\.env' in script
    assert 'python tools\\prepare_env.py --template backend\\.env.example --target backend\\.env' in no_reload_script
    assert 'python3 tools/prepare_env.py --template backend/.env.example --target backend/.env' in wsl_script
    assert 'python tools\\ensure_backend_dev_secrets.py --env-file backend\\.env' in script
    assert 'python tools\\ensure_backend_dev_secrets.py --env-file backend\\.env' in no_reload_script
    assert 'python3 tools/ensure_backend_dev_secrets.py --env-file backend/.env' in wsl_script


def test_start_backend_scripts_check_redis_before_migration():
    script = (REPO_ROOT / "start-backend.bat").read_text(encoding="utf-8")
    no_reload_script = (REPO_ROOT / "start-backend-no-reload.bat").read_text(encoding="utf-8")

    launcher = 'python tools\\run_with_backend_env.py --env-file backend\\.env --'
    preflight = launcher + ' python tools\\check_backend_redis.py --env-file backend\\.env'
    migration = 'python ..\\tools\\run_with_backend_env.py --env-file .env -- python -m alembic upgrade head'
    uvicorn = launcher + ' python -m uvicorn %UVICORN_ARGS%'

    assert preflight in script
    assert preflight in no_reload_script
    assert script.index(preflight) < script.index(migration)
    assert no_reload_script.index(preflight) < no_reload_script.index(migration)
    assert uvicorn in script
    assert uvicorn in no_reload_script


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
