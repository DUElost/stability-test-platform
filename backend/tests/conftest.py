"""
Pytest Configuration and Fixtures
"""

import os
import sys
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.models.schemas import Base
from backend.core.database import Base as CoreBase


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
def sample_host(db_session):
    """Create a sample host"""
    from backend.models.schemas import Host, HostStatus

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
def sample_device(db_session, sample_host):
    """Create a sample device"""
    from backend.models.schemas import Device, DeviceStatus

    device = Device(
        serial="test-device-001",
        host_id=sample_host.id,
        status=DeviceStatus.ONLINE,
        last_seen=datetime.utcnow(),
    )
    db_session.add(device)
    db_session.commit()
    return device


@pytest.fixture
def sample_task(db_session):
    """Create a sample task"""
    from backend.models.schemas import Task, TaskStatus

    task = Task(
        name="test-task",
        type="MONKEY",
        status=TaskStatus.PENDING,
        priority=1,
    )
    db_session.add(task)
    db_session.commit()
    return task
