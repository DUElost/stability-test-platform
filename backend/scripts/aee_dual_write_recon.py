#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""M1 AEE 双写对账工具(只读)。

用途:验证 reconciler(新)与 patrol scan_aee(旧)双写期,reconciler 的 AEE
捕获是否漏报 / 重复。对账分两侧:
  - signal 侧:本脚本查 DB(job_log_signal),统计 reconciler emit 的去重 nfs_path;
  - NFS 物理侧:在 Agent 主机(如 host 7)上执行本脚本打印的 find 命令,数实际 crash 目录。
两侧相除得漏报率,目标 < 5%(§4.4 / §11.3 C / T1-4)。

只读:仅 SELECT plan_run / job_instance / job_log_signal,不写任何表。

用法:
    # 列出最近 PlanRun,帮你找 plan_run_id(关注 name 含 aee、status=RUNNING、aee_sig>0)
    python backend/scripts/aee_dual_write_recon.py --list

    # 对账指定 PlanRun 的 reconciler signal 侧
    python backend/scripts/aee_dual_write_recon.py --plan-run 123

环境:
    DATABASE_URL  默认 postgresql://stability:stability@localhost:5432/stability
                  (自动去掉 SQLAlchemy 的 +psycopg 后缀直连)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

try:
    import psycopg
except ImportError:
    print("需要 psycopg(v3):pip install 'psycopg[binary]'", file=sys.stderr)
    raise SystemExit(1)


def _dsn() -> str:
    raw = os.getenv(
        "DATABASE_URL",
        "postgresql://stability:stability@localhost:5432/stability",
    )
    return raw.replace("+psycopg", "", 1)


def _connect():
    try:
        return psycopg.connect(_dsn(), connect_timeout=5)
    except Exception as exc:  # noqa: BLE001 - 顶层友好提示
        print(f"DB 连接失败: {exc}", file=sys.stderr)
        raise SystemExit(2)


def _snapshot_has_scan_aee(snapshot) -> bool:
    """plan_snapshot 的 patrol 阶段是否仍含 legacy scan_aee(双写旧侧)。

    兼容两种快照结构:
      - 扁平 steps(plan_dispatcher 实际写入,ADR-0020):
        {"plan": {...}, "steps": [{"stage": "patrol", "script_name": "scan_aee", ...}]}
      - lifecycle(pipeline_def 形态,容错保留):
        {"lifecycle": {"patrol": {"steps": [{"action": "script:scan_aee"}]}}}
    """
    try:
        if not snapshot:
            return False
        snap = snapshot if isinstance(snapshot, dict) else json.loads(snapshot)
        # 形态 1:扁平 steps[](每个 step 带 stage + script_name/step_key)
        for st in snap.get("steps") or []:
            if str((st or {}).get("stage", "")).lower() != "patrol":
                continue
            ident = "{} {} {}".format(
                (st or {}).get("script_name", ""),
                (st or {}).get("step_key", ""),
                (st or {}).get("action", ""),
            )
            if "scan_aee" in ident:
                return True
        # 形态 2:lifecycle.patrol.steps[].action(容错)
        patrol = ((snap.get("lifecycle") or {}).get("patrol")) or {}
        steps = patrol.get("steps") if isinstance(patrol, dict) else patrol
        for st in steps or []:
            if "scan_aee" in str((st or {}).get("action", "")):
                return True
    except Exception:  # noqa: BLE001 - 容错:解析失败按无
        pass
    return False


def cmd_list(conn) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        SELECT pr.id, p.name, pr.status, pr.started_at,
               count(DISTINCT j.id) AS jobs,
               count(s.id) FILTER (
                   WHERE s.category IN ('AEE','VENDOR_AEE','ANR')
               ) AS aee_sig
        FROM plan_run pr
        JOIN plan p ON p.id = pr.plan_id
        LEFT JOIN job_instance j ON j.plan_run_id = pr.id
        LEFT JOIN job_log_signal s ON s.job_id = j.id
        GROUP BY pr.id, p.name, pr.status, pr.started_at
        ORDER BY pr.id DESC
        LIMIT 20
        """
    )
    rows = cur.fetchall()
    print("最近 20 个 PlanRun(关注 name 含 aee/patrol、status=RUNNING、aee_sig>0):")
    print(f"{'run_id':>7} {'status':<16} {'jobs':>5} {'aee_sig':>8}  started              name")
    for rid, name, status, started, jobs, sig in rows:
        print(f"{rid:>7} {status:<16} {jobs:>5} {sig:>8}  {str(started)[:19]}  {name}")


def cmd_recon(conn, plan_run_id: int) -> None:
    cur = conn.cursor()
    cur.execute("SELECT status, plan_snapshot FROM plan_run WHERE id = %s", (plan_run_id,))
    row = cur.fetchone()
    if row is None:
        print(f"PlanRun {plan_run_id} 不存在", file=sys.stderr)
        raise SystemExit(3)
    status, snapshot = row
    legacy = _snapshot_has_scan_aee(snapshot)

    cur.execute(
        """
        SELECT s.category, s.source, s.extra->>'pull_source', s.device_serial,
               s.extra->>'nfs_path', s.extra->>'event_type', s.extra->>'schema_version'
        FROM job_log_signal s
        JOIN job_instance j ON j.id = s.job_id
        WHERE j.plan_run_id = %s AND s.category IN ('AEE','VENDOR_AEE','ANR')
        """,
        (plan_run_id,),
    )
    sigs = cur.fetchall()

    print("=" * 68)
    print(f"PlanRun {plan_run_id}  status={status}")
    print(f"plan_snapshot.patrol 含 legacy scan_aee(双写旧侧): {legacy}")
    print(f"AEE/VENDOR_AEE/ANR signal 总数: {len(sigs)}")
    print("=" * 68)

    if not sigs:
        print("无 AEE 类 signal。排查顺序:")
        print("  1. Agent 日志是否有 'aee_reconciler_env enabled=true' 与 'aee_reconciler_active'")
        print("  2. STP_WATCHER_AEE_RECONCILE_ENABLED=true 且 host 命中 _RECONCILE_HOSTS 白名单")
        print("  3. 真机是否已产生 AEE crash(db_history 是否新增行)")
        print("  4. reconciler 基线 180s,首轮或 burst 后才 emit;可调小 INTERVAL 加速验证")
        return

    by_source = Counter((s[2] or s[1] or "(none)") for s in sigs)
    by_cat = Counter(s[0] for s in sigs)
    by_serial: dict[str, Counter] = defaultdict(Counter)
    nfs_paths_recon: dict[str, set] = defaultdict(set)
    missing_schema = 0
    for cat, _source, pull, serial, nfs, _et, schema_ver in sigs:
        by_serial[serial][cat] += 1
        if pull == "reconciler" and nfs:
            nfs_paths_recon[serial].add(nfs)
        if schema_ver is None:
            missing_schema += 1

    print("\n-- by pull_source --")
    for k, v in by_source.most_common():
        print(f"  {k:<16} {v}")
    print("-- by category --")
    for k, v in by_cat.most_common():
        print(f"  {k:<16} {v}")
    if missing_schema:
        print(f"\n[提示] {missing_schema} 条 signal 无 extra.schema_version "
              f"(legacy inotifyd 路径或旧数据;reconciler emit 应为 1)")

    print("\n-- by serial(reconciler 去重 nfs_path = crash 事件数)--")
    total_recon_dirs = 0
    for serial in sorted(by_serial):
        n = len(nfs_paths_recon[serial])
        total_recon_dirs += n
        print(f"  {serial}: signals={dict(by_serial[serial])} reconciler_nfs_dirs={n}")

    print("\n" + "=" * 68)
    print("NFS 物理侧对账 —— 在 Agent 主机(host 7)上执行,逐 serial 比对:")
    print("  (设 $STP_AEE_NFS_ROOT 为 sonic 根;数含 *.dbg 的去重 crash 目录)")
    for serial in sorted(nfs_paths_recon):
        print(
            f"  find \"$STP_AEE_NFS_ROOT\"/*/{serial}/aee_exp "
            f"\"$STP_AEE_NFS_ROOT\"/*/{serial}/vendor_aee_exp -name '*.dbg' 2>/dev/null "
            f"| sed 's#/[^/]*$##' | sort -u | wc -l   # 期望 ≈ {len(nfs_paths_recon[serial])}"
        )
    print("=" * 68)
    print(f"\n判定:reconciler 去重 nfs_dir 合计 = {total_recon_dirs}")
    print("  漏报率 = (NFS_crash_dirs - reconciler_nfs_dirs) / NFS_crash_dirs，目标 < 5%")
    print("  无重复:本脚本已按 set 去重 nfs_path;若同一 nfs_path 出现多条 signal,")
    print("          DB 层 (job_id, seq_no) 幂等键应已拦截,出现即需排查 emit 重复。")


def main() -> None:
    ap = argparse.ArgumentParser(description="M1 AEE 双写对账(只读)")
    ap.add_argument("--plan-run", type=int, help="对账指定 PlanRun id")
    ap.add_argument("--list", action="store_true", help="列出最近 20 个 PlanRun")
    args = ap.parse_args()

    conn = _connect()
    try:
        if args.plan_run:
            cmd_recon(conn, args.plan_run)
        else:
            cmd_list(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
