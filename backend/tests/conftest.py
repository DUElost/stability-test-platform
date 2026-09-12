"""
Pytest Configuration and Fixtures
"""

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from testcontainers.postgres import PostgresContainer


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "phase0: Phase 0 state-closure regression — run with -m phase0",
    )
    config.addinivalue_line(
        "markers",
        "integration: tests that require a live database (TEST_DATABASE_URL)",
    )
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

# Set test mode before importing app to disable startup background threads
os.environ["TESTING"] = "1"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-ci-32-bytes-ok"
os.environ["AGENT_SECRET"] = ""

_TEST_DB_CONTAINER: PostgresContainer | None = None


_CONTAINER_CLEANUP = None


def _register_container_cleanup(container) -> None:
    """#1492：按文件路径加载守卫模块（与 #1300 护栏同款，避免包级导入副作用）。"""
    global _CONTAINER_CLEANUP
    import importlib.util

    path = Path(__file__).resolve().parent / "container_lifecycle.py"
    spec = importlib.util.spec_from_file_location("container_lifecycle", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    _CONTAINER_CLEANUP = mod.ContainerCleanup(container)
    _CONTAINER_CLEANUP.register()


def pytest_sessionfinish(session, exitstatus) -> None:  # noqa: ARG001
    """#1492：正常结束也主动停容器（不依赖 ryuk）。"""
    if _CONTAINER_CLEANUP is not None:
        _CONTAINER_CLEANUP.stop()


def _normalize_test_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+psycopg2://"):
        return database_url.replace("postgresql+psycopg2://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return database_url


def _resolve_test_database_url() -> str:
    global _TEST_DB_CONTAINER

    configured = os.getenv("TEST_DATABASE_URL")
    if configured:
        normalized = _normalize_test_database_url(configured)
        # #1300（R15-R01）：显式地址的机器护栏——隔离命名 + 运行时配置比对；
        # 误指生产库时在这里拒绝，而不是让 db_session 的全表 TRUNCATE 动手。
        # 按文件路径加载（不进 backend.core 包）：此时代码尚未设置
        # DATABASE_URL，包级导入会触发 env_source 的配置解析直接失败。
        import importlib.util

        guard_path = (
            Path(__file__).resolve().parent.parent / "core" / "db_url_guard.py"
        )
        spec = importlib.util.spec_from_file_location("db_url_guard", guard_path)
        guard_mod = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(guard_mod)
        return guard_mod.guard_test_database_url(
            normalized, runtime_database_url=os.getenv("DATABASE_URL"),
        )

    _TEST_DB_CONTAINER = PostgresContainer("postgres:16")
    _TEST_DB_CONTAINER.start()
    # #1492：进程内清理兜底（sessionfinish / SIGTERM / SIGINT / atexit）——
    # 本机 ryuk 回收不可靠（#1482 实测残留 36 个），在可控退出路径上主动停。
    _register_container_cleanup(_TEST_DB_CONTAINER)
    return _normalize_test_database_url(_TEST_DB_CONTAINER.get_connection_url())


TEST_DATABASE_URL = _resolve_test_database_url()

# Keep runtime modules aligned with the test database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
# #941：解析结果（含 testcontainers 兜底路径）同步写回 TEST_DATABASE_URL——
# 租约 / abort-reaper 等 PG-only 测试以 os.getenv("TEST_DATABASE_URL") 判方言，
# 只写 DATABASE_URL 会让它们在实际 PG（容器兜底）上整组 skip。
os.environ["TEST_DATABASE_URL"] = TEST_DATABASE_URL

from backend.core.database import async_engine, engine as app_engine, get_db
from backend.core.database import Base
from backend.models import action_template as _action_template  # noqa: F401
from backend.models import audit as _audit  # noqa: F401
from backend.models import device_lease as _device_lease  # noqa: F401
from backend.models import notification as _notification  # noqa: F401
from backend.models import resource_pool as _resource_pool  # noqa: F401
from backend.models import schedule as _schedule  # noqa: F401
from backend.models import script as _script  # noqa: F401
from backend.models import token_blacklist as _token_blacklist  # noqa: F401
from backend.models import user as _user  # noqa: F401
from backend.models import plan_run_artifact as _plan_run_artifact  # noqa: F401
from backend.models.enums import DeviceStatus, HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.script import Script
from backend.models.plan_run import PlanRun
from backend.core.security import create_access_token
from backend.main import fastapi_app as app


# logging_setup (#563) 在 import backend.main 时把 backend.* 的 propagate
# 关闭（生产防重复输出）——pytest caplog 基于 root handler 捕获，测试环境
# 必须在 import 之后恢复 propagate 才能断言 WARNING 记录。
import logging as _logging

_logging.getLogger("backend").propagate = True


@pytest.fixture(scope="session", autouse=True)
def engine():
    """Create a test database engine"""
    engine = create_engine(TEST_DATABASE_URL, future=True, pool_pre_ping=True)
    # CI first runs `alembic upgrade head` against this empty PostgreSQL DB.
    # create_all remains as a cheap local/testcontainers bootstrap, not as a
    # substitute for migration-chain validation.
    Base.metadata.create_all(bind=engine)
    yield engine
    try:
        asyncio.run(async_engine.dispose())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(async_engine.dispose())
        finally:
            loop.close()
    engine.dispose()
    app_engine.dispose()
    if _TEST_DB_CONTAINER is not None:
        _TEST_DB_CONTAINER.stop()


_TRUNCATE_DEADLOCK_RETRIES = 3


def _truncate_all_tables(engine, table_names: str) -> None:
    """清库（TRUNCATE ... RESTART IDENTITY CASCADE）并对死锁做有界重试（#1273）。

    TRUNCATE 取 AccessExclusiveLock；同进程内仍有存活的连接/后台线程持
    AccessShareLock 时，PG 会把 TRUNCATE 判为循环等待的牺牲者并抛
    DeadlockDetected（全量套件 7–8 个 setup ERROR 的来源，出错集合随运行漂移）。
    死锁是瞬态：对方语句结束后重试即成功；超出上限仍失败则原样抛出。
    """
    for attempt in range(_TRUNCATE_DEADLOCK_RETRIES + 1):
        try:
            with engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    conn.exec_driver_sql(
                        f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"
                    )
                else:
                    for table in reversed(Base.metadata.sorted_tables):
                        conn.execute(table.delete())
            return
        except OperationalError as exc:
            if "DeadlockDetected" not in str(exc) or attempt >= _TRUNCATE_DEADLOCK_RETRIES:
                raise
            time.sleep(0.2 * (attempt + 1))


@pytest.fixture(scope="function")
def db_session(engine):
    """Per-test session with full isolation via TRUNCATE ... RESTART IDENTITY.

    Why not nested transactions: many routes/fixtures call ``session.commit()``,
    which immediately escapes a SAVEPOINT and persists to PG. The old rollback
    pattern silently leaked data across cases (see uq_script_name_version
    collisions and 484-host accumulation). TRUNCATE + RESTART IDENTITY is the
    only sound option once commits cannot be funnelled through SAVEPOINTs.
    """
    # Reverse-dependency order so CASCADE just confirms what we ordered.
    table_names = ", ".join(
        f'"{t.name}"' for t in reversed(Base.metadata.sorted_tables)
    )
    _truncate_all_tables(engine, table_names)

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _seed_ops_specialty(db_session):
    """specialty 仍必填（v2.5 D11 只放开 project）——每测试前保证 ops 存在。"""
    from backend.models.project import Specialty

    if not db_session.query(Specialty).filter_by(key="ops").first():
        db_session.add(Specialty(key="ops", display_name="运维", sort_order=10))
        db_session.commit()
    yield


@pytest.fixture(autouse=True)
def dispose_async_engine_between_tests():
    """Dispose asyncpg pool before pytest tears down the current test loop."""
    yield
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        asyncio.run(async_engine.dispose())
        return

    if loop.is_closed():
        temp_loop = asyncio.new_event_loop()
        try:
            temp_loop.run_until_complete(async_engine.dispose())
        finally:
            temp_loop.close()
        return

    loop.run_until_complete(async_engine.dispose())


@pytest.fixture(autouse=True)
def _default_admission_queue(monkeypatch):
    """Admission queue is the sole dispatch path — enable flag + pump in tests."""
    import backend.core.admission_queue as admission_queue

    monkeypatch.setenv("STP_PLAN_ADMISSION_QUEUE_ENABLED", "1")
    admission_queue.mark_queue_pump_ready(True)
    yield
    admission_queue.mark_queue_pump_ready(False)


@pytest.fixture(autouse=True)
def _reset_login_lockout_state():
    """#281:模块级登录锁定的默认实例跨测试共享,失败计数会互相污染
    (未注册用户名共享一个桶后尤甚);每个测试前清空。"""
    from backend.core import login_lockout

    login_lockout._default._state.clear()
    yield


@pytest.fixture
def client(db_session):
    """Create FastAPI test client with test database"""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # Disable rate limiting + CSRF Origin middleware for tests by clearing middleware stack.
    # CSRF is exhaustively covered in isolation by test_csrf_origin_middleware.py — integration
    # tests focus on cookie/session/RBAC semantics and TestClient does not set Origin by default.
    original_middleware = app.user_middleware.copy()
    app.user_middleware = [
        m for m in app.user_middleware
        if "RateLimit" not in str(m.cls) and "CSRFOrigin" not in str(m.cls)
    ]

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
    app.user_middleware = original_middleware
    # Windows + asyncpg 下，不同 TestClient 事件循环之间复用连接会触发 loop closed。
    # 每个用例后释放异步连接池，避免跨用例复用旧 loop 的连接对象。
    try:
        asyncio.run(async_engine.dispose())
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(async_engine.dispose())
        finally:
            loop.close()


@pytest.fixture
def sample_host(db_session):
    """Create a sample host"""
    host = Host(
        id="101",
        hostname="test-host-101",
        name="test-host",
        ip="192.0.2.100",
        ip_address="192.0.2.100",
        status=HostStatus.ONLINE.value,
        last_heartbeat=datetime.now(timezone.utc),
    )
    db_session.add(host)
    db_session.commit()
    return host


@pytest.fixture
def sample_offline_host(db_session):
    """Create a sample offline host"""
    host = Host(
        id="102",
        hostname="test-host-102",
        name="test-host-offline",
        ip="192.0.2.101",
        ip_address="192.0.2.101",
        status=HostStatus.OFFLINE.value,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(minutes=10),
    )
    db_session.add(host)
    db_session.commit()
    return host


@pytest.fixture
def sample_host_expired(db_session):
    """Create a sample host with expired heartbeat"""
    host = Host(
        id="103",
        hostname="test-host-103",
        name="test-host-expired",
        ip="192.0.2.102",
        ip_address="192.0.2.102",
        status=HostStatus.ONLINE.value,
        last_heartbeat=datetime.now(timezone.utc) - timedelta(seconds=400),
    )
    db_session.add(host)
    db_session.commit()
    return host


@pytest.fixture
def sample_device(db_session, sample_host):
    """Create a sample device"""
    device = Device(
        serial="test-device-001",
        host_id=sample_host.id,
        status=DeviceStatus.ONLINE.value,
        last_seen=datetime.now(timezone.utc),
        adb_connected=True,
        adb_state="device",
        battery_level=80,
        temperature=35,
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def sample_offline_device(db_session, sample_host):
    """Create a sample offline device"""
    device = Device(
        serial="test-device-002",
        host_id=sample_host.id,
        status=DeviceStatus.OFFLINE.value,
        last_seen=datetime.now(timezone.utc) - timedelta(minutes=10),
        adb_connected=False,
        adb_state="offline",
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def sample_busy_device(db_session, sample_host):
    """Create a sample busy device (static fixture — does not create DeviceLease)."""
    device = Device(
        serial="test-device-003",
        host_id=sample_host.id,
        status=DeviceStatus.BUSY.value,
        last_seen=datetime.now(timezone.utc),
        adb_connected=True,
        adb_state="device",
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def gate_chain(db_session):
    """Plan + Script + 2 Hosts/Devices for dispatch-gate tests."""
    host_a = Host(id="h-A", hostname="agentA", status=HostStatus.ONLINE.value, ip="10.0.0.1")
    host_b = Host(id="h-B", hostname="agentB", status=HostStatus.ONLINE.value, ip="10.0.0.2")
    dev_a = Device(serial="dev-A", host_id="h-A", status="ONLINE")
    dev_b = Device(serial="dev-B", host_id="h-B", status="ONLINE")
    script = Script(
        name="check_device", script_type="python", version="1.0.0",
        nfs_path="/scripts/check_device/v1.0.0/check_device.py",
        content_sha256="aabbcc11", default_params={"timeout": 30},
    )
    plan = Plan(name="precheck-plan")
    db_session.add_all([host_a, host_b, dev_a, dev_b, script, plan])
    db_session.commit()
    db_session.add(PlanStep(
        plan_id=plan.id, step_key="init_check",
        script_name="check_device", script_version="1.0.0",
        stage="init", sort_order=0, timeout_seconds=30, retry=0,
    ))
    db_session.commit()
    return {
        "plan": plan,
        "host_a": host_a, "host_b": host_b,
        "device_a": dev_a, "device_b": dev_b,
        "script": script,
    }


@pytest.fixture
def single_device_gate_chain(db_session):
    """Plan + Script + 1 Host/Device for single-device dispatch-gate tests."""
    host = Host(
        id="h-1", hostname="agent1", status=HostStatus.ONLINE.value, ip="10.0.0.9",
    )
    device = Device(serial="dev-1", host_id="h-1", status="ONLINE")
    script = Script(
        name="check_device",
        script_type="python",
        version="1.0.0",
        nfs_path="/scripts/check_device/v1.0.0/check_device.py",
        content_sha256="aabbcc11",
        default_params={"timeout": 30},
    )
    plan = Plan(name="single-device-plan")
    db_session.add_all([host, device, script, plan])
    db_session.commit()
    db_session.add(
        PlanStep(
            plan_id=plan.id,
            step_key="init_check",
            script_name="check_device",
            script_version="1.0.0",
            stage="init",
            sort_order=0,
            timeout_seconds=30,
            retry=0,
        )
    )
    db_session.commit()
    return {"plan": plan, "host": host, "device": device, "script": script}


@pytest.fixture(autouse=True)
def _precheck_notify_test_mode(monkeypatch):
    """Disable notify debounce in tests unless explicitly overridden."""
    monkeypatch.setattr(
        "backend.services.precheck.notify.PRECHECK_NOTIFY_DEBOUNCE_SECONDS", 0,
    )
    from backend.services.precheck.notify import reset_notify_debounce_state

    reset_notify_debounce_state()
    yield
    reset_notify_debounce_state()


# ── Script fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def sample_script(db_session):
    """Create Script rows referenced by PlanSteps in tests."""
    from backend.models.script import Script

    scripts = [
        ("check_device", "1.0.0"),
        ("check_device", "v1.0.0"),
    ]
    for name, version in scripts:
        existing = db_session.query(Script).filter(
            Script.name == name, Script.version == version
        ).first()
        if existing:
            continue
        db_session.add(Script(
            name=name,
            script_type="python",
            version=version,
            nfs_path=f"/nfs/scripts/{name}/{version}",
            content_sha256="0" * 64,
            is_active=True,
            default_params={},
            param_schema={},
        ))
    db_session.commit()
    return db_session.query(Script).all()


# ── Model fixtures (Plan / PlanRun / JobInstance) ──────────────────────────


@pytest.fixture
def sample_plan(db_session):
    """Create a sample Plan with steps."""
    plan = Plan(
        name="test-plan",
        description="Test plan for unit tests",
        failure_threshold=0.1,
                created_by="test",
    )
    db_session.add(plan)
    db_session.flush()

    step = PlanStep(
        plan_id=plan.id,
        step_key="check_device",
        script_name="check_device",
        script_version="v1.0.0",
        stage="init",
        sort_order=0,
    )
    db_session.add(step)
    db_session.commit()
    return plan


@pytest.fixture
def sample_plan_run(db_session, sample_plan):
    """Create a sample PlanRun."""
    run = PlanRun(
        plan_id=sample_plan.id,
        status="RUNNING",
        failure_threshold=sample_plan.failure_threshold,
        plan_snapshot={"name": sample_plan.name, "plan_id": sample_plan.id},
        run_type="MANUAL",
        triggered_by="test",
    )
    db_session.add(run)
    db_session.commit()
    return run


@pytest.fixture
def sample_job_instance(db_session, sample_plan_run, sample_plan, sample_device, sample_host):
    """Create a sample JobInstance."""
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.PENDING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture
def sample_running_job(db_session, sample_plan_run, sample_plan, sample_device, sample_host):
    """Create a sample running JobInstance."""
    job = JobInstance(
        plan_run_id=sample_plan_run.id,
        plan_id=sample_plan.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.RUNNING.value,
        pipeline_def={"lifecycle": {"init": [], "teardown": []}},
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(job)
    db_session.commit()
    return job


# ── User fixtures ─────────────────────────────────────────────────────────


@pytest.fixture
def test_user(db_session):
    """Create a test user"""
    from backend.core.security import get_password_hash
    from backend.models.user import User
    user = db_session.query(User).filter(User.username == "testuser").first()
    if not user:
        user = User(
            username="testuser",
            hashed_password=get_password_hash("testpass123"),
            role="user",
            is_active="Y",
        )
        db_session.add(user)
    else:
        user.hashed_password = get_password_hash("testpass123")
        user.role = "user"
        user.is_active = "Y"
    db_session.commit()
    return user


@pytest.fixture
def admin_user(db_session):
    """Create an admin user"""
    from backend.core.security import get_password_hash
    from backend.models.user import User
    user = db_session.query(User).filter(User.username == "admin").first()
    if not user:
        user = User(
            username="admin",
            hashed_password=get_password_hash("adminpass123"),
            role="admin",
            is_active="Y",
        )
        db_session.add(user)
    else:
        user.hashed_password = get_password_hash("adminpass123")
        user.role = "admin"
        user.is_active = "Y"
    db_session.commit()
    return user


@pytest.fixture
def auth_headers(test_user):
    """Get authentication headers for test user"""
    # R02-D1/D2（#900/#902）：token sub=用户 PK + ver=会话纪元
    token = create_access_token(
        data={
            "sub": str(test_user.id),
            "username": test_user.username,
            "role": test_user.role,
            "ver": test_user.token_version,
        }
    )
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(admin_user):
    """Get authentication headers for admin user"""
    token = create_access_token(
        data={
            "sub": str(admin_user.id),
            "username": admin_user.username,
            "role": admin_user.role,
            "ver": admin_user.token_version,
        }
    )
    return {"Authorization": f"Bearer {token}"}
