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
from sqlalchemy import create_engine
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
from backend.models.enums import DeviceStatus, HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance
from backend.models.plan import Plan, PlanStep
from backend.models.plan_migration_audit import PlanMigrationAudit
from backend.models.plan_run import PlanRun
from backend.core.security import create_access_token
from backend.main import fastapi_app as app


@pytest.fixture(scope="session", autouse=True)
def engine():
    """Create a test database engine"""
    engine = create_engine(TEST_DATABASE_URL, future=True, pool_pre_ping=True)
    # alembic 链路从 001_add_device_monitoring 起就假设 devices 表已存在,
    # 无法在空库上 `alembic upgrade head`。测试库统一用 ORM 视角建表;
    # 真正的迁移健康单独通过本地 dev DB / 预生产校验。
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
    """Create a fresh database session for each test"""
    connection = engine.connect()
    transaction = connection.begin()
    Session = sessionmaker(bind=connection)
    session = Session()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


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
        ip="172.21.15.100",
        ip_address="172.21.15.100",
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
        ip="172.21.15.101",
        ip_address="172.21.15.101",
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
        ip="172.21.15.102",
        ip_address="172.21.15.102",
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
