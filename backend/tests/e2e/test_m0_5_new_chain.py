import json
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
from sqlalchemy import bindparam, create_engine, text


SERVER_URL = os.getenv("STP_SERVER_URL", "http://127.0.0.1:8000")
DATABASE_URL = os.getenv(
    "STP_DATABASE_URL",
    "postgresql+psycopg://stability:stability@localhost:5432/stability",
)
AGENT_SECRET = os.getenv("STP_AGENT_SECRET") or os.getenv("AGENT_SECRET", "")

API_TIMEOUT = float(os.getenv("STP_API_TIMEOUT", "10"))
POLL_INTERVAL = float(os.getenv("STP_POLL_INTERVAL", "2"))
AGG_TIMEOUT = float(os.getenv("STP_AGG_TIMEOUT", "30"))


class ApiError(AssertionError):
    pass


def _headers() -> dict:
    if AGENT_SECRET:
        return {"X-Agent-Secret": AGENT_SECRET}
    return {}


def _unwrap_api(resp: httpx.Response):
    resp.raise_for_status()
    body = resp.json()
    if isinstance(body, dict) and body.get("error"):
        raise ApiError(f"API error: {body['error']}")
    return body.get("data", body)


def _wait_until(desc: str, timeout_s: float, predicate) -> None:
    start = time.time()
    while time.time() - start < timeout_s:
        if predicate():
            return
        time.sleep(POLL_INTERVAL)
    raise AssertionError(f"timeout waiting for {desc} (>{timeout_s}s)")


def test_m0_5_new_chain_e2e():
    # 生成本次测试唯一标识
    run_tag = uuid.uuid4().hex[:8]
    host_id = f"host-e2e-{run_tag}"
    device_serial = f"serial-e2e-{run_tag}"
    tool_name = f"e2e-tool-{run_tag}"
    tool_version = "v0.0.1"

    engine = create_engine(DATABASE_URL, future=True)

    tool_id = None
    wf_id = None
    run_id = None
    device_id = None
    job_ids = []

    with httpx.Client(base_url=SERVER_URL, timeout=API_TIMEOUT) as client:
        # 0) 健康检查
        resp = client.get("/health")
        resp.raise_for_status()

        try:
            # 1) Tool 注册
            tool_payload = {
                "name": tool_name,
                "version": tool_version,
                "script_path": "C:/tools/e2e_tool.py",
                "script_class": "E2ETool",
                "param_schema": {"type": "object"},
                "description": "e2e tool",
                "is_active": True,
            }
            tool = _unwrap_api(client.post("/api/v1/tools", json=tool_payload))
            tool_id = tool["id"]

            # 2) Host 心跳注册
            hb_payload = {
                "host_id": host_id,
                "tool_catalog_version": "e2e",
                "load": {"running_jobs": 0},
            }
            _unwrap_api(client.post("/api/v1/agent/heartbeat", json=hb_payload, headers=_headers()))

            # 3) Device fixture 写入新表 device
            with engine.begin() as conn:
                host_row = conn.execute(
                    text("SELECT id FROM host WHERE id = :id"),
                    {"id": host_id},
                ).fetchone()
                assert host_row is not None, "host not created by heartbeat"

                device_id = conn.execute(
                    text(
                        """
                        INSERT INTO device (serial, host_id, model, platform, tags, status, created_at)
                        VALUES (:serial, :host_id, :model, :platform, :tags::jsonb, :status, NOW())
                        RETURNING id
                        """
                    ),
                    {
                        "serial": device_serial,
                        "host_id": host_id,
                        "model": "e2e-model",
                        "platform": "MTK",
                        "tags": json.dumps({"platform": "MTK", "batch": "e2e"}),
                        "status": "OFFLINE",
                    },
                ).scalar_one()

            # 4) 创建 WorkflowDefinition
            wf_payload = {
                "name": f"e2e-workflow-{run_tag}",
                "description": "e2e workflow",
                "failure_threshold": 0.05,
                "task_templates": [
                    {
                        "name": "e2e-template",
                        "pipeline_def": {
                            "stages": {
                                "execute": [
                                    {
                                        "step_id": "e2e_step",
                                        "action": f"tool:{tool_id}",
                                        "version": tool_version,
                                        "params": {"duration": 10},
                                        "timeout_seconds": 60,
                                        "retry": 0,
                                    }
                                ]
                            }
                        },
                        "platform_filter": {"platform": "MTK"},
                        "sort_order": 0,
                    }
                ],
            }
            wf = _unwrap_api(client.post("/api/v1/workflows", json=wf_payload))
            wf_id = wf["id"]

            # 5) 触发 WorkflowRun
            run = _unwrap_api(
                client.post(f"/api/v1/workflows/{wf_id}/run", json={"device_ids": [device_id]})
            )
            run_id = run["id"]

            with engine.begin() as conn:
                rows = conn.execute(
                    text("SELECT id FROM job_instance WHERE workflow_run_id = :rid"),
                    {"rid": run_id},
                ).fetchall()
                job_ids = [r[0] for r in rows]
            assert job_ids, "job_instance 数量为 0，扇出失败"

            # 5b) Agent 认领 Job
            claimed = _unwrap_api(
                client.post(
                    "/api/v1/agent/jobs/claim",
                    json={"host_id": host_id, "capacity": 10},
                    headers=_headers(),
                )
            )
            claimed_ids = {j["id"] for j in claimed}
            assert claimed_ids.issuperset(set(job_ids)), "job 未被正确认领"

            # 6) HTTP step trace upload + complete_job (Phase 4: replaces MQ)
            msg_ts = datetime.now(timezone.utc).isoformat() + "Z"
            for job_id in job_ids:
                # 6a Upload step trace via HTTP
                trace_payload = [
                    {
                        "job_id": job_id,
                        "step_id": "e2e_step",
                        "stage": "execute",
                        "event_type": "COMPLETED",
                        "status": "COMPLETED",
                        "output": "ok",
                        "original_ts": msg_ts,
                    }
                ]
                _unwrap_api(
                    client.post(
                        "/api/v1/agent/steps",
                        json=trace_payload,
                        headers=_headers(),
                    )
                )

                # 6b Complete job via HTTP
                complete_payload = {
                    "update": {
                        "status": "FINISHED",
                        "exit_code": 0,
                    }
                }
                _unwrap_api(
                    client.post(
                        f"/api/v1/agent/jobs/{job_id}/complete",
                        json=complete_payload,
                        headers=_headers(),
                    )
                )

            # 6c step_trace 落库
            def _step_trace_ok():
                with engine.begin() as conn:
                    count = conn.execute(
                        text(
                            "SELECT count(1) FROM step_trace WHERE job_id = :jid AND step_id = :sid"
                        ),
                        {"jid": job_ids[0], "sid": "e2e_step"},
                    ).scalar_one()
                return count > 0

            _wait_until("step_trace persisted", 20, _step_trace_ok)

            # 9) 聚合状态轮询（result_summary）
            def _result_summary_ready():
                with engine.begin() as conn:
                    row = conn.execute(
                        text(
                            "SELECT status, result_summary FROM workflow_run WHERE id = :rid"
                        ),
                        {"rid": run_id},
                    ).fetchone()
                if not row:
                    return False
                status, summary = row
                # status 已完成但 result_summary 仍为空，视为缺口
                if status != "RUNNING" and summary is None:
                    return False
                return summary is not None

            _wait_until("workflow_run.result_summary populated", AGG_TIMEOUT, _result_summary_ready)

        finally:
            # 清理本次测试数据，避免污染环境
            with engine.begin() as conn:
                if job_ids:
                    conn.execute(
                        text("DELETE FROM step_trace WHERE job_id IN :ids")
                        .bindparams(bindparam("ids", expanding=True)),
                        {"ids": job_ids},
                    )
                    conn.execute(
                        text("DELETE FROM job_instance WHERE id IN :ids")
                        .bindparams(bindparam("ids", expanding=True)),
                        {"ids": job_ids},
                    )
                if run_id:
                    conn.execute(text("DELETE FROM workflow_run WHERE id = :rid"), {"rid": run_id})
                if wf_id:
                    conn.execute(
                        text("DELETE FROM task_template WHERE workflow_definition_id = :wid"),
                        {"wid": wf_id},
                    )
                    conn.execute(text("DELETE FROM workflow_definition WHERE id = :wid"), {"wid": wf_id})
                if device_id:
                    conn.execute(text("DELETE FROM device WHERE id = :did"), {"did": device_id})
                if host_id:
                    conn.execute(text("DELETE FROM host WHERE id = :hid"), {"hid": host_id})
                if tool_id:
                    conn.execute(text("DELETE FROM tool WHERE id = :tid"), {"tid": tool_id})

            engine.dispose()
