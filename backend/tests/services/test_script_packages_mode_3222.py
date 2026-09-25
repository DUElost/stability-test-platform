"""#3222：host 级包模式推导与 fleet 视图（derive + 聚合）。"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.models.host import Host
from backend.services.script_presence import derive_packages_mode, fleet_packages_mode


def _e(name, active):
    # 真实 ack 形态（script_verifier.verify_scripts_payload）：不回传 package_sha256，
    # 带身份判据在控制面 expected 侧——夹具必须镜像这一形态，曾经的版本塞了
    # package_sha256 进 ack，测试绿而生产 48 台全 unknown（2026-09-25 首采当场暴露）。
    return {"name": name, "version": "1.0.0", "ok": True, "package_active": active}


def test_derive_matrix():
    K = {("a", "1.0.0"), ("b", "1.0.0")}
    assert derive_packages_mode([], K, reachable_empty=True) is None
    assert derive_packages_mode([], K, reachable_empty=False) is None
    assert derive_packages_mode([_e("a", True)], set(), False) is None            # expected 全无包身份 → unknown
    assert derive_packages_mode([_e("a", True), _e("b", True)], K, False) == "package"
    assert derive_packages_mode([_e("a", False)], K, False) == "tree"
    assert derive_packages_mode([_e("a", True), _e("b", False)], K, False) == "mixed"
    # ack 里有行但都不在 expected 带身份集（老 agent 未回 package_active 等情形）→ unknown 不误报
    assert derive_packages_mode([_e("c", True)], K, False) is None
    # 可达集内部分目标带包身份：只判带身份的行，不误伤
    assert derive_packages_mode([_e("a", True), _e("z", False)], K, False) == "package"


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
