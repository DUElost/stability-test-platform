"""plan.barrier_timeout_seconds 必须一路走到 Agent（#117 第一步）。

链路：Plan 列 → build_lifecycle → pipeline_def.lifecycle → Agent
      Plan 列 → plan_snapshot → build_lifecycle_from_snapshot → 同上

任一环漏掉，Agent 都会静默退回 600s —— 而 600s 只覆盖单设备 init ≤2.5min，
含刷机的计划会让先做完 init 的设备被慢同伴连坐失败。这种回退不会报错，
只会表现为「一批健康设备莫名其妙终止」，所以必须两条路径都测。
"""

from backend.api.routes.plans import PlanStepIn, _assemble_lifecycle_for_validation
from backend.core.pipeline_validator import validate_pipeline_def
from backend.models.plan import Plan, PlanStep
from backend.services.plan_dispatcher_core import (
    build_lifecycle_from_steps,
    build_lifecycle_from_snapshot,
    build_plan_snapshot,
)


def _plan(**kw):
    defaults = dict(
        id=1, name="p", description=None, failure_threshold=0.05,
        patrol_interval_seconds=None, timeout_seconds=None,
        barrier_timeout_seconds=None, auto_archive_interval_seconds=None,
        next_plan_id=None, watcher_policy=None,
    )
    defaults.update(kw)
    return Plan(**defaults)


def _step():
    return PlanStep(
        plan_id=1, step_key="check_device", script_name="check_device",
        script_version="1.0.0", stage="init", sort_order=0,
        timeout_seconds=30, retry=0, enabled=True,
    )


_META = {("check_device", "1.0.0"): {"default_params": {}, "nfs_path": "/s/x.py",
                                     "param_schema": {}}}


class TestBuildLifecycle:
    def test_absent_when_plan_leaves_it_unset(self):
        """NULL 不写入 —— Agent 才能回落到 env / 600s，既有计划行为不变。"""
        lc = build_lifecycle_from_steps(_plan(), [_step()], _META)
        assert "barrier_timeout_seconds" not in lc

    def test_carried_when_configured(self):
        lc = build_lifecycle_from_steps(_plan(barrier_timeout_seconds=172800), [_step()], _META)
        assert lc["barrier_timeout_seconds"] == 172800


class TestSnapshotRoundTrip:
    def test_snapshot_carries_the_field(self):
        snap = build_plan_snapshot(
            _plan(barrier_timeout_seconds=7200), [_step()], _META, 0.05,
        )
        assert snap["plan"]["barrier_timeout_seconds"] == 7200

    def test_replay_from_snapshot_preserves_it(self):
        """重放旧 PlanRun 不能悄悄退回 600s。"""
        snap = build_plan_snapshot(
            _plan(barrier_timeout_seconds=7200), [_step()], _META, 0.05,
        )
        lc = build_lifecycle_from_snapshot(snap)
        assert lc["barrier_timeout_seconds"] == 7200

    def test_replay_omits_it_when_unset(self):
        snap = build_plan_snapshot(_plan(), [_step()], _META, 0.05)
        assert build_lifecycle_from_snapshot(snap).get("barrier_timeout_seconds") is None


class TestApiRoundTrip:
    @staticmethod
    def _create(client, headers, **extra):
        body = {
            "name": f"barrier-{extra.pop('tag', 'a')}",
            "steps": [{
                "step_key": "check_device", "script_name": "check_device",
                "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                "timeout_seconds": 30, "retry": 0, "enabled": True,
            }],
        }
        body.update(extra)
        return client.post("/api/v1/plans", json=body, headers=headers)

    def test_create_and_read_back(self, client, auth_headers, sample_script):
        resp = self._create(client, auth_headers, barrier_timeout_seconds=172800, tag="c")
        assert resp.status_code == 201, resp.text
        plan_id = resp.json()["data"]["id"]
        assert resp.json()["data"]["barrier_timeout_seconds"] == 172800

        got = client.get(f"/api/v1/plans/{plan_id}", headers=auth_headers)
        assert got.json()["data"]["barrier_timeout_seconds"] == 172800

    def test_defaults_to_null(self, client, auth_headers, sample_script):
        resp = self._create(client, auth_headers, tag="d")
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["barrier_timeout_seconds"] is None

    def test_update_can_set_and_clear(self, client, auth_headers, sample_script):
        plan_id = self._create(client, auth_headers, tag="u").json()["data"]["id"]

        up = client.put(f"/api/v1/plans/{plan_id}",
                        json={"barrier_timeout_seconds": 7200}, headers=auth_headers)
        assert up.status_code == 200, up.text
        assert up.json()["data"]["barrier_timeout_seconds"] == 7200

        # 显式置 null 要能清回缺省（走 model_fields_set，不是 None 判断）
        clr = client.put(f"/api/v1/plans/{plan_id}",
                         json={"barrier_timeout_seconds": None}, headers=auth_headers)
        assert clr.status_code == 200, clr.text
        assert clr.json()["data"]["barrier_timeout_seconds"] is None

    def test_rejects_non_positive(self, client, auth_headers, sample_script):
        """barrier 没有"不限"语义：0 会让先到者立刻超时并连坐失败。"""
        resp = self._create(client, auth_headers, barrier_timeout_seconds=0, tag="z")
        assert resp.status_code == 422


class TestGeneratedLifecyclePassesSchema:
    """**生成出来的 lifecycle 必须自己能过 schema。**

    这一环最初漏掉了，结果是：字段一路串通、9 条串联用例全绿、CI 全绿，但
    `pipeline_schema.json` 的 lifecycle 是 additionalProperties:false 且没有
    这个键 —— 于是任何配了 barrier 预算的计划在 prepare 阶段
    (`plan_dispatcher_sync` 调 validate_pipeline_def) 或到了 Agent
    (`job_runner._validate_pipeline_def`) 直接被拒。

    不是"可配但没生效"，是"配上就失败"。所以断言必须落在**校验器**上，
    而不只是落在 dict 里有没有这个键。
    """

    def test_lifecycle_with_barrier_passes_validation(self):
        lc = build_lifecycle_from_steps(
            _plan(barrier_timeout_seconds=172800), [_step()], _META,
        )
        assert lc["barrier_timeout_seconds"] == 172800
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_snapshot_replayed_lifecycle_passes_validation(self):
        snap = build_plan_snapshot(
            _plan(barrier_timeout_seconds=7200), [_step()], _META, 0.05,
        )
        ok, errors = validate_pipeline_def(
            {"lifecycle": build_lifecycle_from_snapshot(snap)}
        )
        assert ok, errors

    def test_schema_still_rejects_genuinely_unknown_keys(self):
        """加字段不能顺手把 additionalProperties 的保护也放开。"""
        lc = build_lifecycle_from_steps(_plan(), [_step()], _META)
        lc["totally_made_up"] = 1
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert not ok
        assert any("totally_made_up" in e for e in errors)


# ── stall_seconds 管道（#115 阶段 1 修正）──────────────────────────────
# 与 barrier 同族的坑：字段一路串通但 schema 没加 → 配上就校验失败。
# 这里断言必须落在校验器上，且验证「NULL 不写入」——否则所有没配的现有
# 步骤都会带着 stall_seconds: null 被 schema 拒。


def _step_with_stall(stall_seconds, *, stage="init", step_key="check_device"):
    s = _step()
    s.stage = stage
    s.step_key = step_key
    s.stall_seconds = stall_seconds
    return s


def _lifecycle_from_api_plan(plan: dict) -> dict:
    """API 往返后走 plans.py 的校验组装路径（与 create/update 同源）。"""
    steps = [PlanStepIn.model_validate(s) for s in plan["steps"]]
    return _assemble_lifecycle_for_validation(
        steps,
        plan.get("patrol_interval_seconds"),
        plan.get("timeout_seconds"),
        plan.get("barrier_timeout_seconds"),
    )


class TestStallSecondsPipeline:
    def test_lifecycle_with_stall_seconds_passes_validation(self):
        lc = build_lifecycle_from_steps(
            _plan(), [_step_with_stall(120)], _META,
        )
        step = lc["init"][0]
        assert step["stall_seconds"] == 120
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_null_stall_seconds_is_not_written(self):
        """没配的步骤不能带 stall_seconds: null —— schema 会拒（None 不是 integer）。"""
        lc = build_lifecycle_from_steps(_plan(), [_step()], _META)
        assert "stall_seconds" not in lc["init"][0]
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_snapshot_round_trip_preserves_stall_seconds(self):
        snap = build_plan_snapshot(
            _plan(), [_step_with_stall(600)], _META, 0.05,
        )
        assert snap["steps"][0]["stall_seconds"] == 600
        lc = build_lifecycle_from_snapshot(snap)
        assert lc["init"][0]["stall_seconds"] == 600
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_snapshot_without_stall_seconds_stays_absent(self):
        snap = build_plan_snapshot(_plan(), [_step()], _META, 0.05)
        assert snap["steps"][0].get("stall_seconds") is None
        lc = build_lifecycle_from_snapshot(snap)
        assert "stall_seconds" not in lc["init"][0]
        assert validate_pipeline_def({"lifecycle": lc})[0]

    def test_schema_accepts_zero_as_disabled(self):
        """与 timeout_seconds 不同，0 是合法且有意义的（= 不启用）。"""
        lc = build_lifecycle_from_steps(
            _plan(), [_step_with_stall(0)], _META,
        )
        assert lc["init"][0]["stall_seconds"] == 0
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_patrol_stage_carries_stall_seconds(self):
        lc = build_lifecycle_from_steps(
            _plan(patrol_interval_seconds=60),
            [
                _step(),
                _step_with_stall(300, stage="patrol", step_key="patrol_check"),
            ],
            _META,
        )
        assert "stall_seconds" not in lc["init"][0]
        patrol_step = lc["patrol"]["steps"][0]
        assert patrol_step["stall_seconds"] == 300
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_patrol_snapshot_round_trip_preserves_stall_seconds(self):
        snap = build_plan_snapshot(
            _plan(patrol_interval_seconds=60),
            [
                _step(),
                _step_with_stall(300, stage="patrol", step_key="patrol_check"),
            ],
            _META,
            0.05,
        )
        patrol_snap = next(s for s in snap["steps"] if s["stage"] == "patrol")
        assert patrol_snap["stall_seconds"] == 300
        lc = build_lifecycle_from_snapshot(snap)
        assert lc["patrol"]["steps"][0]["stall_seconds"] == 300
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors


class TestStallSecondsApi:
    @staticmethod
    def _create(client, headers, stall_seconds):
        resp = client.post("/api/v1/plans", json={
            "name": "stall-test",
            "steps": [{
                "step_key": "check_device", "script_name": "check_device",
                "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                "timeout_seconds": 30, "stall_seconds": stall_seconds,
                "retry": 0, "enabled": True,
            }],
        }, headers=headers)
        assert resp.status_code == 201, resp.text
        return resp.json()["data"]

    def test_create_and_read_back(self, client, auth_headers, sample_script):
        plan = self._create(client, auth_headers, 120)
        assert plan["steps"][0]["stall_seconds"] == 120

    def test_omitted_defaults_to_null(self, client, auth_headers, sample_script):
        resp = client.post("/api/v1/plans", json={
            "name": "stall-null",
            "steps": [{
                "step_key": "check_device", "script_name": "check_device",
                "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                "timeout_seconds": 30, "retry": 0, "enabled": True,
            }],
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["data"]["steps"][0]["stall_seconds"] is None

    def test_update_preserves_stall_seconds(self, client, auth_headers, sample_script):
        plan_id = self._create(client, auth_headers, 120)["id"]
        up = client.put(f"/api/v1/plans/{plan_id}", json={
            "steps": [{
                "step_key": "check_device", "script_name": "check_device",
                "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                "timeout_seconds": 60, "stall_seconds": 600,
                "retry": 0, "enabled": True,
            }],
        }, headers=auth_headers)
        assert up.status_code == 200, up.text
        assert up.json()["data"]["steps"][0]["stall_seconds"] == 600

    def test_update_can_clear_stall_seconds(self, client, auth_headers, sample_script):
        plan_id = self._create(client, auth_headers, 120)["id"]
        clr = client.put(f"/api/v1/plans/{plan_id}", json={
            "steps": [{
                "step_key": "check_device", "script_name": "check_device",
                "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                "timeout_seconds": 30, "stall_seconds": None,
                "retry": 0, "enabled": True,
            }],
        }, headers=auth_headers)
        assert clr.status_code == 200, clr.text
        plan = clr.json()["data"]
        assert plan["steps"][0]["stall_seconds"] is None
        lc = _lifecycle_from_api_plan(plan)
        assert "stall_seconds" not in lc["init"][0]
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_create_patrol_stall_seconds_via_api_validation_path(
        self, client, auth_headers, sample_script,
    ):
        """patrol 的 stall_seconds 必须经 _assemble_lifecycle_for_validation 写入。"""
        resp = client.post("/api/v1/plans", json={
            "name": "stall-patrol-api",
            "patrol_interval_seconds": 60,
            "steps": [
                {
                    "step_key": "check_device", "script_name": "check_device",
                    "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                    "timeout_seconds": 30, "retry": 0, "enabled": True,
                },
                {
                    "step_key": "patrol_check", "script_name": "check_device",
                    "script_version": "1.0.0", "stage": "patrol", "sort_order": 0,
                    "timeout_seconds": 30, "stall_seconds": 300,
                    "retry": 0, "enabled": True,
                },
            ],
        }, headers=auth_headers)
        assert resp.status_code == 201, resp.text
        plan = resp.json()["data"]
        patrol_step = next(s for s in plan["steps"] if s["stage"] == "patrol")
        assert patrol_step["stall_seconds"] == 300
        lc = _lifecycle_from_api_plan(plan)
        assert "stall_seconds" not in lc["init"][0]
        assert lc["patrol"]["steps"][0]["stall_seconds"] == 300
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_negative_stall_seconds_rejected(self, client, auth_headers, sample_script):
        resp = client.post("/api/v1/plans", json={
            "name": "stall-neg",
            "steps": [{
                "step_key": "check_device", "script_name": "check_device",
                "script_version": "1.0.0", "stage": "init", "sort_order": 0,
                "timeout_seconds": 30, "stall_seconds": -1,
                "retry": 0, "enabled": True,
            }],
        }, headers=auth_headers)
        assert resp.status_code == 422


# ── 0=不限 按步骤开门（schema timeout_seconds minimum 1→0）────────────
# 停滞判据落地（#115 阶段 1/2）+ 脚本打戳（v2.3.1 / flash v1.1.0）后，
# 已接打戳 + 配了 stall_seconds 的步骤可以配 timeout_seconds=0（不限）。
# schema 只放宽 step 级；lifecycle 级（plan 总时长）保持 minimum 1。


def _step_with_timeout(t):
    s = _step()
    s.timeout_seconds = t
    return s


class TestStepTimeoutZero:
    def test_step_timeout_zero_passes_validation(self):
        lc = build_lifecycle_from_steps(_plan(), [_step_with_timeout(0)], _META)
        assert lc["init"][0]["timeout_seconds"] == 0
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert ok, errors

    def test_lifecycle_timeout_zero_still_rejected(self):
        """plan 级总时长 0 没有意义——仍 minimum 1。"""
        lc = build_lifecycle_from_steps(_plan(), [_step_with_timeout(30)], _META)
        lc["timeout_seconds"] = 0
        ok, errors = validate_pipeline_def({"lifecycle": lc})
        assert not ok
        assert any("timeout_seconds" in e for e in errors)
