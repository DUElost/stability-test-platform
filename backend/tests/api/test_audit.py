"""Tests for audit log API routes"""

from backend.core.security import REFRESH_COOKIE_NAME


class TestAuditLogs:
    def test_list_audit_logs(self, client, admin_headers):
        response = client.get("/api/v1/audit-logs", headers=admin_headers)
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data

    def test_list_audit_logs_with_filters(self, client, admin_headers):
        response = client.get(
            "/api/v1/audit-logs",
            params={"resource_type": "task", "action": "create"},
            headers=admin_headers,
        )
        assert response.status_code == 200

    def test_list_audit_logs_requires_auth(self, client):
        response = client.get("/api/v1/audit-logs")
        assert response.status_code in (401, 403)

    def test_login_failed_audit_persists(self, client, admin_headers):
        """#281 P1:失败认证审计必须独立落库——此前 record_audit 后直接抛
        异常,审计行随会话关闭被回滚(get_db 不自动 commit)。"""
        resp = client.post(
            "/api/v1/auth/login",
            data={"username": "ghost_user", "password": "wrongpass123"},
        )
        assert resp.status_code == 401
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(
            item["action"] == "login_failed" and item["resource_id"] == "ghost_user"
            for item in logs["items"]
        )

    def test_audit_ip_not_spoofable_via_xff(self, client, admin_headers):
        """#281 CR Major:审计 IP 不得直接采信 X-Forwarded-For 链首
        (客户端可伪造前置值污染审计来源),必须经可信代理解析。"""
        resp = client.post(
            "/api/v1/auth/login",
            data={"username": "ghost_spoof", "password": "wrongpass123"},
            headers={"X-Forwarded-For": "9.9.9.9, 1.2.3.4"},
        )
        assert resp.status_code == 401
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        entry = next(i for i in logs["items"] if i["action"] == "login_failed")
        assert entry["ip_address"] != "9.9.9.9"  # 伪造链首不得成为审计 IP

    def test_change_password_failed_audit_persists(self, client, admin_headers):
        """#281 P1:改密失败审计同样必须持久化(users.py 失败路径)。"""
        resp = client.post(
            "/api/v1/users/change-password",
            json={"old_password": "wrongpass999", "new_password": "newpass1234"},
            headers=admin_headers,
        )
        assert resp.status_code == 400
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(item["action"] == "change_password_failed" for item in logs["items"])

    def test_token_failed_audit_persists(self, client, admin_headers):
        """#281 P1 缺测项:token_failed 与 login_failed 同模式,必须落库。"""
        resp = client.post(
            "/api/v1/auth/token",
            data={"username": "ghost_token", "password": "wrongpass123"},
        )
        assert resp.status_code == 401
        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(
            item["action"] == "token_failed" and item["resource_id"] == "ghost_token"
            for item in logs["items"]
        )

    def test_refresh_rejected_audit_persists(self, client, admin_headers):
        """#281 P1 缺测项:logout 后(黑名单)的 refresh token 被拒时,
        拒绝审计必须落库(refresh 路径此前写审计后直接 return,随会话回滚)。"""
        login = client.post(
            "/api/v1/auth/login",
            data={"username": "admin", "password": "adminpass123"},
        )
        assert login.status_code == 200
        refresh_cookie = login.cookies.get(REFRESH_COOKIE_NAME)
        assert refresh_cookie

        logout = client.post("/api/v1/auth/logout")
        assert logout.status_code == 200

        rejected = client.post(
            "/api/v1/auth/refresh",
            cookies={REFRESH_COOKIE_NAME: refresh_cookie},
        )
        assert rejected.status_code == 401

        logs = client.get("/api/v1/audit-logs", headers=admin_headers).json()
        assert any(item["action"] == "refresh_rejected" for item in logs["items"])


def test_audit_log_filters_username_ip_resource_id(client, admin_headers, db_session):
    """#628：username / ip_address / resource_id 精确过滤，且可组合。

    三类筛选都直接打在审计行自带的快照列上（不 join users / 不转主键类型），
    resource_id 按 varchar 精确匹配（#832）。
    """
    from backend.models.audit import AuditLog

    db_session.add_all([
        AuditLog(
            username="audit628_alice", action="update", resource_type="plan",
            resource_id="62842", ip_address="10.62.8.1", details={},
        ),
        AuditLog(
            username="audit628_bob", action="update", resource_type="plan",
            resource_id="62843", ip_address="10.62.8.2", details={},
        ),
        AuditLog(
            username="audit628_alice", action="create", resource_type="task",
            resource_id="62899", ip_address="10.62.8.2", details={},
        ),
    ])
    db_session.commit()

    def fetch(**params) -> dict:
        resp = client.get("/api/v1/audit-logs", params=params, headers=admin_headers)
        assert resp.status_code == 200, resp.text
        return resp.json()

    assert fetch(username="audit628_alice")["total"] == 2
    assert fetch(ip_address="10.62.8.2")["total"] == 2
    assert fetch(resource_id="62842")["total"] == 1
    # 组合收窄：alice 在 10.62.8.2 上的操作
    combined = fetch(username="audit628_alice", ip_address="10.62.8.2")
    assert combined["total"] == 1
    assert combined["items"][0]["action"] == "create"
    # 部分匹配不成立（精确匹配语义）
    assert fetch(username="audit628_alice", resource_id="6284")["total"] == 0


class TestAuditFilterFacets:
    """#2629：筛选候选必须来自**实际写入过**的词表。

    前端原先硬编码「9 资源 / 6 操作」，其中 6 个字面量写入侧根本不存在 ⇒ 选中即
    「共 0 条」的**自信假阴性**；真实存在的 18 种资源类型反而没有入口。
    本类的核心判据不是「返回了哪些值」，而是「**返回的值一定筛得出东西**」。
    """

    @staticmethod
    def _seed(db_session):
        from backend.models.audit import AuditLog

        many = "act2629_many"
        one = "act2629_one"
        db_session.add_all([
            AuditLog(username="audit2629_a", action=many, resource_type="rt2629_alpha",
                     resource_id="1", ip_address="10.62.8.1", details={}),
            AuditLog(username="audit2629_a", action=many, resource_type="rt2629_alpha",
                     resource_id="2", ip_address="10.62.8.1", details={}),
            AuditLog(username="audit2629_b", action=many, resource_type="rt2629_beta",
                     resource_id="3", ip_address="10.62.8.2", details={}),
            AuditLog(username="audit2629_b", action=one, resource_type="rt2629_beta",
                     resource_id="4", ip_address="10.62.8.2", details={}),
        ])
        db_session.commit()
        return many, one

    def test_facets_are_all_selectable(self, client, admin_headers, db_session):
        """**自洽判据**：facets 给出的每个值，用它去筛都必须至少命中 1 条。

        这条钉的是「选项 ⇔ 可筛出的结果」这个不变式本身——假阴性正是它被破坏的形态，
        而且它不依赖任何词表清单，所以写入侧将来改名/新增都不需要改用例。
        """
        self._seed(db_session)
        facets = client.get("/api/v1/audit-logs/facets", headers=admin_headers).json()

        checked = 0
        for key, param in (("resource_types", "resource_type"), ("actions", "action")):
            for entry in facets[key]:
                resp = client.get(
                    "/api/v1/audit-logs",
                    params={param: entry["value"]},
                    headers=admin_headers,
                )
                assert resp.status_code == 200, resp.text
                assert resp.json()["total"] >= 1, (
                    f"facets 给出的 {param}={entry['value']!r} 筛出 0 条"
                    "——又造出了一个假阴性选项（#2629）"
                )
                checked += 1
        assert checked >= 4, f"覆盖面小得可疑（只核对 {checked} 个值）"

    def test_facets_reflect_written_counts_and_never_invent_values(
        self, client, admin_headers, db_session,
    ):
        self._seed(db_session)
        facets = client.get("/api/v1/audit-logs/facets", headers=admin_headers).json()

        resource_counts = {e["value"]: e["count"] for e in facets["resource_types"]}
        action_counts = {e["value"]: e["count"] for e in facets["actions"]}
        assert resource_counts["rt2629_alpha"] == 2
        assert resource_counts["rt2629_beta"] == 2
        assert action_counts["act2629_many"] == 3
        assert action_counts["act2629_one"] == 1

        # 死选项的回归判据：**从未写入过的值不得出现在候选里**
        assert "tool" not in resource_counts
        assert "tool_category" not in resource_counts
        assert "template" not in resource_counts
        assert "dispatch" not in action_counts
        assert "cancel" not in action_counts

    @staticmethod
    def _seed_2778(db_session):
        """历史别名 + 规范值并存（写侧已统一，存量行按 append-only 保留）。"""
        from backend.models.audit import AuditLog

        db_session.add_all([
            AuditLog(username="audit2778", action="job_terminalized", resource_type="job",
                     resource_id="1", details={}),
            AuditLog(username="audit2778", action="job_terminalized", resource_type="job_instance",
                     resource_id="2", details={}),
            AuditLog(username="audit2778", action="scan", resource_type="script_catalog",
                     resource_id=None, details={}),
            AuditLog(username="audit2778", action="create", resource_type="script",
                     resource_id="3", details={}),
        ])
        db_session.commit()

    def test_facets_merge_historical_aliases_into_canonical(
        self, client, admin_headers, db_session,
    ):
        """#2778：资源维按规范值归并历史别名（计数求和）——下拉里不得并列两个半真选项。

        同一 job 实体曾按收尾路径分裂为 job / job_instance；合并后只出现
        `job_instance` 一项且计数为两侧之和，管理员不再需要知道"这个 job 是谁收尾的"
        才能筛到它。
        """
        self._seed_2778(db_session)
        facets = client.get("/api/v1/audit-logs/facets", headers=admin_headers).json()
        resource_counts = {e["value"]: e["count"] for e in facets["resource_types"]}

        assert resource_counts["job_instance"] == 2, resource_counts
        assert resource_counts["script"] == 2, resource_counts
        # 别名不得再作为独立可选项出现
        assert "job" not in resource_counts
        assert "script_catalog" not in resource_counts

    def test_resource_type_filter_expands_historical_aliases(
        self, client, admin_headers, db_session,
    ):
        """#2778：按规范值筛选必须同时命中历史别名的存量行（反向输入别名亦然）。"""
        self._seed_2778(db_session)

        def total(params: dict) -> int:
            resp = client.get(
                "/api/v1/audit-logs", params=params, headers=admin_headers,
            )
            assert resp.status_code == 200, resp.text
            return resp.json()["total"]

        # 规范值 → 展开命中历史 job 行；历史别名输入 → 归一到规范值后同样展开
        assert total({"resource_type": "job_instance"}) == 2
        assert total({"resource_type": "job"}) == 2
        assert total({"resource_type": "script"}) == 2
        # 未登记的规范值不展开、不误伤
        assert total({"resource_type": "plan_run"}) == 0

    def test_facets_are_ordered_by_frequency(
        self, client, admin_headers, db_session,
    ):
        """按条数倒序——高频合规关注点排在前面（下拉/补全的第一屏就是它）。"""
        many, one = self._seed(db_session)
        facets = client.get("/api/v1/audit-logs/facets", headers=admin_headers).json()
        actions = [e["value"] for e in facets["actions"]]
        assert actions.index(many) < actions.index(one)

    def test_facets_require_auth(self, client):
        assert client.get("/api/v1/audit-logs/facets").status_code in (401, 403)

    def test_missing_table_predicate_matches_the_list_route_compat(self):
        """表不存在的兜底判定被两条路由**共用**（原先内联在 list 里，容易各写一份）。

        直接测纯函数：造 `ProgrammingError` 需要真缺表，而漏掉某一形态的后果是
        老库上 facets 直接 500——判据得钉住形态本身。
        """
        from sqlalchemy.exc import ProgrammingError

        from backend.api.routes.audit import _is_missing_audit_table

        def boom(message: str) -> ProgrammingError:
            return ProgrammingError("SELECT ...", {}, Exception(message))

        assert _is_missing_audit_table(boom('relation "audit_logs" does not exist'))
        assert _is_missing_audit_table(boom("UndefinedTable: audit_logs"))
        assert _is_missing_audit_table(boom("表 audit_logs 不存在"))
        # 别的错误不得被吞成空结果（尤其：另一张表缺失、权限、语法）
        assert not _is_missing_audit_table(boom('relation "users" does not exist'))
        assert not _is_missing_audit_table(boom("permission denied for table audit_logs"))
        assert not _is_missing_audit_table(boom("syntax error at audit_logs"))


    def test_facets_are_bounded_per_dimension(self, client, admin_headers, db_session):
        """#2694：facets 每维**有界**（top-N），不再把全部 distinct 值倒给前端。

        生产实测 `audit_logs` 已 266,882 行、`action` 86+ 种且无界增长；无界返回会让
        每次开 `/audit` 把整个聚合结果搬给前端 datalist。本用例造 > N 个 distinct
        action，断言返回条数被 `_FACET_LIMIT` 截断。
        """
        from backend.api.routes.audit import _FACET_LIMIT
        from backend.models.audit import AuditLog

        for i in range(_FACET_LIMIT + 5):
            db_session.add(AuditLog(
                username="audit2694", action=f"bounded_action_{i:03d}",
                resource_type="rt2694", resource_id=str(i),
                ip_address="10.69.4.1", details={},
            ))
        db_session.commit()

        facets = client.get("/api/v1/audit-logs/facets", headers=admin_headers).json()
        assert len(facets["actions"]) <= _FACET_LIMIT, (
            f"actions 未被截断：{len(facets['actions'])} > {_FACET_LIMIT}（#2694）"
        )
        # 有界不等于空：仍须给出候选
        assert len(facets["actions"]) == _FACET_LIMIT, (
            f"应恰好返回 {_FACET_LIMIT} 条（种子 > N），实际 {len(facets['actions'])}"
        )

    def test_facets_bounded_still_returns_top_by_count(self, client, admin_headers, db_session):
        """#2694：截断须保留**按条数倒序**的语义——高频项优先，不被长尾挤掉。

        这是有界化的价值所在：下拉里出现的应是管理员大概率要找的那几个。
        """
        from backend.models.audit import AuditLog

        # 造一个明显高频项 + 若干低频项
        for _ in range(5):
            db_session.add(AuditLog(
                username="audit2694b", action="hot_action",
                resource_type="rt2694b", resource_id="h",
                ip_address="10.69.4.2", details={},
            ))
        for i in range(3):
            db_session.add(AuditLog(
                username="audit2694b", action=f"cold_action_{i}",
                resource_type="rt2694b", resource_id=str(i),
                ip_address="10.69.4.2", details={},
            ))
        db_session.commit()

        facets = client.get("/api/v1/audit-logs/facets", headers=admin_headers).json()
        values = [e["value"] for e in facets["actions"]]
        assert values[0] == "hot_action", f"应按条数倒序（高频优先），实际首位 {values[0]}"
