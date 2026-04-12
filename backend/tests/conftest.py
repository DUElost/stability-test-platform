"""
Pytest Configuration and Fixtures
"""

import asyncio
import os
from datetime import datetime, timedelta

import pytest


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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(JSONB, "sqlite")
def compile_jsonb_for_sqlite(type_, compiler, **kwargs):
    return "JSON"

# Set test mode before importing app to disable startup background threads
os.environ["TESTING"] = "1"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-ci"

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ALLOW_SQLITE_TESTS = os.getenv("ALLOW_SQLITE_TESTS", "0") == "1"

if not TEST_DATABASE_URL:
    if ALLOW_SQLITE_TESTS:
        TEST_DATABASE_URL = "sqlite:///:memory:"
    else:
        raise RuntimeError(
            "TEST_DATABASE_URL is required for tests (PostgreSQL). "
            "For local quick SQLite tests only, set ALLOW_SQLITE_TESTS=1."
        )

# Keep runtime modules aligned with the test database.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

from backend.core.database import async_engine, get_db
from backend.core.database import Base
from backend.models.enums import DeviceStatus, HostStatus, JobStatus
from backend.models.host import Device, Host
from backend.models.job import JobInstance, TaskTemplate as JobTaskTemplate
from backend.models.workflow import WorkflowDefinition, WorkflowRun
from backend.core.security import create_access_token
from backend.main import fastapi_app as app


@pytest.fixture(scope="session")
def engine():
    """Create a test database engine"""
    create_kwargs = {"future": True}
    if TEST_DATABASE_URL.startswith("sqlite"):
        create_kwargs["connect_args"] = {"check_same_thread": False}
        create_kwargs["poolclass"] = StaticPool
    else:
        create_kwargs["pool_pre_ping"] = True

    engine = create_engine(TEST_DATABASE_URL, **create_kwargs)
    if TEST_DATABASE_URL.startswith("sqlite"):
        Base.metadata.create_all(bind=engine)
    yield engine
    if TEST_DATABASE_URL.startswith("sqlite"):
        Base.metadata.drop_all(bind=engine)
    engine.dispose()


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


@pytest.fixture
def client(db_session):
    """Create FastAPI test client with test database"""
    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    # Disable rate limiting middleware for tests by clearing middleware stack
    original_middleware = app.user_middleware.copy()
    app.user_middleware = [m for m in app.user_middleware if "RateLimit" not in str(m.cls)]

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
        last_heartbeat=datetime.utcnow(),
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
        last_heartbeat=datetime.utcnow() - timedelta(minutes=10),
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
        last_heartbeat=datetime.utcnow() - timedelta(seconds=400),
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
        last_seen=datetime.utcnow(),
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
        last_seen=datetime.utcnow() - timedelta(minutes=10),
        adb_connected=False,
        adb_state="offline",
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def sample_busy_device(db_session, sample_host):
    """Create a sample busy device"""
    device = Device(
        serial="test-device-003",
        host_id=sample_host.id,
        status=DeviceStatus.BUSY.value,
        last_seen=datetime.utcnow(),
        adb_connected=True,
        adb_state="device",
        lock_run_id=1,
        lock_expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db_session.add(device)
    db_session.commit()
    return device


# ── Model fixtures (WorkflowDefinition / WorkflowRun / JobInstance) ────────


@pytest.fixture
def sample_workflow_definition(db_session):
    """Create a sample WorkflowDefinition."""
    wd = WorkflowDefinition(
        name="test-workflow",
        description="Test workflow for unit tests",
        failure_threshold=0.1,
        created_by="test",
    )
    db_session.add(wd)
    db_session.commit()
    return wd


@pytest.fixture
def sample_task_template(db_session, sample_workflow_definition):
    """Create a sample TaskTemplate within a WorkflowDefinition."""
    tt = JobTaskTemplate(
        workflow_definition_id=sample_workflow_definition.id,
        name="test-template",
        pipeline_def={"version": 1, "stages": [{"name": "test", "steps": []}]},
        sort_order=0,
    )
    db_session.add(tt)
    db_session.commit()
    return tt


@pytest.fixture
def sample_workflow_run(db_session, sample_workflow_definition):
    """Create a sample WorkflowRun."""
    run = WorkflowRun(
        workflow_definition_id=sample_workflow_definition.id,
        status="RUNNING",
        failure_threshold=sample_workflow_definition.failure_threshold,
        triggered_by="test",
    )
    db_session.add(run)
    db_session.commit()
    return run


@pytest.fixture
def sample_job_instance(db_session, sample_workflow_run, sample_task_template, sample_device, sample_host):
    """Create a sample JobInstance."""
    job = JobInstance(
        workflow_run_id=sample_workflow_run.id,
        task_template_id=sample_task_template.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.PENDING.value,
        pipeline_def=sample_task_template.pipeline_def,
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.fixture
def sample_running_job(db_session, sample_workflow_run, sample_task_template, sample_device, sample_host):
    """Create a sample running JobInstance."""
    job = JobInstance(
        workflow_run_id=sample_workflow_run.id,
        task_template_id=sample_task_template.id,
        device_id=sample_device.id,
        host_id=sample_host.id,
        status=JobStatus.RUNNING.value,
        pipeline_def=sample_task_template.pipeline_def,
        started_at=datetime.utcnow(),
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
