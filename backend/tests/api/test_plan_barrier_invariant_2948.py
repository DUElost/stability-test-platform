"""#2948：barrier 跨参数不变式在 Plan 写入边界成立（POST 与 PUT 两路 + 局部 PUT）。

不变式：barrier_timeout_seconds / barrier_max_wait_seconds（非空即硬顶）都必须
≥ 控制面 coordinator 存活窗（`_sched().coordinator_heartbeat_timeout_seconds`，
测试环境默认 300）。票面实测 barrier=5 被 200 接受——本文件钉拒绝路径；
同时钉实现时发现的第二洞：**只改 barrier_max_wait 的 PUT** 原不进任何再校验
（elif 条件集漏项 + 调用漏第 5 参）。
"""
from __future__ import annotations

import itertools

_coord_default = 300  # settings/scheduler.py 默认；测试不改 env，边界按它断言

_counter = itertools.count()


def _uniq(prefix: str) -> str:
    return f"{prefix}-{next(_counter)}"


def _step(sample_script):
    return [{
        "step_key": "init_0", "script_name": sample_script[0].name,
        "script_version": sample_script[0].version, "stage": "init",
        "sort_order": 0, "timeout_seconds": 30,
    }]


def _create(client, auth_headers, sample_script, **extra):
    payload = {"name": _uniq("barrier"), "steps": _step(sample_script),
               "specialty_key": "ops", **extra}
    return client.post("/api/v1/plans", json=payload, headers=auth_headers)


class TestBarrierInvariantOnWrite:
    def test_post_rejects_barrier_below_coord_window(self, client, auth_headers, sample_script):
        resp = _create(client, auth_headers, sample_script, barrier_timeout_seconds=5)
        assert resp.status_code == 422, resp.text
        detail = resp.json()["detail"]
        assert detail["code"] == "INVALID_BARRIER_CONFIGURATION"
        assert detail["field"] == "barrier_timeout_seconds"

    def test_post_rejects_hard_cap_below_coord_window(self, client, auth_headers, sample_script):
        # #117 语义：max_wait 非空=绝对硬顶；5s 硬顶与不设 barrier 同效，同判。
        resp = _create(client, auth_headers, sample_script, barrier_max_wait_seconds=5)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["field"] == "barrier_max_wait_seconds"

    def test_boundary_equal_and_above_and_none_pass(self, client, auth_headers, sample_script):
        for value in (_coord_default, _coord_default + 300, None):
            resp = _create(
                client, auth_headers, sample_script,
                barrier_timeout_seconds=value, barrier_max_wait_seconds=value,
            )
            assert resp.status_code == 201, f"value={value}: {resp.text}"

    def test_put_full_update_rejects(self, client, auth_headers, sample_script):
        created = _create(client, auth_headers, sample_script).json()["data"]
        # 不带 steps：走 elif 局部更新分支（steps 分支有自己的 5 参调用，
        # 两路都要拒，但本例的靶是局部路径）
        body = {"barrier_timeout_seconds": 7,
                "expected_updated_at": created["updated_at"]}
        resp = client.put(f"/api/v1/plans/{created['id']}", json=body, headers=auth_headers)
        assert resp.status_code == 422, resp.text
        assert resp.json()["detail"]["code"] == "INVALID_BARRIER_CONFIGURATION"

    def test_put_only_max_wait_still_validated(self, client, auth_headers, sample_script):
        """elif 漏项的钉子：PUT 仅带 barrier_max_wait_seconds 也必须进再校验。"""
        created = _create(client, auth_headers, sample_script).json()["data"]
        resp = client.put(
            f"/api/v1/plans/{created['id']}",
            json={"barrier_max_wait_seconds": 9,
                  "expected_updated_at": created["updated_at"]},
            headers=auth_headers,
        )
        assert resp.status_code == 422, (
            f"只改硬顶的 PUT 绕过了不变式（elif 条件集回归）：{resp.text}"
        )
        assert resp.json()["detail"]["field"] == "barrier_max_wait_seconds"

    def test_put_valid_hard_cap_persists(self, client, auth_headers, sample_script):
        created = _create(client, auth_headers, sample_script).json()["data"]
        resp = client.put(
            f"/api/v1/plans/{created['id']}",
            json={"barrier_max_wait_seconds": 600,
                  "expected_updated_at": created["updated_at"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["data"]["barrier_max_wait_seconds"] == 600
