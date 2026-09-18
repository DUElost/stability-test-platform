"""Tests for results API routes"""

import json
from datetime import datetime, timedelta, timezone

from backend.models.host import Device
from backend.models.job import JobInstance, JobLogSignal, StepTrace
from backend.models.plan import Plan
from backend.models.plan_run import PlanRun


class TestResultsSummary:
    def test_summary_empty(self, client, auth_headers):
        response = client.get("/api/v1/results/summary", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert "runs_by_status" in data
        assert "test_type_stats" in data
        assert "risk_distribution" in data
        assert "recent_runs" in data
        assert data["runs_by_status"]["total"] >= 0

    def test_summary_with_limit(self, client, auth_headers):
        response = client.get("/api/v1/results/summary", params={"limit": 5}, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()
        assert len(data["recent_runs"]) <= 5

    def test_summary_aggregates_from_job_instance_chain(
        self, client, auth_headers, db_session, sample_device, monkeypatch,
    ):
        # #2365：覆盖率指标随每次 /summary 计算刷新——用记录器钉住「指标 == 响应」，
        # 免得观测面与界面各说各话。
        class _GaugeRecorder:
            def __init__(self):
                self.values: dict = {}
                self._level = ""

            def labels(self, *, level):
                self._level = level
                return self

            def set(self, value):
                self.values[self._level] = value

        recorder = _GaugeRecorder()
        monkeypatch.setattr("backend.api.routes.results.risk_jobs_by_level", recorder)

        now = datetime.now(timezone.utc)
        baseline = client.get("/api/v1/results/summary", headers=auth_headers).json()
        baseline_counts = dict(baseline["risk_distribution"])
        suffix = now.strftime("%Y%m%d%H%M%S%f")
        smoke_type = f"Smoke-{suffix}"
        stress_type = f"Stress-{suffix}"

        plan_smoke = Plan(
            name=smoke_type,
            description="",
            failure_threshold=0.05,
                    )
        plan_stress = Plan(
            name=stress_type,
            description="",
            failure_threshold=0.05,
                    )
        db_session.add_all([plan_smoke, plan_stress])
        db_session.flush()

        plan_run = PlanRun(
            plan_id=plan_smoke.id,
            status="RUNNING",
            failure_threshold=0.05,
            plan_snapshot={"name": plan_smoke.name, "plan_id": plan_smoke.id},
            run_type="MANUAL",
            triggered_by="pytest",
        )
        db_session.add(plan_run)
        db_session.flush()

        pipeline_def = {"lifecycle": {"init": [], "teardown": []}}
        devices = [sample_device]
        for index in range(3):
            device = Device(
                serial=f"RESULT-{suffix}-{index}",
                host_id=sample_device.host_id,
                status="ONLINE",
            )
            db_session.add(device)
            devices.append(device)
        db_session.flush()

        jobs = [
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_smoke.id,
                device_id=devices[0].id,
                host_id=sample_device.host_id,
                status="COMPLETED",
                status_reason=None,
                pipeline_def=pipeline_def,
                started_at=now - timedelta(minutes=12),
                ended_at=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=12),
                updated_at=now - timedelta(minutes=10),
            ),
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_stress.id,
                device_id=devices[1].id,
                host_id=sample_device.host_id,
                status="FAILED",
                status_reason="tool failed",
                pipeline_def=pipeline_def,
                started_at=now - timedelta(minutes=9),
                ended_at=now - timedelta(minutes=8),
                created_at=now - timedelta(minutes=9),
                updated_at=now - timedelta(minutes=8),
            ),
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_stress.id,
                device_id=devices[2].id,
                host_id=sample_device.host_id,
                status="ABORTED",
                status_reason="manual stop",
                pipeline_def=pipeline_def,
                started_at=now - timedelta(minutes=7),
                ended_at=now - timedelta(minutes=7),
                created_at=now - timedelta(minutes=7),
                updated_at=now - timedelta(minutes=7),
            ),
            JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan_smoke.id,
                device_id=devices[3].id,
                host_id=sample_device.host_id,
                status="RUNNING",
                status_reason=None,
                pipeline_def=pipeline_def,
                started_at=now - timedelta(minutes=3),
                ended_at=None,
                created_at=now - timedelta(minutes=3),
                updated_at=now - timedelta(minutes=2),
            ),
        ]
        db_session.add_all(jobs)
        db_session.flush()

        # #2365：风险级别由**活链**（log_observation：DLE 权威 + 未链接信号）判定。
        # 这里同时放两类行，用来钉住判据没被换回去：
        #   jobs[0] 有 S 级信号 + 一条 `risk=LOW` 快照诱饵 → 必须是 high；
        #   jobs[1] 有 B 级信号 → low；
        #   jobs[2] 只有 `risk=MEDIUM` 快照诱饵（没有任何信号）→ 必须仍是 unknown
        #           （快照里的 risk= 自 ADR-0025 起就没有生产者）。
        db_session.add_all([
            JobLogSignal(
                job_id=jobs[0].id,
                host_id=str(sample_device.host_id),
                device_serial=sample_device.serial,
                seq_no=0,
                category="ANR",
                source="inotifyd",
                path_on_device="/data/anr/summary-0.txt",
                detected_at=now - timedelta(minutes=11),
                received_at=now - timedelta(minutes=11),
                extra={"event_subtype": "swt", "nfs_path": "/nfs/swt/summary-0", "schema_version": 2},
            ),
            JobLogSignal(
                job_id=jobs[1].id,
                host_id=str(sample_device.host_id),
                device_serial=sample_device.serial,
                seq_no=0,
                category="AEE",
                source="inotifyd",
                path_on_device="/data/aee/summary-1.txt",
                detected_at=now - timedelta(minutes=8),
                received_at=now - timedelta(minutes=8),
                extra={"event_subtype": "misc", "nfs_path": "/nfs/misc/summary-1", "schema_version": 2},
            ),
        ])
        db_session.add_all([
            StepTrace(
                job_id=jobs[0].id,
                step_id="__job__",
                stage="post_process",
                status="COMPLETED",
                event_type="RUN_COMPLETE",
                output=json.dumps({"update": {"log_summary": "risk=LOW;restarts=2"}}),
                error_message=None,
                original_ts=now - timedelta(minutes=10),
                created_at=now - timedelta(minutes=10),
            ),
            StepTrace(
                job_id=jobs[2].id,
                step_id="__job__",
                stage="post_process",
                status="COMPLETED",
                event_type="RUN_COMPLETE",
                output=json.dumps({"update": {"log_summary": "risk=MEDIUM;restarts=1"}}),
                error_message=None,
                original_ts=now - timedelta(minutes=6),
                created_at=now - timedelta(minutes=6),
            ),
        ])
        db_session.commit()

        response = client.get("/api/v1/results/summary", params={"limit": 3}, headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert data["runs_by_status"]["finished"] == baseline["runs_by_status"]["finished"] + 1
        assert data["runs_by_status"]["failed"] == baseline["runs_by_status"]["failed"] + 1
        assert data["runs_by_status"]["canceled"] == baseline["runs_by_status"]["canceled"] + 1
        assert data["runs_by_status"]["running"] == baseline["runs_by_status"]["running"] + 1
        assert data["runs_by_status"]["total"] == baseline["runs_by_status"]["total"] + 4

        type_stats = {row["type"]: row for row in data["test_type_stats"]}
        assert type_stats[smoke_type]["total"] == 2
        assert type_stats[smoke_type]["finished"] == 1
        assert type_stats[smoke_type]["failed"] == 0
        assert type_stats[stress_type]["total"] == 2
        assert type_stats[stress_type]["finished"] == 0
        assert type_stats[stress_type]["failed"] == 1

        # #2365：判据 = 活链；#2494/ADR-0045 D2：桶名就是级别本身（不再翻成
        # high/medium/low），无信号 = unknown。jobs[0] 的 `risk=LOW` 诱饵（快照）
        # 不得覆盖 S 级信号。
        assert data["risk_distribution"]["s"] == baseline["risk_distribution"]["s"] + 1
        assert data["risk_distribution"]["a"] == baseline["risk_distribution"]["a"]
        assert data["risk_distribution"]["b"] == baseline["risk_distribution"]["b"] + 1
        assert data["risk_distribution"]["unknown"] == baseline["risk_distribution"]["unknown"] + 2

        assert len(data["recent_runs"]) == 3
        assert data["recent_runs"][0]["run_id"] == jobs[3].id
        assert data["recent_runs"][0]["status"] == "RUNNING"
        assert smoke_type in data["recent_runs"][0]["task_name"]
        assert data["recent_runs"][1]["run_id"] == jobs[2].id
        assert data["recent_runs"][1]["status"] == "CANCELED"
        # recent_runs 同样走活链，且**出的是级别本身**（ADR-0045 D2 后这里没有翻译）：
        # jobs[3] 无信号 → UNKNOWN、jobs[2] 只有快照诱饵 → 仍 UNKNOWN、jobs[1] 有 B 级信号 → B
        assert data["recent_runs"][0]["risk_level"] == "UNKNOWN"
        assert data["recent_runs"][1]["risk_level"] == "UNKNOWN"
        assert data["recent_runs"][2]["risk_level"] == "B"

        # 指标面：四个桶都与响应一致（unknown 含无信号 job 与未完成 job）
        assert recorder.values == {
            "s": baseline_counts["s"] + 1,
            "a": baseline_counts["a"],
            "b": baseline_counts["b"] + 1,
            "unknown": baseline_counts["unknown"] + 2,
        }


class TestRiskTrend:
    """ADR-0029 P2：项目级风险趋势（按天 S/A/B，run 级 DLE 权威聚合）。"""

    def test_trend_empty(self, client, auth_headers):
        resp = client.get("/api/v1/results/risk-trend", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["buckets"] == []
        assert data["days"] == 30

    def test_trend_buckets_by_project(
        self, client, auth_headers, db_session, sample_device,
    ):
        from backend.models.job import JobLogSignal
        from backend.models.project import TestProject

        now = datetime.now(timezone.utc)
        project = TestProject(project_key="TREND-A", display_name="trend",
                              source="USER")
        db_session.add(project)
        db_session.flush()
        plan = Plan(name="trend-plan", failure_threshold=0.05, project_id=project.id)
        db_session.add(plan)
        db_session.flush()
        run = PlanRun(
            plan_id=plan.id,
            project_id=project.id,
            status="SUCCESS",
            failure_threshold=0.05,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
            started_at=now - timedelta(days=1),
        )
        db_session.add(run)
        db_session.flush()
        job = JobInstance(
            plan_run_id=run.id,
            plan_id=plan.id,
            device_id=sample_device.id,
            host_id=sample_device.host_id,
            status="COMPLETED",
            pipeline_def={"lifecycle": {"init": [], "teardown": []}},
            started_at=now - timedelta(days=1),
            ended_at=now - timedelta(days=1),
            created_at=now - timedelta(days=1),
            updated_at=now - timedelta(days=1),
        )
        db_session.add(job)
        db_session.flush()
        # S 级：category 在白名单（ANR）+ event_subtype=swt（命中 swt 规则）
        # + nfs_path 非空（unlinked 计数按 nfs_path DISTINCT）
        db_session.add(JobLogSignal(
            job_id=job.id,
            host_id=str(sample_device.host_id),
            device_serial=sample_device.serial,
            seq_no=0,
            category="ANR",
            source="inotifyd",
            path_on_device="/data/anr/1.txt",
            detected_at=now - timedelta(days=1),
            received_at=now - timedelta(days=1),
            extra={
                "event_subtype": "swt",
                "nfs_path": "/nfs/swt/1",
                "schema_version": 2,
            },
        ))
        db_session.commit()

        resp = client.get(
            "/api/v1/results/risk-trend?project_key=TREND-A",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["project_key"] == "TREND-A"
        assert len(data["buckets"]) == 1
        bucket = data["buckets"][0]
        assert bucket["date"] == (now - timedelta(days=1)).date().isoformat()
        assert bucket["S"] == 1
        assert bucket["runs"] == 1

        # 其他项目不串扰
        other = client.get(
            "/api/v1/results/risk-trend?project_key=NOPE",
            headers=auth_headers,
        )
        assert other.status_code == 404


class TestResultsSummaryRiskGauge:
    """#2365 重开后的两处收口：**无 job 必须把四桶写回 0**；**带 `project_key` 的请求
    不得改写全局 gauge**。

    沿用同文件 #2365 既有用例的做法——用记录器替掉 `risk_jobs_by_level`，钉的是
    「这次调用到底写了什么」，而不是注册表终值（避免用例之间互相污染）。
    """

    @staticmethod
    def _recorder(monkeypatch):
        class _GaugeRecorder:
            def __init__(self):
                self.values: dict = {}
                self._level = ""

            def labels(self, *, level):
                self._level = level
                return self

            def set(self, value):
                self.values[self._level] = value

        recorder = _GaugeRecorder()
        monkeypatch.setattr(
            "backend.api.routes.results.risk_jobs_by_level", recorder,
        )
        return recorder

    @staticmethod
    def _seed_jobs(db_session, sample_device, *, tag, count=2, project_id=None):
        from uuid import uuid4

        from backend.models.project import TestProject  # noqa: F401  可读性：明确依赖

        suffix = uuid4().hex[:8]
        plan = Plan(
            name=f"gauge-{tag}-{suffix}", description="", failure_threshold=0.05,
        )
        db_session.add(plan)
        db_session.flush()
        plan_run = PlanRun(
            plan_id=plan.id,
            project_id=project_id,
            status="SUCCESS",
            failure_threshold=0.05,
            plan_snapshot={"name": plan.name, "plan_id": plan.id},
            run_type="MANUAL",
            triggered_by="pytest",
            started_at=datetime.now(timezone.utc) - timedelta(minutes=20),
            ended_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        )
        db_session.add(plan_run)
        db_session.flush()
        jobs = []
        for index in range(count):
            device = Device(
                serial=f"GAUGE-{tag}-{suffix}-{index}",
                host_id=sample_device.host_id,
                status="ONLINE",
            )
            db_session.add(device)
            db_session.flush()
            job = JobInstance(
                plan_run_id=plan_run.id,
                plan_id=plan.id,
                device_id=device.id,
                host_id=device.host_id,
                status="COMPLETED",
                status_reason=None,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=datetime.now(timezone.utc) - timedelta(minutes=15),
                ended_at=datetime.now(timezone.utc) - timedelta(minutes=12),
            )
            db_session.add(job)
            jobs.append(job)
        db_session.commit()
        return jobs

    def test_gauge_returns_to_zero_when_all_jobs_are_gone(
        self, client, auth_headers, db_session, sample_device, monkeypatch,
    ):
        """验收 ①：job 全清空后再调用，四桶必须全部回落 0。

        旧实现把四个 `.set()` 全写在 `if total_jobs > 0:` 里 → 清空后整段跳过，
        gauge 停在上一轮的非零值，读起来像「还有风险分布」——与本单「让无判定依据
        与判据坏了可区分」的目标正好相反。
        """
        recorder = self._recorder(monkeypatch)
        self._seed_jobs(db_session, sample_device, tag="zero")

        client.get("/api/v1/results/summary", headers=auth_headers)
        first = dict(recorder.values)
        assert set(first) == {"s", "a", "b", "unknown"}
        assert first["unknown"] >= 1, "用例前提：先要有一次非零写入，否则「归零」无从谈起"

        db_session.query(JobInstance).delete(synchronize_session=False)
        db_session.commit()
        recorder.values.clear()

        response = client.get("/api/v1/results/summary", headers=auth_headers)
        assert response.status_code == 200
        # RiskDistribution 就是四桶本身（无 total 字段）
        assert sum(response.json()["risk_distribution"].values()) == 0
        assert recorder.values == {"s": 0, "a": 0, "b": 0, "unknown": 0}, (
            "无 job 时也必须写一次（全 0）——否则 gauge 停在上一轮非零值"
        )

    def test_project_scoped_call_does_not_rewrite_global_gauge(
        self, client, auth_headers, db_session, sample_device, monkeypatch,
    ):
        """验收 ②：带 `project_key` 的调用不得改写全局 gauge（该指标只有 `level` 标签）。"""
        from backend.models.project import TestProject

        recorder = self._recorder(monkeypatch)
        now = datetime.now(timezone.utc)
        project = TestProject(
            project_key=f"GAUGE{now.strftime('%H%M%S%f')}", display_name="gauge-scope",
        )
        db_session.add(project)
        db_session.commit()

        self._seed_jobs(db_session, sample_device, tag="global", count=3)
        self._seed_jobs(
            db_session, sample_device, tag="scoped", count=1, project_id=project.id,
        )

        client.get("/api/v1/results/summary", headers=auth_headers)
        global_snapshot = dict(recorder.values)
        assert global_snapshot["unknown"] == 4, f"全局应有 4 个 job：{global_snapshot}"

        recorder.values.clear()
        scoped = client.get(
            "/api/v1/results/summary",
            params={"project_key": project.project_key},
            headers=auth_headers,
        )
        assert scoped.status_code == 200
        assert sum(scoped.json()["risk_distribution"].values()) == 1, "作用域响应仍按作用域算"
        # 关键：作用域这一次调用**不写**全局 gauge（旧实现会把 1 覆盖掉 4）
        assert recorder.values == {}, (
            f"带 project_key 的请求改写了全局 gauge：{recorder.values}"
        )


class TestRiskVocabularyParity:
    """#2494 判据 1 的行为面：同一 job 在**四个对外面必须是同一个级别**。

    四面 = `/results/summary` 的 `recent_runs[].risk_level`、`risk_distribution` 桶、
    `/results/risk-trend` 当日桶、报告 DTO 的 `risk_summary.risk_level`。
    收敛前它们分别是 `HIGH` / `high` / `S` / `S`（趋势第四态还另起 `NONE`）——
    徽标按另一套键查表就恒显「未知」，而同屏 S/A/B 计数正常（#2418 的同型缺陷）。
    离线的词表门禁在 `tests/test_risk_vocabulary_drift.py`，这里钉的是"跑起来真一致"。
    """

    def test_same_level_across_summary_trend_and_report(
        self, client, auth_headers, db_session, sample_device
    ):
        now = datetime.now(timezone.utc)
        plan = Plan(name="parity-s", description="", failure_threshold=0.05)
        db_session.add(plan)
        db_session.flush()

        def mk_run_job(tag: str) -> JobInstance:
            """一个 run 配一个 job（趋势按 **run** 汇总，所以两个 job 必须分属两个 run，
            否则"零事件那一档"会被同 run 的 S 级吃掉，测不到 D4）。"""
            run = PlanRun(
                plan_id=plan.id,
                status="SUCCESS",
                failure_threshold=0.05,
                plan_snapshot={"name": plan.name, "plan_id": plan.id},
                run_type="MANUAL",
                triggered_by="pytest",
                started_at=now - timedelta(minutes=30),
                ended_at=now - timedelta(minutes=20),
            )
            db_session.add(run)
            db_session.flush()
            job = JobInstance(
                plan_run_id=run.id,
                plan_id=plan.id,
                device_id=sample_device.id,
                host_id=sample_device.host_id,
                status="COMPLETED",
                status_reason=None,
                pipeline_def={"lifecycle": {"init": [], "teardown": []}},
                started_at=now - timedelta(minutes=25),
                ended_at=now - timedelta(minutes=21),
            )
            db_session.add(job)
            db_session.flush()
            return job

        s_job = mk_run_job("s")
        bare_job = mk_run_job("bare")
        # S 级：ANR + swt 子类型走活链（log_observation）判定，不读任何快照文本
        db_session.add(JobLogSignal(
            job_id=s_job.id,
            host_id=str(sample_device.host_id),
            device_serial=sample_device.serial,
            seq_no=0,
            category="ANR",
            source="inotifyd",
            path_on_device="/data/anr/parity-0.txt",
            detected_at=now - timedelta(minutes=24),
            received_at=now - timedelta(minutes=24),
            extra={
                "event_subtype": "swt",
                "nfs_path": "/nfs/swt/parity-0",
                "schema_version": 2,
            },
        ))
        # bare_job 不放任何信号：它是 D4 的那一半——"没有采到异常"必须落在 UNKNOWN，
        # 不许被压成 B/低（收敛前正是这种 job 在列表里显示 LOW）。
        db_session.commit()

        summary = client.get(
            "/api/v1/results/summary", params={"limit": 20}, headers=auth_headers,
        ).json()
        rows = {r["run_id"]: r for r in summary["recent_runs"]}
        assert rows[s_job.id]["risk_level"] == "S", (
            f"列表侧对外值域必须是级别本身，不得再翻成 HIGH：{rows[s_job.id]['risk_level']}"
        )
        assert rows[bare_job.id]["risk_level"] == "UNKNOWN", (
            f"零事件的 job 必须是 UNKNOWN，不得压成 B/LOW：{rows[bare_job.id]['risk_level']}"
        )
        # 判据 1 的字面要求：整张列表值域 ⊆ {S,A,B,UNKNOWN}。两个 job（有判据 / 无判据）
        # 都在集合里，这条才有判别力——只有单条时它会被上面的逐行断言先吃掉。
        assert {r["risk_level"] for r in summary["recent_runs"]} == {"S", "UNKNOWN"}, (
            "列表侧值域超出对外词表："
            f"{sorted({r['risk_level'] for r in summary['recent_runs']})}"
        )
        # 桶名 = 级别（D2），且 UNKNOWN 与 b 分开（D4：零事件不是低风险）
        dist = summary["risk_distribution"]
        assert set(dist) == {"s", "a", "b", "unknown"}
        assert dist["s"] >= 1
        assert dist["unknown"] >= 1, "无判定依据的 job 没进 unknown 桶（D4 的覆盖率观测对象）"
        assert dist["b"] == 0, "零事件被算进了 b（D4 禁止：没采到异常 ≠ 低风险）"

        # 注意：同一文件里三种形状并存——risk-trend 与 /results/summary 走**裸模型**
        # （`response_model=RiskTrendOut` 等），`/runs/{id}/report` 与 `/report/cached`
        # 走 `{data, error}` **信封**（#2420 第 3 项：live 口径于 #2543 补上信封，与
        # cached 对齐；前端 `utils/api/runs.ts` 两条同为 `unwrapApiResponse`）。
        # 对拍跨形状取值时必须**先按各自形状解到位**，否则比较的是 `None == 'S'`。
        trend = client.get(
            "/api/v1/results/risk-trend", params={"days": 30}, headers=auth_headers,
        ).json()
        day = trend["buckets"][0]
        assert "NONE" not in day, f"趋势第四态已并入 UNKNOWN，不得再长回 NONE：{day}"
        assert day["S"] >= 1 and day["B"] == 0
        assert day["UNKNOWN"] >= 1, "趋势里零事件没进 UNKNOWN（第四态并词的落点，D2/D4）"

        report_body = client.get(
            f"/api/v1/runs/{s_job.id}/report", headers=auth_headers,
        ).json()
        # 先钉形状再解包：信封一旦改名，`report_body["data"]` 要当场炸，而不是
        # 被 `or {}` 这类容错读法吞成 `None == None` 的**永真**（#2568 的口径：
        # 「绿但没测到东西」比红更糟）。
        assert set(report_body) == {"data", "error"}, (
            f"报告 live 口径必须是 {{data, error}} 信封（#2420/#2543）：{sorted(report_body)}"
        )
        assert report_body["error"] is None
        report_data = report_body["data"]
        # 两侧各自钉死再比相等：只写 `==` 时，两侧同时读空也是「通过」。
        assert report_data["risk_summary"]["risk_level"] == "S", (
            "报告侧没真读到 S——多半是解包路径不对（本行的 KeyError 就是判别力）"
        )
        assert report_data["risk_summary"]["risk_level"] == rows[s_job.id]["risk_level"], (
            "报告 DTO 与列表必须同词表：徽标与同屏计数不能各说一套（#2494）"
        )
