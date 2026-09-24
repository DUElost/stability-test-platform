# 容量 P0 同窗上线执行手册（控制面 unit + 告警副本 + Agent 削峰）

- **状态**：**手册（未执行）**——2026-09-24 11:3x 基于本机只读实测编写；执行者：owner
- **目的**：把 R523 之后**已合入但未生效**的 P0 改动真正落到本机生产控制面与 48 台 Agent，并按 owner 口径采集 #3244 裁决所需的真机数据
- **关联**：父单 [#2959](https://github.com/DUElost/stability-test-platform/issues/2959)；控制面 [#3241](https://github.com/DUElost/stability-test-platform/pull/3241)（PR 已合）+ [#3249](https://github.com/DUElost/stability-test-platform/pull/3249)（PR 已合）；Agent [#3251](https://github.com/DUElost/stability-test-platform/pull/3251)（PR 已合，**未分发**）；压测 [#3253](https://github.com/DUElost/stability-test-platform/pull/3253)（已合）；ADR 草案 [`ADR-0052`](../adr/ADR-0052-terminal-fact-parent-aggregation-decoupling.md)（Proposed）
- **依据**：ADR-0047 v1.1（D1/D2/D5）、ADR-0051（部署源与内容寻址）、`tools/dev/check-monitoring-assets.py`（资产对账口径）

> 本手册不是执行记录。执行时请在本文件末尾追加「执行记录」或在 #2959 落评论。

---

## 0. 现状快照（2026-09-24 11:2x–11:3x CST，只读实测）

| 面 | 现状 | 证据 |
|---|---|---|
| 控制面进程 | **09:46:49 起**，`current → /home/debian13/stp-releases/c371113`（含 #3241/#3249 代码：池 `20/20` + `pool_timeout=2s`、503 过载语义、终态舱壁） | `systemctl show … -p ExecMainStartTimestamp`、`readlink -f /home/debian13/stp-releases/current`、`grep STP_DB_POOL_SIZE current/backend/core/database.py` |
| **运行中作业** | **plan_run 534（plan 53）RUNNING，480 个 RUNNING 作业**——本窗开工前必须等它结束，或由 owner 明确决定在窗内中止 | `select status,count(*) from job_instance …` |
| unit | **无**预算门禁 `ExecStartPre`；**无** `StartLimitIntervalSec/Burst`（#2058 的保护在 Phase-1 换根时丢失）；`check-deploy-source.sh` 已按 Phase-1 形态退役 | `systemctl cat stability-backend` 与 `current/deploy/control-plane/systemd/stability-backend.service` 渲染后 diff |
| 告警副本 | `/etc/prometheus/rules/alerts-stability-platform.yml` 仍是 **9-23 10:35** 版：`slots_exhausted` 规则仍 `warning` + `for: 5m`、**无** `StabilityTerminalBulkheadRejected`；`promtool` 在 `/usr/bin/promtool` | 副本 mtime + `sudo grep`；现载 9 groups / 39 rules |
| Agent 机队 | 14h 内无 `hot_update*` 审计 → **#3242 未分发**；48/48 ONLINE、48/48 有 digest 字段 | `audit_logs`、`host` |
| 池参数覆盖 | `.env.backend` **无** `STP_DB_POOL_*` 覆盖（运行值 = 代码默认 20/20 + 2s） | `grep -cE '^STP_DB_POOL_SIZE|…' .env.backend` = 0 |
| 资产对账 | `check-monitoring-assets.py` 报 9 项 drift；**其中只有平台规则副本属本窗口**，其余 8 项（node-exporter、`stp-mem-top`、`stp-script-guard`、`stp-skill-usage` 等）为既有遗留，另单处置 | 工具 `--json` 输出 |

**关键结论**：控制面**代码已新、装置未齐**（门禁未装、告警未同步）；Agent **代码未上机**。三件事都要在窗口内一次做完，才能让 R523 组合闭环进入可验证状态。

---

## 1. 目标与判定

| # | 目标 | 判定（验收线） |
|---|---|---|
| A | 控制面 unit 补预算门禁 + 恢复 start-limit，重启实测 | `journalctl` 可见门禁输出（`app_total=80 … headroom=17`）；`ExecMainStatus=0`；`/health` 200 |
| B | Prometheus 平台副本同步 + reload | 规则索引里 `StabilityDbConnectionSlotsExhausted` 无 `for:`、`severity=critical`；`StabilityTerminalBulkheadRejected` 在场；`check-monitoring-assets.py` 该项 `match` |
| C | Agent #3242 分发（48 台）+ 摘要收敛 | 分发结果无 fail；机队 `agent_artifact_digest` 收敛到新 code digest（在跑作业的 host 允许滞后） |
| D | 真机复跑观测（真实大 run 中止） | 8 条验收线（见 §2 Step 4），**0×53300 / 0×500 / 池不越预算 / 120s 收敛** |
| E | #3244 裁决输入 | §2 Step 4 的数据表贴到 #3244；达标才把 ADR-0052 转 Accepted（阈值见其 §5） |

---

## 2. 步骤

### Step 0 — 前置（T-0）

```bash
# 0.1 记录基线（回滚锚点）
readlink -f /home/debian13/stp-releases/current | tee /tmp/release-before.txt
systemctl show stability-backend -p ExecMainStartTimestamp -p MainPID | tee /tmp/backend-before.txt

# 0.2 备份两个被改对象
sudo cp /etc/systemd/system/stability-backend.service{,.bak-20260924}
sudo cp /etc/prometheus/rules/alerts-stability-platform.yml{,.bak-20260924}

# 0.3 确认无在跑大 run（当前有 480 RUNNING，先等它终态或由 owner 裁决）
psql "$DATABASE_URL" -c "select status, count(*) from job_instance where status in ('RUNNING','PENDING') group by 1;"
```

### Step 1 — 控制面 unit：补预算门禁 + 恢复 start-limit

**只做两处最小改动**（不整份重渲染：模板里的 `check-deploy-source.sh` 已按 ADR-0051 Phase-1 形态退役，整份覆盖会把它带回来）：

```bash
systemctl cat stability-backend > /tmp/unit.new
python3 - <<'PY'
from pathlib import Path
t = Path("/tmp/unit.new").read_text()

# ① 恢复 #2058 的 start-limit（Phase-1 换根时丢失；硬门禁 + Restart=always 必须有界）
anchor_unit = "[Unit]\nDescription=Stability Test Platform Backend\nAfter=network.target\n"
assert anchor_unit in t and "StartLimitIntervalSec" not in t
t = t.replace(anchor_unit, anchor_unit + "StartLimitIntervalSec=300\nStartLimitBurst=3\n", 1)

# ② 预算门禁紧跟 schema 门禁之后（ADR-0047 D1，硬失败）
anchor = ("ExecStartPre=/home/debian13/stp-releases/current/venv/bin/python "
          "/home/debian13/stp-releases/current/tools/dev/check_alembic_at_head.py\n")
gate = (anchor + "ExecStartPre=/home/debian13/stp-releases/current/venv/bin/python "
        "/home/debian13/stp-releases/current/tools/dev/check_db_pool_budget.py\n")
assert anchor in t and gate not in t
t = t.replace(anchor, gate, 1)

# ③ 去掉 `systemctl cat` 的来源注释行
t = "\n".join(l for l in t.splitlines() if not l.startswith("# /etc/systemd/system/")) + "\n"
Path("/tmp/unit.new").write_text(t)
print("unit.new ready")
PY

# 0.4 人工核对：只应看到这两块差异
diff <(sudo cat /etc/systemd/system/stability-backend.service) /tmp/unit.new

sudo install -m0644 /tmp/unit.new /etc/systemd/system/stability-backend.service
sudo systemctl daemon-reload

# 0.5 窗口内重启（同时是门禁的实测；若 Gate 拒启 → 见 §3 回滚）
sudo systemctl restart stability-backend
sleep 3
systemctl show stability-backend -p ActiveState -p ExecMainStatus -p NRestarts
journalctl -u stability-backend --since "-3min" | grep -aE "app_total|check_db_pool_budget|budget"
curl -s -o /dev/null -w '/health=%{http_code}\n' http://127.0.0.1:8000/health
```

> 门禁预期输出（只读 `SHOW`，见 #3249 的 `config_source` 语义）：
> `[OK] app_total=80（每引擎 40 × 2） instances=1 budget=80 available=97 reserve=8 headroom=17 config_source=default`

### Step 2 — Prometheus 平台副本同步 + reload

```bash
cd /home/debian13/stp-releases/current
promtool check rules deploy/prometheus/alerts-stability-platform.yml

sudo install -m0644 deploy/prometheus/alerts-stability-platform.yml \
  /etc/prometheus/rules/alerts-stability-platform.yml
curl -s -X POST http://127.0.0.1:9091/-/reload

# 核对：规则名在场 + slots 规则去 for / 转 critical + 新规则在场
curl -s 127.0.0.1:9091/api/v1/rules | python3 - <<'PY'
import json, sys
g = json.load(sys.stdin)["data"]["groups"]
rules = {r["name"]: r for grp in g for r in grp["rules"]}
assert "StabilityTerminalBulkheadRejected" in rules, "新规则未加载"
slots = rules["StabilityDbConnectionSlotsExhausted"]
assert slots.get("for") in (None, "0s"), slots.get("for")   # 去 for
assert slots["labels"]["severity"] == "critical"
print("rules OK:", len(rules), "rules /", len(g), "groups")
PY

# 资产对账：平台规则副本一项应为 match（#2985 的假报已由 LEGACY_FALLBACKS 收口）
./venv/bin/python tools/dev/check-monitoring-assets.py --json | python3 -c "
import json,sys
for a in json.load(sys.stdin)['assets']:
    if 'alerts-stability-platform' in a['destination']:
        print(a['destination'], a['state'], a.get('source_used'))
"
```

### Step 3 — Agent 分发（#3242）

**前提**：分发输入树必须含 `fdce0ac6`（#3251）。**当前运行 bundle `c371113` 不含**，二选一：

**路径 A（推荐，符合 ADR-0051 D6：分发源 = 部署根）**

```bash
# A1 前滚开发树到 main（必须 clean 且在 main；有并行会话改动时改用路径 B）
cd /home/debian13/stability-test-platform
git fetch origin --quiet && git status --short && git merge --ff-only origin/main

# A2 构建新 bundle（schema-target 由工具现算）
REV=$(git rev-parse --short HEAD)
./venv/bin/python tools/release/build_bundle.py --repo-root . \
  --out /home/debian13/stp-releases/$REV --version local-$(date +%Y%m%d)-$REV
#  A2′ 部署根外部物料（ADR-0051 清单）：venv / logs / .env.backend / dist-prod / inventory.ini
ls -ld /home/debian13/stp-releases/$REV/venv /home/debian13/stp-releases/$REV/logs \
      /home/debian13/stp-releases/$REV/frontend/dist-prod \
      /home/debian13/stp-releases/$REV/tools/ansible/inventory.ini \
      /home/debian13/stp-releases/$REV/.env.backend
#  （venv 与 c371113 同法：`python3 -m venv <release>/venv` + 依赖安装；
#    若窗内不能复刻，改走路径 B，不要带着空 venv 切 current）

# A3 原子切换 + 重启（重启同时实测 Step 1 的门禁）
ln -sfn /home/debian13/stp-releases/$REV /home/debian13/stp-releases/current.tmp
mv -T /home/debian13/stp-releases/current.tmp /home/debian13/stp-releases/current
readlink -f /home/debian13/stp-releases/current
sudo systemctl restart stability-backend && sleep 3
curl -s -o /dev/null -w '/health=%{http_code}\n' http://127.0.0.1:8000/health
```

**路径 B（退路：不动 current，用暂存树分发——既有 exception 路径，09-15 实测 48/48）**

```bash
git worktree add --detach /tmp/stp-p0-src origin/main
cp -a /home/debian13/stability-test-platform/backend/agent/resources /tmp/stp-p0-src/backend/agent/
ln -s /home/debian13/stability-test-platform/.env.backend /tmp/stp-p0-src/.env.backend
cp -a /home/debian13/stability-test-platform/tools/ansible/inventory.ini /tmp/stp-p0-src/tools/ansible/inventory.ini
# ⚠ 不得从干净 worktree（无 resources）分发——会把机队资源层清空
```

**分发（两条路径共用）**

```bash
cd <选中源树>            # 路径 A = /home/debian13/stp-releases/current；路径 B = /tmp/stp-p0-src
grep -c _TERMINAL_UPLOAD_SEMAPHORE backend/agent/api_client.py   # 必须 ≥1（证明含 #3242）

/usr/bin/python3 - <<'PY'   # --direct 不加载 .env/.env.backend，须先 load（既有配方）
from dotenv import load_dotenv
for f in (".env", ".env.backend"):
    load_dotenv(f)
import importlib, sys
sys.argv = ["batch_hot_update.py", "--direct"]
raise SystemExit(importlib.import_module("backend.scripts.batch_hot_update").main())
PY
```

- 默认**跳过有在跑作业的 host**；本窗若要在跑也升级：`--include-active --abort-running-jobs`（**会 ABORT 在跑作业**，空转 `PATROL_SLEEP` 轮次作废——需 owner 明确同意）。
- 维护中 host 返回 `HOST_IN_MAINTENANCE`：归队前必须补分发。
- 热更新自带 `restart` + `restart_probe`，**不需要**手工重启 Agent。

**分发后核对**

```bash
cd <选中源树>
python3 tools/ansible/compute_deploy_digest.py \
  --source-dir backend/agent --schema-file backend/schemas/pipeline_schema.json
# 用打印出的 CODE_DIGEST 对账（有 RUNNING 作业的 host 会滞后——软锁正常态，等作业结束/下轮收敛）
psql "$DATABASE_URL" -c "select count(*) from host where agent_artifact_digest = '<CODE_DIGEST>';"
```

### Step 4 — 真机复跑观测（#3244 门槛）

> **口径纠正**：**不要**在生产库跑 `test_plan_run_abort_backflow_scale_3243.py`（它会向库内播种假 host/run；测试必须隔离）。「真机复跑」= 用**真实大 run** 做同口径的中止观测。

1. 确认无其它大 run → 从 UI 启动覆盖全机队的计划（如 monkey）
2. 等 `job_instance.status='RUNNING'` ≈ 480–490
3. 以 `stp-admin` 中止该 run，记录中止时刻（T0）
4. 观测 5–10 分钟并采数（下表左列命令）→ 把结果表贴到 [#3244](https://github.com/DUElost/stability-test-platform/issues/3244)

| # | 判据 | 采集 |
|---|---|---|
| 1 | 53300 = 0 | `sudo grep -aE "remaining connection slots" /var/log/postgresql/postgresql-17-main.log \| grep -a "<T0 分钟>"` |
| 2 | API 500 = 0（背压只能是 503） | nginx：`'" 500 '` 计数；`'" 503 '` 允许且应集中在 `/complete` |
| 3 | 可响应性 p99 < 1s | `histogram_quantile(0.99, rate(stability_api_request_duration_seconds_bucket[5m])) by (endpoint)`（重点看 `/complete`、plan-run、devices） |
| 4 | 池不越预算 | `max_over_time(stability_db_pool_checked_out{engine="async"}[1m])` ≤ 40、sync 同 |
| 5 | 490 条事实全部 ACK | `stability_agent_outbox_pending{type="terminal"}` 峰值 → 回落 0；`job_instance` 无 RUNNING 残留 |
| 6 | 计数一致 | `plan_run` 五列 vs `job_instance` 分组计数 vs `plan_run_host` 求和 |
| 7 | 120s 内收敛 | `plan_run.ended_at - T0`；`result_summary` 与分组计数一致 |
| 8 | 副作用无重复/丢失 | chain / dedup / post_completion 计数与审计（`saq_post_completion_*` 与终态数一致） |

补充观测：`increase(stability_terminal_bulkhead_rejected_total[5m])`、`stability_terminal_bulkhead_waiting`、`stability_db_pool_checkout_failures_total` 按 kind 拆分（**应为 0 增长**）。

> 若本窗中止的是**未完成部署前**就在跑的老 run（例如 plan_run 534 在 Step 3 前已终态），它的数据仍可用于「部署前基线」，不能用作 #3244 的达标证据。

---

## 3. 回滚

**触发条件**：出现 53300 / API 500 / 服务不可用 / UI p99 持续 > 1s / 门禁误拒 / 机队 digest 异常。

```bash
# R1 停窗口：记录现场（journal、backend_error.log、规则 API、审计）

# R2 恢复 unit（门禁或 start-limit 出问题时；fail-closed 拒启是设计，回滚 unit 即恢复）
sudo cp /etc/systemd/system/stability-backend.service.bak-20260924 /etc/systemd/system/stability-backend.service
sudo systemctl daemon-reload && sudo systemctl restart stability-backend

# R3 恢复告警副本
sudo cp /etc/prometheus/rules/alerts-stability-platform.yml.bak-20260924 /etc/prometheus/rules/alerts-stability-platform.yml
curl -s -X POST http://127.0.0.1:9091/-/reload

# R4 若已切 bundle：指回旧 release 并重启
ln -sfn "$(cat /tmp/release-before.txt)" /home/debian13/stp-releases/current.tmp
mv -T /home/debian13/stp-releases/current.tmp /home/debian13/stp-releases/current
sudo systemctl restart stability-backend

# R5 机队回退（如需要）：按 ADR-0040 对目标 host 重跑指向旧 code revision 的热更新（同一流程）
```

---

## 4. 本窗口不做（另单）

- 8 项既有监控资产 drift（node-exporter / `stp-mem-top` / `stp-script-guard` / `stp-skill-usage` 等）——本窗口只收敛平台规则副本一项。
- unit 模板与 Phase-1 现行形态的两处差异（`check-deploy-source.sh` 的去留、StartLimit* 的模板同步）——StartLimit 本窗已在**实装 unit** 恢复；模板同步属 ADR-0051 Phase 追踪。
- 多实例 `STP_DB_POOL_INSTANCES`（ADR-0027 未启动）。
- ADR-0052 的实施（须先转 Accepted，且真机数据达标）。

## 5. 编写时的只读证据（可复核）

```bash
readlink -f /home/debian13/stp-releases/current                 # → /home/debian13/stp-releases/c371113
systemctl show stability-backend -p ExecMainStartTimestamp      # → 2026-09-24 09:46:49 CST
systemctl cat stability-backend | grep -c check_db_pool_budget  # → 0
sudo grep -c StabilityTerminalBulkheadRejected /etc/prometheus/rules/alerts-stability-platform.yml  # → 0
psql "$DATABASE_URL" -c "select action,count(*) from audit_logs where action like 'hot_update%' and timestamp > now() - interval '14 hours' group by 1;"
curl -s 127.0.0.1:9091/api/v1/rules | python3 -c "import json,sys;d=json.load(sys.stdin);print(sum(len(g['rules']) for g in d['data']['groups']),'rules')"
```
