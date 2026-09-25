"""#3222：host 级包模式推导与 fleet 视图（derive + 聚合）。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models.host import Host
from backend.services.script_presence import derive_packages_mode, fleet_packages_mode


def _e(name, sha, active):
    entry = {"name": name, "version": "1.0.0", "ok": True}
    if sha is not None:
        entry["package_sha256"] = sha
        entry["package_active"] = active
    return entry


def test_derive_matrix():
    assert derive_packages_mode([], reachable_empty=True) is None
    assert derive_packages_mode([], reachable_empty=False) is None
    assert derive_packages_mode([_e("a", None, None)], reachable_empty=False) is None       # 全无包身份 → unknown
    assert derive_packages_mode([_e("a", "x" * 64, True), _e("b", "y" * 64, True)], False) == "package"
    assert derive_packages_mode([_e("a", "x" * 64, False)], False) == "tree"
    assert derive_packages_mode([_e("a", "x" * 64, True), _e("b", "y" * 64, False)], False) == "mixed"
    # 无包身份的行不参与判定（tree 模式 agent 全部 ok 也不误报 package）
    assert derive_packages_mode([_e("a", None, None), _e("b", "y" * 64, True)], False) == "package"


def test_fleet_packages_mode_counts(db_session: Session):
    now = datetime.now(timezone.utc)
    def h(hid, **kw):
        return Host(id=hid, hostname=hid, ip=f"10.0.0.{hid[-1]}", ip_address=f"10.0.0.{hid[-1]}",
                    status="ONLINE", last_heartbeat=now, created_at=now, **kw)
    db_session.add_all([
        h("h1", script_packages_mode="package"),
        h("h2", script_packages_mode="package"),
        h("h3", script_packages_mode="mixed"),
        h("h4"),
        h("h5", script_packages_mode="tree", retired_at=now),  # 退役主机不计
    ])
    db_session.commit()
    assert fleet_packages_mode(db_session) == {"package": 2, "tree": 0, "mixed": 1, "unknown": 1}
