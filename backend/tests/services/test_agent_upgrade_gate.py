"""#1520 垂直切片：Agent upgrade-gate 直测（#3295 后 HTTP 映射住在 api 层）。

服务层（``backend/services/agent_upgrade_gate.py``）不再构造 HTTP 异常：
``begin_host_upgrade`` 的领域异常原样传播；翻译器 ``raise_upgrade_gate_http``
与元组 ``UPGRADE_GATE_DOMAIN_ERRORS`` 在 ``backend/api/error_handlers.py``，
由 ``backend/api/routes/agent_api.py`` 的端点显式捕获调用。
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from backend.api.error_handlers import (
    raise_upgrade_gate_http as _raise_upgrade_gate_http,
)
from backend.api.routes.agent_api import (
    acquire_upgrade_gate as upgrade_gate_endpoint,
)
from backend.services.agent_upgrade_gate import (
    UpgradeGateReleaseRequest,
    UpgradeGateRequest,
    release_agent_upgrade_gate,
)
from backend.services.errors import ServiceError
from backend.services.host_maintenance import HostMaintenanceConflict
from backend.services.host_upgrade_gate import (
    HostAbortDrainTimeoutError,
    HostAbortPendingError,
    HostHasActiveJobsError,
    HostNotFoundError,
    HostRetiredError,
    HostUpgradeGateError,
)
from fastapi import HTTPException


class TestRaiseUpgradeGateHttp:
    def test_host_not_found_404(self):
        with pytest.raises(HTTPException) as exc:
            _raise_upgrade_gate_http("h1", HostNotFoundError("missing"))
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "HOST_NOT_FOUND"

    def test_active_jobs_409(self):
        with pytest.raises(HTTPException) as exc:
            _raise_upgrade_gate_http(
                "h1",
                HostHasActiveJobsError(active_jobs=[{"id": 1}]),
            )
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "HOST_HAS_ACTIVE_JOBS"


class TestAcquireReleaseGuards:
    def test_release_requires_holder(self):
        with pytest.raises(ServiceError) as exc:
            release_agent_upgrade_gate(
                db=MagicMock(),
                host_id="h1",
                payload=UpgradeGateReleaseRequest(holder="  "),
            )
        assert exc.value.status == 400

    def test_acquire_maps_domain_error(self):
        db = MagicMock()
        with patch(
            "backend.services.agent_upgrade_gate.begin_host_upgrade",
            side_effect=HostNotFoundError("gone"),
        ):
            with pytest.raises(HTTPException) as exc:
                upgrade_gate_endpoint(
                    "h1",
                    UpgradeGateRequest(holder="ansible"),
                    db,
                )
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "HOST_NOT_FOUND"


# ══════════════════════════════════════════════════════════════════════════════
# #2638：领域异常 ⇒ HTTP 的**可达性**契约（#3295 后钉在路由端点函数上）
#
# `raise_upgrade_gate_http` 里每条 `isinstance` 分支都得被端点的 `except` 元组接住，
# 否则那条分支永远不执行、异常原样冒到框架层变 500。本单实例是 `HostRetiredError`
# （ADR-0038 D5 的 409 `HOST_RETIRED` 早就写好了，只是没人接）。
# 判据取**运行时子类集合**而不是源码字面量：新增异常若没进下面的工厂表，
# `test_factory_table_covers_every_gate_error` 会直接报「用例已过期」，
# 而不是让契约断言静默少一条（#2639 那一族「锚点漂移 ⇒ 否定断言恒真」的失败形态）。
# ══════════════════════════════════════════════════════════════════════════════

_ACTIVE = [{"id": 1, "plan_run_id": 2, "plan_id": 3}]

#: name -> (工厂, 期望状态码, 期望 code)
_GATE_ERROR_CONTRACT = {
    "HostNotFoundError": (lambda: HostNotFoundError("gone"), 404, "HOST_NOT_FOUND"),
    "HostRetiredError": (lambda: HostRetiredError("retired"), 409, "HOST_RETIRED"),
    "HostHasActiveJobsError": (lambda: HostHasActiveJobsError(_ACTIVE), 409,
                               "HOST_HAS_ACTIVE_JOBS"),
    "HostAbortPendingError": (lambda: HostAbortPendingError(_ACTIVE, 30), 409,
                              "HOST_ABORT_PENDING"),
    "HostAbortDrainTimeoutError": (lambda: HostAbortDrainTimeoutError([7], None), 504,
                                   "ABORT_DRAIN_TIMEOUT"),
    # 不继承 HostUpgradeGateError（来自 host_maintenance，也没有 `.code`），但仍必须被映射
    "HostMaintenanceConflict": (lambda: HostMaintenanceConflict("busy"), 409,
                                "HOST_IN_MAINTENANCE"),
}


def test_factory_table_covers_every_gate_error():
    """工厂表必须与 `HostUpgradeGateError` 的运行时子类集合**双向**相等。

    只写「子集」断言的话，新增一个异常类就不会被契约用例覆盖——那正是 #2638 的形状。
    """
    live = {cls.__name__ for cls in HostUpgradeGateError.__subclasses__()}
    assert live == set(_GATE_ERROR_CONTRACT) - {"HostMaintenanceConflict"}, (
        f"领域异常族与映射契约表漂移（新增/改名/删除异常都要同步本表）：{live}"
    )


@pytest.mark.parametrize("name", sorted(_GATE_ERROR_CONTRACT))
def test_acquire_translates_every_domain_error_to_http(name):
    """每一条领域异常都必须变成带 `code` 的 HTTP 错误，不得原样上抛（⇒ 500）。"""
    factory, expected_status, expected_code = _GATE_ERROR_CONTRACT[name]
    error = factory()
    db = MagicMock()
    with patch(
        "backend.services.agent_upgrade_gate.begin_host_upgrade",
        side_effect=error,
    ):
        with pytest.raises(HTTPException) as exc:
            upgrade_gate_endpoint("h1", UpgradeGateRequest(holder="ansible"), db)

    assert exc.value.status_code == expected_status, name
    assert exc.value.detail["code"] == expected_code, (
        f"{name} 落到了别的映射分支：{exc.value.detail['code']} != {expected_code}"
    )
    if hasattr(error, "code"):
        # 族自己的 `code` 与映射必须同源，否则调用方按 code 分支时会看到两套口径
        assert error.code == expected_code, f"{name}.code 漂移：{error.code}"
    db.commit.assert_not_called()  # 拒绝路径不得写审计/提交


def test_retired_host_gate_acquire_is_409_not_500():
    """#2638 的原始形状：退役主机申请升级窗口要 409 `HOST_RETIRED`，不是框架层 500。

    这条**不 patch 映射函数**，走真实的端点函数 + `raise_upgrade_gate_http`，
    因此如果将来有人把 `HostRetiredError` 从 `UPGRADE_GATE_DOMAIN_ERRORS` 元组里拿掉，本用例会以
    `HostRetiredError` 失败（而不是 HTTPException）——即「分支重新变成不可达」
    会被立刻抓住。
    """
    db = MagicMock()
    with patch(
        "backend.services.agent_upgrade_gate.begin_host_upgrade",
        side_effect=HostRetiredError("host h1 is retired"),
    ):
        with pytest.raises(HTTPException) as exc:
            upgrade_gate_endpoint("h1", UpgradeGateRequest(holder="ansible"), db)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "HOST_RETIRED"
    assert "retired" in exc.value.detail["message"]
