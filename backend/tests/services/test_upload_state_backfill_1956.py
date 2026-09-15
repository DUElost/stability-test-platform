"""#1956 存量回填工具的用例。

被测对象：``tools/dev/backfill-no-scan-gate-upload-state.py`` 的 ``plan_changes()``。
它**不复制**判据，而是直接调用 ``backend.services.device_log_event.resolve_initial_upload_state``，
因此本用例同时守卫两件事：

1. 接线正确 —— ``UNIVIEW`` 的等待态（``LOCAL``/``DETECTED``）会被规划为 ``UPLOAD_PENDING``；
2. **不误伤 MTK 语义** —— ``AEE`` 家族仍是「scan xls 引用后才传」，``LOCAL`` 不得被提升；
   已进入上送流程（``REMOTE`` 等）的行也不得被重复规划。

（工具本体是 dev 脚本、带连字符故不能直接 import，用 importlib 按路径加载，与本仓库
``tests/test_agent_priv_boundary.py`` 的做法一致。）
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BACKFILL_SCRIPT = REPO_ROOT / "tools/dev/backfill-no-scan-gate-upload-state.py"


def _load_backfill_module():
    spec = importlib.util.spec_from_file_location(
        "stp_backfill_no_scan_gate_upload_state", BACKFILL_SCRIPT,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _make_event(host_id: str, **overrides):
    from backend.models.device_log_event import DeviceLogEvent

    payload = {
        "serial": "62002360",
        "platform": "UNISOC",
        "event_type": "UNIVIEW",
        "detected_at": datetime.now(timezone.utc),
        "state": "LOCAL",
        "local_path": "/mnt/hdd/aee_events/uniview_watcher/0914/62002360/JE.103000004",
        "host_id": host_id,
    }
    payload.update(overrides)
    return DeviceLogEvent(**payload)


def test_plans_only_no_scan_gate_awaiting_rows(db_session, sample_host):
    module = _load_backfill_module()
    uniview_local = _make_event(sample_host.id)
    mtk_aee_local = _make_event(sample_host.id, platform="MTK", event_type="AEE", serial="MTK-1")
    uniview_remote = _make_event(
        sample_host.id, event_type="UNIVIEW", state="REMOTE", serial="62002361",
        remote_path="/aee/uniview/62002361/JE.1",
    )
    db_session.add_all([uniview_local, mtk_aee_local, uniview_remote])
    db_session.commit()

    changes = module.plan_changes(db_session)

    planned_ids = {row.id for row, _target in changes}
    assert planned_ids == {uniview_local.id}, "只应规划 UNIVIEW 的等待态行"
    assert mtk_aee_local.id not in planned_ids, "MTK AEE 的 LOCAL 语义（scan 引用后才传）不得被提升"
    assert uniview_remote.id not in planned_ids, "已在 REMOTE 的行不得被重复规划"
    assert [target for _row, target in changes] == ["UPLOAD_PENDING"]


def test_plan_changes_honours_limit(db_session, sample_host):
    module = _load_backfill_module()
    for index in range(3):
        db_session.add(_make_event(sample_host.id, serial=f"6200236{index}"))
    db_session.commit()

    assert len(module.plan_changes(db_session, limit=2)) == 2
    assert len(module.plan_changes(db_session, limit=0)) == 3
