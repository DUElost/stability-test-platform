"""
Pytest Configuration and Fixtures
"""

import asyncio
import os
from datetime import datetime, timedelta, timezone

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
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

# Set test mode before importing app to disable startup background threads
os.environ["TESTING"] = "1"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-ci-32-bytes-ok"
os.environ["AGENT_SECRET"] = ""

_TEST_DB_CONTAINER: PostgresContainer | None = None


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
        return _normalize_test_database_url(configured)

    _TEST_DB_CONTAINER = PostgresContainer("postgres:16")
    _TEST_DB_CONTAINER.start()
    return _normalize_test_database_url(_TEST_DB_CONTAINER.get_connection_url())


TEST_DATABASE_URL = _resolve_test_database_url()

# Keep runtime modules aligned with the test database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

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


# ADR-0029 P1-B2：Plan 归属/专项双必填的测试兜底。
# CI 的 PG schema 由 `alembic upgrade head` 建（plan.project_id NOT NULL，
# migration e6f7g8h9i0j1），而大量测试内联 Plan(...) 构造不设归属——直接
# 违反约束（main CI backend-test 现场 NotNullViolation）。语义与设计一致：
# 不显式归属 = GENERIC（「通用（不限项目）」哨兵）+ ops（运维）专项。
# 全局 seed（每测试 TRUNCATE 后重建）——API 创建路径的 _resolve_project_id
# 在 INSERT 前查库，钩子（before_insert）覆盖不到，必须预先存在。
@pytest.fixture(autouse=True)
def _seed_plan_defaults(db_session):
    """每测试前重建 GENERIC 哨兵 + ops 专项（db_session 的 TRUNCATE 会清掉）。"""
    from backend.models.project import Specialty, TestProject

    if not db_session.query(TestProject).filter_by(project_key="GENERIC").first():
        db_session.add(TestProject(
            project_key="GENERIC", display_name="通用（不限项目）",
            source="USER",
        ))
    if not db_session.query(Specialty).filter_by(key="ops").first():
        db_session.add(Specialty(key="ops", display_name="运维", sort_order=10))
    db_session.commit()
    yield


@event.listens_for(Plan, "before_insert")
def _plan_default_attribution(mapper, connection, target):
    """测试构造 Plan 未显式设归属时落到 GENERIC/ops（P1-B2 双必填兜底）。

    覆盖 ORM 直接构造路径（db.add(Plan(...))）；API 路径由 autouse seed
    保证 GENERIC/ops 预先存在。双保险：GENERIC 被删时按需重建。
    """
    if target.project_id is not None and target.specialty_id is not None:
        return
    if target.project_id is None:
        generic_id = connection.execute(
            text("SELECT id FROM test_project WHERE project_key = 'GENERIC'")
        ).scalar()
        if generic_id is None:
            generic_id = connection.execute(
                text(
                    "INSERT INTO test_project "
                    "(project_key, display_name, source, status, created_at, updated_at) "
                    "VALUES ('GENERIC', '通用（不限项目）', 'USER', 'ACTIVE', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id"
                )
            ).scalar()
        target.project_id = generic_id
    if target.specialty_id is None:
        ops_id = connection.execute(
            text("SELECT id FROM specialty WHERE key = 'ops'")
        ).scalar()
        if ops_id is None:
            ops_id = connection.execute(
                text(
                    "INSERT INTO specialty (key, display_name, sort_order) "
                    "VALUES ('ops', '运维', 10) RETURNING id"
                )
            ).scalar()
        target.specialty_id = ops_id


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
    with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            conn.exec_driver_sql(
                f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"
            )
        else:
            for table in reversed(Base.metadata.sorted_tables):
                conn.execute(table.delete())

    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()


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
    token = create_access_token(data={"sub": "testuser", "role": "user"})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def admin_headers(admin_user):
    """Get authentication headers for admin user"""
    token = create_access_token(data={"sub": "admin", "role": "admin"})
    return {"Authorization": f"Bearer {token}"}
