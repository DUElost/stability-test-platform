"""每账户登录锁定的纯逻辑测试。

login_lockout 是控制面模块,但本套件(backend/agent/tests)自包含、无 DB,
CI PR 轻量门禁即可跑;锁定逻辑与 DB 无关,放这里保持全绿速度。
"""
import time

from backend.core import login_lockout


def _make_lock() -> login_lockout.LoginLockout:
    return login_lockout.LoginLockout(
        max_failures=3, failure_window_seconds=60, lockout_seconds=5,
    )


def test_lockout_triggers_on_threshold_and_expires():
    lock = _make_lock()
    assert lock.record_failure("alice") == 0
    assert lock.record_failure("alice") == 0
    assert lock.record_failure("alice") == 5  # 第 3 次触发锁定
    assert lock.locked_remaining("alice") > 0
    time.sleep(5.2)
    assert lock.locked_remaining("alice") == 0  # 锁定期满自动清零


def test_success_clears_failures():
    lock = _make_lock()
    lock.record_failure("alice")
    lock.record_failure("alice")
    lock.record_success("alice")
    assert lock.locked_remaining("alice") == 0
    assert lock.record_failure("alice") == 0  # 从 1 重新计数,而非接续到阈值


def test_failure_window_prunes_old_failures():
    lock = login_lockout.LoginLockout(
        max_failures=3, failure_window_seconds=1, lockout_seconds=300,
    )
    lock.record_failure("bob")
    time.sleep(1.2)
    assert lock.record_failure("bob") == 0  # 旧失败已出窗,重新从 1 计数


def test_usernames_are_case_insensitive():
    lock = _make_lock()
    lock.record_failure("Alice")
    lock.record_failure("alice")
    assert lock.locked_remaining("ALICE") >= 0
    # 同一账户不同大小写共享一个桶:再失败一次即触发锁定
    assert lock.record_failure("aLiCe") == 5


def test_module_level_defaults_exist():
    assert login_lockout.STP_LOGIN_MAX_FAILURES >= 1
    assert login_lockout.STP_LOGIN_LOCKOUT_SECONDS >= 1
    assert login_lockout.locked_remaining("__never_seen__") == 0
