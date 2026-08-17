"""每账户登录锁定的纯逻辑测试。

login_lockout 是控制面模块,但本套件(backend/agent/tests)自包含、无 DB,
CI PR 轻量门禁即可跑;锁定逻辑与 DB 无关,放这里保持全绿速度。
"""
import threading
import time

import pytest

from backend.core import login_lockout
from backend.core.login_lockout import AccountLocked, InvalidCredentials


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


def test_keys_are_opaque_case_variants_do_not_share_bucket():
    """#281 P1:键由调用方提供(路由用数据库用户 ID/原样用户名),模块不再
    lower()。users.username 是大小写敏感的普通 String——Alice 与 alice 是
    两个账户,不能共享锁定桶、不能互相触发锁定。"""
    lock = _make_lock()
    lock.record_failure("alice")
    lock.record_failure("alice")
    assert lock.locked_remaining("Alice") == 0  # 变体互不影响
    assert lock.record_failure("Alice") == 0    # 从 1 重新计数


def test_eviction_never_evicts_locked_accounts():
    """#281 CR Major:淘汰只移除未锁定条目;用户名洪泛不能把仍在锁定期内
    的目标账户挤出锁定桶。"""
    lock = login_lockout.LoginLockout(
        max_failures=2, failure_window_seconds=60, lockout_seconds=60,
        max_tracked_accounts=3,
    )
    lock.record_failure("victim")
    lock.record_failure("victim")  # 触发锁定
    assert lock.locked_remaining("victim") > 0
    for i in range(20):
        lock.record_failure(f"flood_{i}")  # 洪泛 20 个用户名
    assert lock.locked_remaining("victim") > 0  # 锁定中的 victim 仍在


def test_table_capacity_holds_when_all_accounts_locked():
    """#281 二轮 P2:表满且全部在锁定中时,新账户不再插入——容量上限
    (MAX_TRACKED_ACCOUNTS)真正生效,字典不会无限增长。"""
    lock = login_lockout.LoginLockout(
        max_failures=2, failure_window_seconds=60, lockout_seconds=60,
        max_tracked_accounts=3,
    )
    for i in range(3):
        lock.record_failure(f"locked_{i}")
        lock.record_failure(f"locked_{i}")  # 触发锁定
    assert lock.locked_remaining("locked_0") > 0
    assert lock.locked_remaining("locked_2") > 0

    for i in range(5):
        lock.record_failure(f"new_{i}")  # 全员锁定:放弃跟踪新账户

    assert len(lock._state) == 3  # 不再增长
    assert lock.locked_remaining("locked_0") > 0  # 原锁定条目仍在
    assert lock.locked_remaining("new_0") == 0    # 新账户未被跟踪


def test_eviction_frees_slot_for_new_account_after_expiry():
    """锁定期满(prune 清理)后,新账户重新获得跟踪位。"""
    lock = login_lockout.LoginLockout(
        max_failures=2, failure_window_seconds=60, lockout_seconds=1,
        max_tracked_accounts=2,
    )
    lock.record_failure("old_0")
    lock.record_failure("old_0")  # 锁定 1s
    lock.record_failure("old_1")
    lock.record_failure("old_1")  # 锁定 1s
    time.sleep(1.2)  # 两个都到期
    # 到期条目在下次 touch 时被 prune 清掉,新账户可被跟踪
    assert lock.record_failure("fresh") == 0
    assert lock.locked_remaining("fresh") == 0
    lock.record_failure("fresh")
    assert lock.locked_remaining("fresh") > 0  # fresh 达到阈值触发锁定
    assert len(lock._state) <= 2


def test_guarded_attempt_serializes_concurrent_attempts():
    """#281 CR Major:guarded_attempt 在单锁内完成「检查→验证→计数」——
    并发请求不能同时越过锁定检查后在同一批次越阈值试密。max_failures=2
    时 3 个并发失败请求:只有 2 次真正执行密码验证,第 3 个在锁内重新检查
    时拿到 AccountLocked(旧三段式实现会让 3 个请求全部越界试密)。"""
    lock = login_lockout.LoginLockout(
        max_failures=2, failure_window_seconds=60, lockout_seconds=60,
    )
    verified: list[str] = []
    results: list[str] = []
    barrier = threading.Barrier(3)

    def attempt() -> None:
        barrier.wait()
        try:
            lock.guarded_attempt("acct", lambda: (verified.append("v"), False)[1])
            results.append("ok")
        except AccountLocked:
            results.append("locked")
        except InvalidCredentials:
            results.append("failed")

    threads = [threading.Thread(target=attempt) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["failed", "failed", "locked"]
    assert len(verified) == 2
    assert lock.locked_remaining("acct") > 0


def test_guarded_attempt_skips_verify_when_locked():
    lock = _make_lock()
    lock.record_failure("bob")
    lock.record_failure("bob")
    lock.record_failure("bob")  # 触发锁定
    called: list[int] = []
    with pytest.raises(AccountLocked):
        lock.guarded_attempt("bob", lambda: (called.append(1), True)[1])
    assert called == []  # 锁定中不执行密码验证(避免无谓 bcrypt)


def test_guarded_attempt_counts_failure_then_locks_even_with_correct_password():
    lock = _make_lock()  # max_failures=3
    for _ in range(3):
        with pytest.raises(InvalidCredentials):
            lock.guarded_attempt("carol", lambda: False)
    with pytest.raises(AccountLocked):
        lock.guarded_attempt("carol", lambda: True)  # 锁定期内密码正确也拒绝


def test_guarded_attempt_success_clears_failures():
    lock = _make_lock()
    with pytest.raises(InvalidCredentials):
        lock.guarded_attempt("dave", lambda: False)
    lock.guarded_attempt("dave", lambda: True)
    assert lock.locked_remaining("dave") == 0
    with pytest.raises(InvalidCredentials):
        lock.guarded_attempt("dave", lambda: False)
    assert lock.locked_remaining("dave") == 0  # 从 1 重新计数


def test_module_level_defaults_exist():
    assert login_lockout.STP_LOGIN_MAX_FAILURES >= 1
    assert login_lockout.STP_LOGIN_LOCKOUT_SECONDS >= 1
    assert login_lockout.locked_remaining("__never_seen__") == 0
