"""
Pytest Configuration and Fixtures
"""

import os
import sys
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Set test mode before importing app to disable startup background threads
os.environ["TESTING"] = "1"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-for-ci"

from backend.models.schemas import Base, Host, HostStatus, Device, DeviceStatus, Task, TaskStatus, TaskRun, RunStatus
from backend.core.database import Base as CoreBase, get_db
from backend.core.security import create_access_token
from backend.main import app


# Use in-memory SQLite for testing
TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture(scope="session")
def engine():
    """Create a test database engine"""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine


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


@pytest.fixture
def sample_host(db_session):
    """Create a sample host"""
    host = Host(
        name="test-host",
        ip="172.21.15.100",
        status=HostStatus.ONLINE,
        last_heartbeat=datetime.utcnow(),
    )
    db_session.add(host)
    db_session.commit()
    return host


@pytest.fixture
def sample_offline_host(db_session):
    """Create a sample offline host"""
    host = Host(
        name="test-host-offline",
        ip="172.21.15.101",
        status=HostStatus.OFFLINE,
        last_heartbeat=datetime.utcnow() - timedelta(minutes=10),
    )
    db_session.add(host)
    db_session.commit()
    return host


@pytest.fixture
def sample_host_expired(db_session):
    """Create a sample host with expired heartbeat"""
    host = Host(
        name="test-host-expired",
        ip="172.21.15.102",
        status=HostStatus.ONLINE,
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
        status=DeviceStatus.ONLINE,
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
        status=DeviceStatus.OFFLINE,
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
        status=DeviceStatus.BUSY,
        last_seen=datetime.utcnow(),
        adb_connected=True,
        adb_state="device",
        lock_run_id=1,
        lock_expires_at=datetime.utcnow() + timedelta(minutes=10),
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def sample_task(db_session, sample_device):
    """Create a sample task"""
    task = Task(
        name="test-task",
        type="MONKEY",
        params={"count": 1000},
        target_device_id=sample_device.id,
        status=TaskStatus.PENDING,
        priority=1,
    )
    db_session.add(task)
    db_session.commit()
    return task


@pytest.fixture
def sample_queued_task(db_session, sample_device):
    """Create a sample queued task"""
    task = Task(
        name="test-queued-task",
        type="MTBF",
        params={"duration": 3600},
        target_device_id=sample_device.id,
        status=TaskStatus.QUEUED,
        priority=2,
    )
    db_session.add(task)
    db_session.commit()
    return task


@pytest.fixture
def sample_running_task(db_session, sample_device):
    """Create a sample running task"""
    task = Task(
        name="test-running-task",
        type="DDR",
        params={"duration": 1800},
        target_device_id=sample_device.id,
        status=TaskStatus.RUNNING,
        priority=3,
    )
    db_session.add(task)
    db_session.commit()
    return task


@pytest.fixture
def sample_task_run(db_session, sample_task, sample_host, sample_device):
    """Create a sample task run"""
    run = TaskRun(
        task_id=sample_task.id,
        host_id=sample_host.id,
        device_id=sample_device.id,
        status=RunStatus.QUEUED,
    )
    db_session.add(run)
    db_session.commit()
    return run


@pytest.fixture
def sample_dispatched_run(db_session, sample_queued_task, sample_host, sample_device):
    """Create a sample dispatched task run"""
    run = TaskRun(
        task_id=sample_queued_task.id,
        host_id=sample_host.id,
        device_id=sample_device.id,
        status=RunStatus.DISPATCHED,
    )
    db_session.add(run)
    db_session.commit()
    return run


@pytest.fixture
def sample_running_run(db_session, sample_running_task, sample_host, sample_device):
    """Create a sample running task run"""
    run = TaskRun(
        task_id=sample_running_task.id,
        host_id=sample_host.id,
        device_id=sample_device.id,
        status=RunStatus.RUNNING,
        started_at=datetime.utcnow(),
        last_heartbeat_at=datetime.utcnow(),
    )
    db_session.add(run)
    db_session.commit()
    return run


@pytest.fixture
def test_user(db_session):
    """Create a test user"""
    from backend.core.security import get_password_hash
    # Import User here to avoid circular imports
    from backend.models.schemas import User
    user = User(
        username="testuser",
        hashed_password=get_password_hash("testpass123"),
        role="user",
        is_active="Y",
    )
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def admin_user(db_session):
    """Create an admin user"""
    from backend.core.security import get_password_hash
    # Import User here to avoid circular imports
    from backend.models.schemas import User
    user = User(
        username="admin",
        hashed_password=get_password_hash("adminpass123"),
        role="admin",
        is_active="Y",
    )
    db_session.add(user)
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
