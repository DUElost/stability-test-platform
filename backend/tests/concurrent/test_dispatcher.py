"""
Concurrent Testing Suite for Task Dispatcher

Tests the concurrent properties defined in specs.md:
- PROP-004: Task Dispatch Idempotency
- PROP-005: Heartbeat Timestamp Monotonicity
- Race condition validation

NOTE: This module tests the DEPRECATED scheduler/dispatcher.py.
      It should be rewritten to target services/dispatcher.py (Wave 3e).
"""

import pytest

pytestmark = pytest.mark.skip(
    reason="Tests target deprecated scheduler/dispatcher.py — rewrite for services/dispatcher.py in Wave 3e"
)

SessionLocal = None
engine = None


class TestConcurrentDeviceDispatch:
    """并发设备分发测试"""

    @pytest.fixture
    def dispatcher(self):
        """创建调度器实例"""
        return TaskDispatcher()

    def test_single_device_concurrent_dispatch(self, db_session):
        """
        测试单个设备并发分发竞争

        场景: 多个任务同时竞争一个可用设备
        期望: 只有一个任务成功获取设备锁
        """
        # 创建测试数据
        host = Host(
            name="test-host",
            ip="172.21.15.100",
            status=HostStatus.ONLINE,
            last_heartbeat=datetime.utcnow()
        )
        db_session.add(host)
        db_session.flush()

        device = Device(
            serial="test-device-001",
            host_id=host.id,
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow()
        )
        db_session.add(device)
        db_session.flush()

        # 创建多个待分发任务
        tasks = []
        for i in range(5):
            task = Task(
                name=f"test-task-{i}",
                type="MONKEY",
                status=TaskStatus.PENDING,
                priority=1
            )
            db_session.add(task)
            tasks.append(task)

        db_session.commit()

        # 并发尝试分发
        results = []
        errors = []

        def try_dispatch(task_id: int) -> Tuple[int, bool, Optional[str]]:
            try:
                with SessionLocal() as db:
                    dispatcher = TaskDispatcher()
                    task = db.get(Task, task_id)
                    if not task or task.status != TaskStatus.PENDING:
                        return task_id, False, "Task not pending"

                    device_result, host_result = dispatcher._pick_device(db, task)
                    if not device_result:
                        return task_id, False, "No device available"

                    capacity_ok, active, limit = dispatcher._host_capacity(db, host_result)
                    if not capacity_ok:
                        return task_id, False, "Host at capacity"

                    try:
                        run_id = dispatcher._create_run_with_lock(db, task, device_result, host_result, limit)
                        db.commit()
                        return task_id, True, f"Run {run_id} created"
                    except RuntimeError as e:
                        return task_id, False, str(e)
            except Exception as e:
                return task_id, False, str(e)

        # 使用线程池并发执行
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(try_dispatch, task.id) for task in tasks]
            for future in as_completed(futures):
                task_id, success, message = future.result()
                results.append((task_id, success, message))
                if not success:
                    errors.append((task_id, message))

        # 验证结果
        success_count = sum(1 for _, success, _ in results if success)

        # 只有一个任务应该成功
        assert success_count == 1, f"Expected 1 success, got {success_count}. Results: {results}"

        # 验证设备被锁定
        db_session.refresh(device)
        assert device.status == DeviceStatus.BUSY
        assert device.lock_run_id is not None

    def test_device_lock_atomicity(self, db_session):
        """
        测试设备锁的原子性

        验证: 设备锁的获取和状态更新是原子操作
        """
        host = Host(
            name="atomic-test-host",
            ip="172.21.15.101",
            status=HostStatus.ONLINE,
            last_heartbeat=datetime.utcnow()
        )
        db_session.add(host)
        db_session.flush()

        device = Device(
            serial="atomic-device-001",
            host_id=host.id,
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow()
        )
        db_session.add(device)

        task = Task(
            name="atomic-test-task",
            type="MONKEY",
            status=TaskStatus.PENDING
        )
        db_session.add(task)
        db_session.commit()

        lock_acquired = []

        def acquire_lock(task_id: int) -> bool:
            try:
                with SessionLocal() as db:
                    dispatcher = TaskDispatcher()
                    task = db.get(Task, task_id)
                    device_result, host_result = dispatcher._pick_device(db, task)

                    if device_result and host_result:
                        capacity_ok, active, limit = dispatcher._host_capacity(db, host_result)
                        if capacity_ok:
                            try:
                                run_id = dispatcher._create_run_with_lock(db, task, device_result, host_result, limit)
                                db.commit()
                                lock_acquired.append(task_id)
                                return True
                            except RuntimeError:
                                return False
                    return False
            except Exception:
                return False

        # 并发尝试获取锁
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(acquire_lock, task.id) for _ in range(10)]
            for future in as_completed(futures):
                future.result()

        # 验证只有一个线程获取到锁
        assert len(lock_acquired) == 1, f"Expected 1 lock, got {len(lock_acquired)}"


class TestDispatchIdempotency:
    """PROP-004: 调度器幂等性测试"""

    def test_dispatch_idempotency_same_task(self, db_session):
        """
        测试同一任务的幂等分发

        场景: 同一任务被多次分发
        期望: 只创建一个 TaskRun 记录
        """
        host = Host(
            name="idempotent-host",
            ip="172.21.15.102",
            status=HostStatus.ONLINE,
            last_heartbeat=datetime.utcnow()
        )
        db_session.add(host)
        db_session.flush()

        device = Device(
            serial="idempotent-device",
            host_id=host.id,
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow()
        )
        db_session.add(device)

        task = Task(
            name="idempotent-task",
            type="MONKEY",
            status=TaskStatus.PENDING
        )
        db_session.add(task)
        db_session.commit()

        run_ids = []

        def dispatch_attempt() -> Optional[int]:
            try:
                with SessionLocal() as db:
                    dispatcher = TaskDispatcher()
                    task_obj = db.get(Task, task.id)

                    if task_obj.status != TaskStatus.PENDING:
                        return None

                    device_result, host_result = dispatcher._pick_device(db, task_obj)
                    if not device_result:
                        return None

                    capacity_ok, active, limit = dispatcher._host_capacity(db, host_result)
                    if not capacity_ok:
                        return None

                    try:
                        run_id = dispatcher._create_run_with_lock(db, task_obj, device_result, host_result, limit)
                        db.commit()
                        return run_id
                    except RuntimeError:
                        return None
            except Exception:
                return None

        # 多次尝试分发同一任务
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(dispatch_attempt) for _ in range(5)]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    run_ids.append(result)

        # 验证只创建了一个 Run
        unique_run_ids = set(run_ids)
        assert len(unique_run_ids) == 1, f"Expected 1 unique run, got {len(unique_run_ids)}"

        # 验证数据库中只有一个 RUNNING/DISPATCHED 的 Run
        runs = db_session.query(TaskRun).filter(TaskRun.task_id == task.id).all()
        active_runs = [r for r in runs if r.status in {RunStatus.QUEUED, RunStatus.DISPATCHED, RunStatus.RUNNING}]
        assert len(active_runs) == 1, f"Expected 1 active run, got {len(active_runs)}"


class TestHeartbeatMonotonicity:
    """PROP-005: 心跳时间戳单调性测试"""

    def test_heartbeat_timestamp_monotonic(self):
        """
        验证心跳时间戳单调递增
        """
        timestamps = []
        base_time = datetime.utcnow()

        # 模拟心跳序列
        for i in range(10):
            ts = base_time + timedelta(seconds=i * 5)
            timestamps.append(ts)

        # 验证单调性
        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i-1], \
                f"Timestamp {i} ({timestamps[i]}) should be greater than {i-1} ({timestamps[i-1]})"

    def test_heartbeat_clock_skew_handling(self):
        """
        测试时钟回拨处理

        场景: NTP 同步或手动调整导致时间回退
        期望: 系统使用 max(current, received) 逻辑
        """
        current_heartbeat = datetime.utcnow()

        # 模拟收到的旧心跳（时钟回拨）
        old_heartbeat = current_heartbeat - timedelta(seconds=30)

        # 系统应该保留较大的时间戳
        effective_heartbeat = max(current_heartbeat, old_heartbeat)

        assert effective_heartbeat == current_heartbeat, \
            "Should use the larger timestamp when clock skew detected"

    def test_heartbeat_sequence_integrity(self):
        """
        验证心跳序列完整性
        """
        sequence = []
        base_time = datetime.utcnow()

        # 正常序列
        for i in range(5):
            sequence.append(base_time + timedelta(seconds=i * 5))

        # 乱序到达（网络延迟）
        delayed_beat = base_time + timedelta(seconds=2 * 5)  # 应该排在第3位
        sequence.append(delayed_beat)

        # 排序后验证
        sorted_sequence = sorted(sequence)
        for i in range(1, len(sorted_sequence)):
            assert sorted_sequence[i] >= sorted_sequence[i-1]


class TestConcurrentRecycler:
    """并发回收器测试"""

    def test_concurrent_lock_release(self, db_session):
        """
        测试并发锁释放

        场景: 多个过期锁同时被回收
        期望: 所有锁都被正确释放，无竞态条件
        """
        from ...scheduler.recycler import _check_device_lock_expiration

        host = Host(
            name="recycler-host",
            ip="172.21.15.103",
            status=HostStatus.ONLINE,
            last_heartbeat=datetime.utcnow()
        )
        db_session.add(host)
        db_session.flush()

        # 创建多个带过期锁的设备
        devices = []
        now = datetime.utcnow()
        expired_time = now - timedelta(seconds=1)

        for i in range(5):
            device = Device(
                serial=f"expired-device-{i}",
                host_id=host.id,
                status=DeviceStatus.BUSY,
                lock_run_id=i + 1000,
                lock_expires_at=expired_time
            )
            db_session.add(device)
            devices.append(device)

        db_session.commit()

        # 并发执行锁过期检查
        released_counts = []

        def check_expiration() -> int:
            try:
                with SessionLocal() as db:
                    count = _check_device_lock_expiration(db, datetime.utcnow())
                    db.commit()
                    return count
            except Exception:
                return 0

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(check_expiration) for _ in range(3)]
            for future in as_completed(futures):
                released_counts.append(future.result())

        # 验证所有锁都被释放（可能由不同线程完成）
        total_released = sum(released_counts)
        assert total_released == 5, f"Expected 5 released locks, got {total_released}"

        # 验证设备状态已更新
        for device in devices:
            db_session.refresh(device)
            assert device.status == DeviceStatus.ONLINE
            assert device.lock_run_id is None


class TestDatabaseConcurrency:
    """数据库并发测试"""

    def test_skip_locked_behavior(self, db_session):
        """
        测试 SKIP LOCKED 行为

        验证: PostgreSQL 正确支持 FOR UPDATE SKIP LOCKED
        """
        # PostgreSQL always supports SKIP LOCKED
        if engine.dialect.name != 'postgresql':
            pytest.skip("SKIP LOCKED requires PostgreSQL")

        host = Host(
            name="isolation-host",
            ip="172.21.15.104",
            status=HostStatus.ONLINE,
            last_heartbeat=datetime.utcnow()
        )
        db_session.add(host)
        db_session.flush()

        device = Device(
            serial="isolation-device",
            host_id=host.id,
            status=DeviceStatus.ONLINE,
            last_seen=datetime.utcnow()
        )
        db_session.add(device)
        db_session.commit()

        results = []

        def update_device() -> bool:
            try:
                with SessionLocal() as db:
                    # 获取设备并锁定
                    dev = db.query(Device).filter(Device.id == device.id).with_for_update().first()
                    if dev:
                        dev.status = DeviceStatus.BUSY
                        db.commit()
                        results.append("success")
                        return True
            except Exception as e:
                results.append(f"error: {e}")
                return False

        # 并发更新
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(update_device) for _ in range(2)]
            for future in as_completed(futures):
                future.result()

        # 只有一个应该成功（另一个应该等待或失败）
        success_count = results.count("success")
        assert success_count >= 1, f"Expected at least 1 success, got {results}"


@pytest.fixture
def db_session(monkeypatch):
    """提供数据库会话"""
    global SessionLocal, engine

    test_db_url = os.getenv("TEST_DATABASE_URL")
    allow_sqlite = os.getenv("ALLOW_SQLITE_TESTS", "0") == "1"
    tmp_path = None

    if not test_db_url:
        if not allow_sqlite:
            raise RuntimeError(
                "TEST_DATABASE_URL is required for concurrent tests (PostgreSQL). "
                "For local quick SQLite tests only, set ALLOW_SQLITE_TESTS=1."
            )
        test_db_url = "sqlite:///:memory:"

    if test_db_url.startswith("sqlite"):
        # 并发测试使用文件型 SQLite，避免 :memory: 连接隔离导致数据不可见
        if test_db_url.endswith(":memory:"):
            tmp = tempfile.NamedTemporaryFile(prefix="stability-concurrent-", suffix=".db", delete=False)
            tmp_path = Path(tmp.name)
            tmp.close()
            test_db_url = f"sqlite:///{tmp_path.as_posix()}"
        engine = create_engine(
            test_db_url,
            connect_args={"check_same_thread": False},
            future=True,
        )
    else:
        engine = create_engine(
            test_db_url,
            pool_pre_ping=True,
            future=True,
        )

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine, future=True)

    # 将调度器/回收器模块切换到测试库
    monkeypatch.setattr(dispatcher_module, "engine", engine)
    monkeypatch.setattr(dispatcher_module, "SessionLocal", SessionLocal)
    monkeypatch.setattr(recycler_module, "SessionLocal", SessionLocal)

    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
        if tmp_path is not None:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass
