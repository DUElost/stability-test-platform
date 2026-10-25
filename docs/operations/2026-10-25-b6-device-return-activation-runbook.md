# B6 设备归还激活清单

> **状态：未执行。最后更新：2026-10-25。** 本文是 U7 第二个 PR 的执行清单，不是激活记录。
> 依据：[#3723 当前正文](https://github.com/DUElost/stability-test-platform/issues/3723) v1.7.21 §3.6.3、§3.7、§5，
> [Owner 批准的草案 v0.1](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6385265226)，
> 以及[第 10、11 步的规划者裁定](https://github.com/DUElost/stability-test-platform/issues/3723#issuecomment-6387873600)。

Owner 已于 2026-10-25 批准草案的九项取向，并中止当时全部在跑的 PlanRun。这是历史操作记录，不证明执行窗口内没有新增 job；第 6 步仍复查。
本文编写与评审不授权读取生产凭据、查询生产库、调用生产接口或操作 Agent。下面的命令仅供未来获授权的激活执行者使用。

## 0. 执行约定

角色：Owner 决定四个确认点及异常处置；执行者在控制面宿主执行命令；核验者按只读方式复核证据；规划者核对清单与最终生效证据。
执行者必须按第 1–18 步顺序执行。执行者遇到停止条件时必须停在当步并报告 Owner。执行者不得自行调整顺序或用后续确认点的结果补前置闸门。

通用部署命令只引用 [control-plane-deploy SOP](../../.claude/skills/control-plane-deploy/SKILL.md) 的步骤编号。
B6 的控制面回退限制优先于该 SOP 的通用回滚描述：迁移后执行者必须只前滚修复。执行者不得指回不认识新 revision 的旧控制面。执行者不得用 downgrade 删列。
激活前的残留按[开关机手册 §1–§4](./powercycle-chain-disarm-runbook.md)处置；阶段 B 生效后的义务由欠账承载。

以下变量在实际执行时填入，并在新 shell 中重新设置；执行者不得把变量的凭据值写进文档或评论：

```bash
set -euo pipefail
B6_REPO=/home/debian13/stability-test-platform
B6_RELEASE=/home/debian13/stp-releases/current
B6_EVIDENCE_DIR='/home/debian13/stp-evidence/b6-<窗口编号>'
mkdir -p "$B6_EVIDENCE_DIR"
```

`AUTH` 按 SOP §0 在发布根取得。`B6_RO_DATABASE_URL` 按 [production-diagnostics](./production-diagnostics.md) 的只读身份配置。
各 SQL 代码块均用下面的只读连接入口粘贴执行；其中 `b6_window_at` 只在确认点 1 之后使用，`b6_resumed_at` 在第 16 步之后使用，`b6_catalog_version` 与 `b6_canary_host_id` 在第 10 步赋值后使用：

```bash
psql "$B6_RO_DATABASE_URL" -X -v ON_ERROR_STOP=1 \
  -v b6_window_at="${B6_WINDOW_AT:-}" -v b6_resumed_at="${B6_RESUMED_AT:-}" \
  -v b6_catalog_version="${B6_CATALOG_VERSION:-}" -v b6_canary_host_id="${B6_CANARY_HOST_ID:-}"
```

每个 SQL 代码块含 `BEGIN READ ONLY` 与 `ROLLBACK`。生产 CLI 的 dry-run 另用 SOP §0 导出的站点运行环境，按现有逐设备事务执行。初始化执行者不得把只读诊断凭据传给 apply。
核验者更新上述 shell 变量后必须重新启动此 psql 入口，以传入新值；旧 psql 会话不会随 shell 变量更新。
执行者必须逐段执行命令。执行者遇到任一非预期的非零状态时必须停在当步。执行者不得在前一段失败后继续粘贴下一段。
证据目录保存命令、时间、HTTP 状态、结果摘要与 Owner 确认引用；执行者不得把 token、连接串、完整设备序列号或 SSH 凭据写进证据摘要。

## 1. 第 1–18 步

### 1. 固定激活 SHA 并完成完整测试（窗口前）

**执行者：实施者/核验者。** 本清单合入后，以届时 main 为激活 SHA。实施者不得使用本 PR head 或本文编写基线作为激活 SHA。
先核对 #3723 §5 第 0 条的既有交付证据：U1–U8 均已合入、U1–U5/U8 独立复核通过、最终包的 MLD_LX2/MLD_LX3 真机报告与 required CI 已齐备；它们不是第 17 步新 L2 清单的结果。

```bash
cd "$B6_REPO"
git fetch origin main
B6_ACTIVATION_SHA=$(git rev-parse origin/main)
B6_U7_MERGE_SHA=$(gh pr view 3790 --repo DUElost/stability-test-platform --json mergeCommit --jq '.mergeCommit.oid')
git merge-base --is-ancestor "$B6_U7_MERGE_SHA" "$B6_ACTIVATION_SHA"
printf '%s\n' "$B6_ACTIVATION_SHA" > "$B6_EVIDENCE_DIR/activation-sha.txt"
gh workflow run ci.yml --ref main --repo DUElost/stability-test-platform
gh run list --repo DUElost/stability-test-platform --workflow ci.yml \
  --event workflow_dispatch --commit "$B6_ACTIVATION_SHA" --limit 20 \
  --json databaseId,headSha,status,conclusion
B6_CI_RUN_ID='<上述同SHA全量运行的ID>'
gh run watch "$B6_CI_RUN_ID" --repo DUElost/stability-test-platform --exit-status
gh run view "$B6_CI_RUN_ID" --repo DUElost/stability-test-platform \
  --json headSha,conclusion,jobs > "$B6_EVIDENCE_DIR/full-ci.json"
jq -e --arg sha "$B6_ACTIVATION_SHA" '
  .headSha == $sha and .conclusion == "success" and
  any(.jobs[]; .name == "backend-test" and .conclusion == "success" and
    any(.steps[]; .name == "Run backend tests" and .conclusion == "success") and
    any(.steps[]; .name == "Install pinned promtool" and .conclusion == "success") and
    any(.steps[]; .name == "Run agent tests" and .conclusion == "success") and
    any(.steps[]; .name == "Run repo-level tests" and .conclusion == "success"))
' "$B6_EVIDENCE_DIR/full-ci.json"
```

预期：同一 SHA 的完整 CI 成功，含 backend 全量、Agent 与仓库测试、安装 pinned promtool 后的告警场景层；PR required checks 也成功。
核验者必须同时核对该运行的 `backend-test` 日志中 promtool 版本与场景用例实际执行，skip 不计为通过。
审计预期：只有 GitHub Actions 运行/作业记录，无生产审计行；上述 `gh run view` 是只读回查。
**停止：** PR 尚未合入、SHA 不匹配、任一完整检查失败/跳过/未完成，或构建前 main 又前进；重新绑定 SHA、重跑检查后再进入后续步骤。

### 2. 构建 bundle 与前端，停在阶段 A 第 14 步

**执行者：部署执行者。命令：** 执行 SOP §0.5 阶段 A 第 1–14 步；前端构建见其第 12 步，bundle 与 venv 见第 13 步，停顿与汇报见第 14 步。
第 6 步发现 #3723 仍 OPEN 时，引用本次 Owner 激活批准。执行者必须保留该确认记录。执行者不得以“issue 应关闭”代替批准。
预期：部署源干净且为已测激活 SHA，新增 migration 三项已识别，新 release 的 source.revision 与该 SHA 一致；交出切换命令及“控制面只前滚”的说明。
审计预期：构建产物、守卫输出、前端入口与阶段 A 报告；不写业务审计。只读回查按 SOP 阶段 A 第 9、13、14 步。
**停止：** 构建源不干净/不是 main、任何 DO-NOT-SWITCH 条件未解除、release/前端 SHA 不一致、依赖/就绪检查失败。
随行变更的批准范围沿用草案：#3742、依赖升级、main 版展锐模板。执行者不得扩到 #3758 的其余待裁事项。

### 3. 列出 schedule、未结束运行与离线主机（只读）

**执行者：核验者。** 用 API 分页保存启用中的 schedule；接口无 enabled 过滤参数，在客户端筛选：

```bash
: > "$B6_EVIDENCE_DIR/enabled-schedules.ndjson"
B6_SKIP=0
while :; do
  B6_PAGE=$(curl --fail-with-body -sS -H "$AUTH" \
    "http://127.0.0.1:8000/api/v1/schedules?skip=$B6_SKIP&limit=200") || exit 1
  printf '%s' "$B6_PAGE" | jq -c '.items[] | select(.enabled) | {id,name,plan_id,enabled,next_run_at}' \
    >> "$B6_EVIDENCE_DIR/enabled-schedules.ndjson"
  B6_TOTAL=$(printf '%s' "$B6_PAGE" | jq -r '.total')
  B6_SKIP=$((B6_SKIP + 200))
  [ "$B6_SKIP" -ge "$B6_TOTAL" ] && break
done
jq -s . "$B6_EVIDENCE_DIR/enabled-schedules.ndjson" > "$B6_EVIDENCE_DIR/enabled-schedules.json"
```

```sql
BEGIN READ ONLY;
SELECT id, plan_id, status, started_at FROM plan_run
WHERE status IN ('QUEUED','PRECHECK','RUNNING') ORDER BY id;
SELECT id, plan_run_id, host_id, device_id, status FROM job_instance
WHERE status IN ('PENDING','RUNNING','UNKNOWN') ORDER BY host_id, id;
SELECT id, status, last_heartbeat FROM host
WHERE retired_at IS NULL AND status <> 'ONLINE' ORDER BY id;
SELECT id, plan_id, enabled, next_run_at FROM task_schedules WHERE enabled IS TRUE ORDER BY id;
SELECT count(*) AS old_disarm_references FROM plan_step
WHERE script_name='powercycle_disarm' AND script_version IN
 ('1.0.0','1.0.1','1.0.2','1.0.3','1.0.4','1.0.5','1.0.6','1.0.7');
ROLLBACK;
```

预期：形成最新清单，API 与 SQL 的启用 schedule id 对齐；离线主机单列，历史数量不是当前事实。old_disarm_references=0；第 8 步仍复查。
审计预期：GET/SELECT 不新增业务审计；以上相同查询只读复查。
**停止：** HTTP/分页读取失败、清单不完整或 API 与 SQL id 不一致，先报告 Owner。核验者不得把隐藏/不可访问的 schedule 算作已停用。
如果 old_disarm_references 不为 0，核验者必须停在本步并在确认点 1 之前交 Owner 裁决（#3723 §3.6.3 第 2 条）。

### 4. Owner 确认点 1：开始窗口，关闭 schedule

**执行者：Owner 确认，执行者操作。** Owner 在 #3723 确认开始时刻、时长、阶段 A 报告及本次暂停/恢复责任人；执行者记录 `B6_WINDOW_AT`。
窗口内执行者必须暂停手工触发 PlanRun。没有全局硬暂停开关，关闭 schedule 与操作纪律共同生效。

> 警告：toggle 是翻转，重复调用可能重新启用 schedule，使新 job 进入维护窗口。执行者必须先读取当前 enabled。请求结果不明时，执行者不得盲重试；先 GET 确定状态后再决定是否重试。

```bash
B6_WINDOW_AT='<Owner确认的ISO8601开始时刻>'
jq -r '.[].id' "$B6_EVIDENCE_DIR/enabled-schedules.json" | while read -r B6_SCHEDULE_ID; do
  B6_STATE=$(curl --fail-with-body -sS -H "$AUTH" \
    "http://127.0.0.1:8000/api/v1/schedules/$B6_SCHEDULE_ID") || exit 1
  if [ "$(printf '%s' "$B6_STATE" | jq -r '.enabled')" = true ]; then
    curl --fail-with-body -sS -X POST -H "$AUTH" \
      "http://127.0.0.1:8000/api/v1/schedules/$B6_SCHEDULE_ID/toggle" \
      | tee "$B6_EVIDENCE_DIR/schedule-$B6_SCHEDULE_ID-disabled.json" | jq -e '.enabled == false' || exit 1
  fi
done
```

预期：第 3 步清单内 enabled=false、next_run_at=null；用第 3 步 SQL 复查没有遗漏的启用行。
审计预期：`toggle_schedule` 不调用 record_audit，不产生独立 toggle 审计行；保存 HTTP 回执与 Owner 确认，GET/SELECT 是状态回查。
**停止：** Owner 未确认、任何非 200/状态不符、仍有启用 schedule，或有人手工派发；停在暂停阶段，不切控制面。

### 5. 复核排空记录

**执行者：Owner/核验者。** Owner 已于 2026-10-25 中止当时全部在跑 PlanRun；重跑第 3 步的运行清单。
如果出现新增运行，Owner 必须逐个选择等待自然结束或中止，并记录选择。批准中止单个运行时用：

```bash
B6_ABORT_RUN_ID='<Owner本次明确批准中止的PlanRun整数ID>'
curl --fail-with-body -sS -X POST -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"reason":"B6 activation drain"}' \
  "http://127.0.0.1:8000/api/v1/plan-runs/$B6_ABORT_RUN_ID/abort"
```

预期：200 回报中止请求；ACK 与实际收尾仍由第 6 步确认。核验者不得只凭接口成功判定已排空。
审计预期：新中止产生 `abort_plan_run`，同步终止一批 job 时可能另有 `job_batch_terminalized`；按附录 A 回查。
**停止：** 未获逐个批准、404（运行不存在）、409（状态冲突）或未排空。执行者不得自动重试中止。核验者不得跳过 UNKNOWN。

### 6. 零在途 job 闸门

**执行者：核验者。** 再读生产当前事实，不沿用 Owner 先前中止时的快照：

```sql
BEGIN READ ONLY;
SELECT status, count(*) AS jobs FROM job_instance
WHERE status IN ('PENDING','RUNNING','UNKNOWN') GROUP BY status;
ROLLBACK;
```

预期：0 行；第 3 步也无仍可产生 job 的 QUEUED/PRECHECK/RUNNING PlanRun。
审计预期：只读 SELECT，无新增审计或事件；保留查询时刻与零行结果。
**停止：** 任一 PENDING/RUNNING/UNKNOWN job 或未结束 PlanRun；回第 5 步由 Owner 处置。执行者不得让新消费者接管旧在跑 job。

### 7. 切控制面与 nullable 迁移

**执行者：部署执行者。命令：** 执行 SOP §0.5 阶段 B 第 1 步及第 3 步；健康核验用该第 1 步的轮询，不复制切换命令。
预期：stability-backend active、/health 200；ExecStartPre 自动执行迁移并通过 schema-at-head。
三项 B6 迁移是 `c3d4e5f6a7b8`、`u3b6c7d8e9f0`、`u8b6d3752a01`，只加可空列或新表。

```sql
BEGIN READ ONLY;
SELECT version_num FROM alembic_version;
SELECT table_name, column_name, data_type FROM information_schema.columns
WHERE table_schema='public' AND
 ((table_name='job_instance' AND column_name IN ('disarm_results','disarm_guard'))
 OR (table_name='device' AND column_name='return_state') OR table_name='device_return_debt')
ORDER BY table_name, ordinal_position;
ROLLBACK;
```

审计预期：切换/重启/DDL 无专门业务审计；以 SOP 的 manifest、unit/journal、health 与上述只读 schema 查询留证。
**停止：** health 或迁移失败、schema 不符；只前滚修复，覆盖 SOP 阶段 B 第 2 步的通用旧 rev 回滚分支。执行者不得继续换前端或升级 Agent。

### 8. 发包、八个旧版本恢复发布、scan

**执行者：发包执行者。** 恢复发布依赖 git 树对象，因此命令从已冻结激活 SHA 的 git 检出运行；包写入站点包源。
先按 SOP §0 导出站点环境。`B6_PACKAGES_ROOT` 优先取 `STP_PACKAGES_ROOT`，未设置时回落到 `STP_AEE_NFS_ROOT/packages`；顺序与 scan 的 `default_packages_root()` 相同。
发包执行者必须在发布根打印 `default_packages_root()`。发包执行者必须将输出与选定目录比较。只有两个路径相同，发包执行者才可以继续发包。

```sql
BEGIN READ ONLY;
SELECT count(*) AS old_disarm_references FROM plan_step
WHERE script_name='powercycle_disarm' AND script_version IN
 ('1.0.0','1.0.1','1.0.2','1.0.3','1.0.4','1.0.5','1.0.6','1.0.7');
ROLLBACK;
```

预期：0。以下 manifest 校验从已测检出读取，不改登记与冻结包：

```bash
B6_PACKAGES_ROOT="${STP_PACKAGES_ROOT:-${STP_AEE_NFS_ROOT:?按SOP导出站点环境}/packages}"
cd "$B6_RELEASE"
B6_SCAN_PACKAGES_ROOT=$(venv/bin/python - <<'PY'
from backend.services.script_catalog import default_packages_root
root=default_packages_root()
assert root is not None, 'packages root undefined'
print(root)
PY
)
printf 'publish packages root: %s\nscan packages root: %s\n' "$B6_PACKAGES_ROOT" "$B6_SCAN_PACKAGES_ROOT"
test "$B6_PACKAGES_ROOT" = "$B6_SCAN_PACKAGES_ROOT"
cd "$B6_REPO"
test "$(git rev-parse HEAD)" = "$B6_ACTIVATION_SHA"
./scripts/project_python.sh - <<'PY'
import json
from pathlib import Path
m=json.loads(Path('tool_manifest.json').read_text())['tools']
for name, version, expected in [
 ('powercycle_disarm','1.0.8','98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557'),
 ('powercycle_finish','1.1.0','76d9883df880d2156f016db6bc8adcf902f978ae87690331181b07c897ec1aff')]:
    row=next(v for v in m[name]['versions'] if v['version']==version)
    assert row['package_sha256']==expected and not row.get('retired',False)
    print(name, version, row['package_sha256'])
PY
./scripts/project_python.sh tools/dev/check_script_packages.py --publish --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.0 --source-tree 9943ccae94c232151ee7ce826deecd355485a50e --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.1 --source-tree 153c867d9bc2438144506884625b1d66749becd5 --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.2 --source-tree 1fdf49a42f9ed3bae7a453302ce147db9909cf03 --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.3 --source-tree c832364b0fb1f1ef5f143835900b3a336d372f4d --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.4 --source-tree 09df58f12ba303b24ed2efa2ad90c71516fdfab9 --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.5 --source-tree 200c857e2d60f629d4b4e7e4d98e9f990453a835 --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.6 --source-tree 2d17d445221b12724f635fa9cf5faedb8ce388a4 --packages-root "$B6_PACKAGES_ROOT"
./scripts/project_python.sh tools/dev/check_script_packages.py --publish-restored powercycle_disarm@1.0.7 --source-tree 96c2391d2909282afe4588ec3d3aa73ec23bf146 --packages-root "$B6_PACKAGES_ROOT"
sha256sum "$B6_PACKAGES_ROOT/powercycle_disarm/1.0.8.tar.gz" "$B6_PACKAGES_ROOT/powercycle_finish/1.1.0.tar.gz"
curl --fail-with-body -sS -X POST -H "$AUTH" http://127.0.0.1:8000/api/v1/scripts/scan \
  | tee "$B6_EVIDENCE_DIR/scan.json" | jq '.data | {created,skipped,conflicts,package_missing,package_conflicts}'
curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/scripts?is_active=false' \
  | jq -e '[.data[] | select(.name=="powercycle_disarm") | .version] as $seen |
    ["1.0.0","1.0.1","1.0.2","1.0.3","1.0.4","1.0.5","1.0.6","1.0.7"] |
    all(.[]; . as $v | $seen | index($v)!=null)'
curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/scripts?is_active=true' \
  | jq -e '.data | any(.[]; .name=="powercycle_disarm" and .version=="1.0.8") and
    any(.[]; .name=="powercycle_finish" and .version=="1.1.0")'
```

预期：打印的两个包源路径相同、test exit=0；正常发包成功写入或同字节跳过；八次 restored 均 `[OK] publish-restored`，新版本 SHA 与 manifest 相同。
scan HTTP 200、conflicts/package_conflicts 为空；八个旧版本 inactive；package_missing 仅剩 #3661 明确登记的存量，且没有 powercycle_disarm/powercycle_finish。
`--publish` 会核对每族最新包并派生 manifest 副本，不是按版本过滤的命令；执行者必须记录实际新落位清单。
审计预期：本地发包不写 audit_logs，保留工具回执；scan 写一条 `action=scan, resource_type=script`，附录 A 只读回查其计数摘要。
**停止：** 旧引用不为零、包源未配置或两个路径不同、树对象不可达、SHA/retired 状态不符、工具退出非零、scan 非 200、任何 conflict 或超出 #3661 的缺包。
执行者不得用 force_rebaseline 消除冲突。执行者不得改写旧包或 manifest。

### 9. 同步告警副本，再换前端

**执行者：部署执行者。** 从新发布根先校验再同步五条 B6 告警；只 reload Prometheus：

```bash
cd "$B6_RELEASE"
promtool check rules deploy/prometheus/alerts-stability-platform.yml
sudo install -m0644 deploy/prometheus/alerts-stability-platform.yml /etc/prometheus/rules/alerts-stability-platform.yml
curl --fail-with-body -sS -X POST http://127.0.0.1:9091/-/reload
curl --fail-with-body -sS http://127.0.0.1:9091/api/v1/rules \
  | jq -e '[.data.groups[].rules[].name] as $names |
    ["StabilityDisarmNotExecutedOrIncomplete","StabilityDisarmFailedRatioHigh",
     "StabilityDeviceReturnDebtOpenHigh","StabilityDeviceReturnDebtOldestHigh",
     "StabilityDeviceReturnOverrideHigh"] | all(.[]; . as $n | $names | index($n)!=null)'
B6_ASSETS_RC=0
venv/bin/python tools/dev/check-monitoring-assets.py --json \
  > "$B6_EVIDENCE_DIR/monitoring-assets.json" || B6_ASSETS_RC=$?
jq -e '.assets[] | select(.source_used=="deploy/prometheus/alerts-stability-platform.yml") |
  .state=="match"' "$B6_EVIDENCE_DIR/monitoring-assets.json"
printf 'monitoring-assets exit=%s\n' "$B6_ASSETS_RC"
```

预期：promtool/reload/规则名断言成功，平台告警副本这一项 match；其它既有资产 drift 逐项记录。执行者不得把其它资产的既有 drift 记为本窗已修复。
然后按 SOP §0.5 阶段 B 第 8 步换前端，核对新入口文件名。
审计预期：规则复制/reload/前端目录切换无业务审计；只读 rules API、资产报告、SOP 前端入口对账是回查证据。
**停止：** promtool 非零、reload 非 200、五条规则缺失、副本不 match 或前端入口不符。

### 10. 单台空闲 canary 热更新与能力核验

**执行者：部署执行者/核验者。命令：** 先按 SOP §0.5 阶段 C 第 1–5 步选空闲 ONLINE 主机并 canary 热更新，记录 `B6_CANARY_HOST_ID` 与其既有 SSH 访问目标 `B6_CANARY_SSH`。
预期：matched、心跳晚于更新完成；B6 前后版本号同为 2.0.0，无法据此区分能力。执行者不得为 B6 设置 STP_AGENT_MIN_VERSION。

四项 B6 只读核对如下，均不依赖第 12 步：

```bash
curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/scripts?is_active=true' \
  | jq -e '.data[] | select(.name=="powercycle_disarm" and .version=="1.0.8") |
    .package_sha256=="98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557"'
sha256sum "$B6_PACKAGES_ROOT/powercycle_disarm/1.0.8.tar.gz"
cd "$B6_RELEASE"
B6_CATALOG_VERSION=$(venv/bin/python - <<'PY'
from sqlalchemy import text
from backend.core.database import SessionLocal
from backend.services.script_catalog_version import compute_script_catalog_version
with SessionLocal() as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    print(compute_script_catalog_version(db))
    db.rollback()
PY
)
printf '%s\n' "$B6_CATALOG_VERSION" > "$B6_EVIDENCE_DIR/catalog-version.txt"
curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/hosts/$B6_CANARY_HOST_ID" \
  | jq -e '.status=="ONLINE" and .agent_code_sync_status=="matched"'
```

上述 hosts API 不返回 script_catalog_version。重开 §0 的 psql 入口，传入本步的 `B6_CATALOG_VERSION` 与 `B6_CANARY_HOST_ID`，只读核对目录列：

```sql
BEGIN READ ONLY;
SELECT id, status, script_catalog_version FROM host
WHERE id=:'b6_canary_host_id' AND retired_at IS NULL AND status='ONLINE';
SELECT id, status, script_catalog_version FROM host
WHERE id=:'b6_canary_host_id' AND retired_at IS NULL AND status='ONLINE'
  AND script_catalog_version IS DISTINCT FROM :'b6_catalog_version';
ROLLBACK;
```

预期：第一条恰好 1 行，证明 canary 存在、未退役且 ONLINE；第二条 0 行，证明目录版本相同。NULL 也属于版本不符。

主机侧核验使用既有 SSH 访问配置。核验者不得在文档中填主机地址或凭据。以下小程序仅只读包与 Agent 本地状态；不调用 LocalDB.initialize、不运行探针、不拉包：

```bash
cat > "$B6_EVIDENCE_DIR/host-b6-readonly.py" <<'PY'
import json, os, sqlite3
from pathlib import Path
from dotenv import load_dotenv
os.chdir('/opt/stability-test-agent')
load_dotenv('.env', override=False)
from agent.config import BASE_DIR
from agent.tool_cache import _packages_root, _sha256_of
root=_packages_root(os.environ)
assert root is not None, 'packages root undefined'
package=root/'powercycle_disarm'/'1.0.8.tar.gz'
digest=_sha256_of(package)
assert digest=='98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557'
with sqlite3.connect((BASE_DIR/'agent_state.db').as_uri()+'?mode=ro', uri=True) as db:
    row=db.execute('SELECT value FROM agent_state WHERE key=?', ('return_init_check_available',)).fetchone()
assert row and row[0]=='1', 'init-check endpoint not yet declared by heartbeat'
print(json.dumps({'package_sha256':digest,'return_init_check_available':True}))
PY
ssh "$B6_CANARY_SSH" '/opt/stability-test-agent/venv/bin/python -' \
  < "$B6_EVIDENCE_DIR/host-b6-readonly.py" > "$B6_EVIDENCE_DIR/canary-package.json"
B6_LOG_RC=0
ssh "$B6_CANARY_SSH" "grep -E 'disarm_protocol_unavailable' /opt/stability-test-agent/logs/agent_error.log /opt/stability-test-agent/logs/agent.log" \
  > "$B6_EVIDENCE_DIR/canary-protocol.log" || B6_LOG_RC=$?
test "$B6_LOG_RC" -lt 2
cat "$B6_EVIDENCE_DIR/canary-protocol.log"
curl --fail-with-body -sS -X POST -H "$AUTH" \
  "http://127.0.0.1:8000/api/v1/script-presence/refresh?host_id=$B6_CANARY_HOST_ID"
curl --fail-with-body -sS -H "$AUTH" \
  "http://127.0.0.1:8000/api/v1/script-presence/hosts/$B6_CANARY_HOST_ID"
```

预期：控制面目录/站点包/主机包 SHA 同为 98cc16f8…；host.script_catalog_version 等于当前控制面目录版本。
Agent 本地声明值为 1，表示实际心跳已告知 return_init_check_available；更新后的日志时间段没有 disarm_protocol_unavailable。
grep 退出 1 表示无匹配，2 表示读取失败；SSH 连接失败通常为 255。核验者必须按时间区分历史旧行。核验者不得把历史旧警告记为本次失败。
presence HTTP 200 且目标版本没有 missing/mismatch/unknown，证明已引用版本的包源路径可用；它不证明 1.0.8 已预热。核验者不得用空目标集的全零结果作成功证据。
审计预期：canary 有 hot_update_result 结果（含 no-op）；具体请求路径可能另写 hot_update。能力 GET 不新增审计；hosts GET 会将心跳过期的 ONLINE 标为 OFFLINE，该变化是停止信号。
presence refresh 写 host_script_presence 账本及 host.script_packages_mode，无专门业务审计；GET 账本与附录 A 回查。
**停止：** canary 非 matched/心跳不新、当前协议警告、目录/任一包 SHA/本地声明不符、SSH 读取失败、presence 无实际可核验目标或失败。

### 11. 批量热更新与全 ONLINE 闸门

**执行者：部署执行者/核验者。命令：** 按 SOP §0.5 阶段 C 第 6–7 步执行批量热更新及同步状态统计；是否 --force 沿用其判据。执行者不得加入 abort_running_jobs。
记录全部在用 ONLINE host。核验者按既有 SSH 配置准备 `online-hosts-ssh.tsv`（每行 host_id、SSH 目标，以制表符分隔，无表头）；只保存在受限证据目录，摘要只列 host_id。
逐台用第 10 步的只读程序核验主机侧包 SHA：

```bash
curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/hosts?limit=200' \
  > "$B6_EVIDENCE_DIR/hosts-after.json"
jq -e '.total<=200 and all(.items[] | select(.status=="ONLINE");
  .agent_code_sync_status=="matched")' "$B6_EVIDENCE_DIR/hosts-after.json"
jq -r '.items[] | select(.status=="ONLINE") | .id' "$B6_EVIDENCE_DIR/hosts-after.json" \
  | sort > "$B6_EVIDENCE_DIR/online-host-ids.txt"
cut -f1 "$B6_EVIDENCE_DIR/online-hosts-ssh.tsv" | sort > "$B6_EVIDENCE_DIR/ssh-host-ids.txt"
diff -u "$B6_EVIDENCE_DIR/online-host-ids.txt" "$B6_EVIDENCE_DIR/ssh-host-ids.txt"
while IFS=$'\t' read -r B6_HOST_ID B6_SSH_TARGET; do
  ssh "$B6_SSH_TARGET" '/opt/stability-test-agent/venv/bin/python -' \
    < "$B6_EVIDENCE_DIR/host-b6-readonly.py" > "$B6_EVIDENCE_DIR/host-$B6_HOST_ID-package.json" || exit 1
done < "$B6_EVIDENCE_DIR/online-hosts-ssh.tsv"
```

用 §0 的 psql 入口传入 `B6_CATALOG_VERSION`，只读查询目录版本不同的在用 ONLINE 主机：

```sql
BEGIN READ ONLY;
SELECT id, status, script_catalog_version FROM host
WHERE retired_at IS NULL AND status='ONLINE'
  AND script_catalog_version IS DISTINCT FROM :'b6_catalog_version'
ORDER BY id;
ROLLBACK;
```

预期：SUMMARY fail=0，API 中全部 ONLINE 在用主机 matched；上述 SQL 为 0 行；逐台包 SHA 一致且有成功回执。
核验者必须逐项核对 TSV 覆盖与结果文件。核验者不得以循环退出或已有旧文件代替逐台成功。
离线主机按第 3 步列入补更清单；其重新上线后执行本步。核验者不得宣称旧 Agent 已具备 B6 能力。
审计预期：批量 direct 路径写 hot_update_result，按 details.entry、host_id、ok/converged 回查；具体请求路径另可能写 hot_update。
**停止：** total>200（清单未完整覆盖）、任何未收敛、目录/SHA 不同、访问失败。先修复，或由 Owner 在恢复派发前逐台决定暂不使用并记录隔离措施。核验者不得把未核验机算通过。

### 12. Owner 确认点 2：在用计划重指清单与预算

**执行者：执行者取现态，Owner 确认后执行者写入。** 候选 54/78/66/71/35 不是授权的最终清单；Owner 按窗口内现态确认具体 plan id、完整 steps 请求体与预算。

```bash
for B6_PLAN_ID in 54 78 66 71 35; do
  curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/plans/$B6_PLAN_ID" \
    > "$B6_EVIDENCE_DIR/plan-$B6_PLAN_ID-before.json" || exit 1
  jq --slurpfile template "$B6_RELEASE/backend/schemas/pipeline_templates/powercycle.json" '
    .data | {expected_updated_at:.updated_at,steps:[.steps[] |
    {step_key,script_name,script_version,stage,sort_order,timeout_seconds,stall_seconds,params,retry,enabled} |
    if .script_name=="powercycle_disarm" then .script_version="1.0.8" | .timeout_seconds=([(.timeout_seconds//0),660]|max)
    elif .script_name=="powercycle_finish" then .script_version="1.1.0" | .timeout_seconds=([(.timeout_seconds//0),420]|max)
    else . end]} |
    if any(.steps[]; .stage=="teardown" and .script_name=="powercycle_disarm") then .
    else
      ($template[0].lifecycle.teardown[] | select(.action=="script:powercycle_disarm")) as $d |
      if any(.steps[]; .step_key==$d.step_id) then error("disarm step_key collision")
      else .steps += [{step_key:$d.step_id,script_name:"powercycle_disarm",script_version:$d.version,
        stage:"teardown",sort_order:((([.steps[] | select(.stage=="teardown") | .sort_order]|min)//0)-1),
        timeout_seconds:$d.timeout_seconds,stall_seconds:0,params:$d.params,retry:$d.retry,enabled:true}]
      end
    end |
    if any(.steps[]; .stage=="teardown" and .script_name=="powercycle_disarm" and .enabled)
    then . else error("disarm exists but is disabled; review full steps") end
    ' "$B6_EVIDENCE_DIR/plan-$B6_PLAN_ID-before.json" \
    > "$B6_EVIDENCE_DIR/plan-$B6_PLAN_ID-proposed.json"
done
```

Owner 确认材料包含 `expected_updated_at` 与完整 `steps`（step_key、script_name、script_version、stage、sort_order、timeout_seconds、stall_seconds、params、retry、enabled）。
执行者必须保留其它计划字段和 init/patrol 步骤；PUT 的 steps 是整体替换，不是局部 patch。
目标是 disarm 1.0.8、finish 1.1.0；有效墙钟至少分别为 660/420 秒，且不小于有效参数的 wait_device_online_seconds+60、collect_attempts×collect_retry_wait_seconds+60。
如果现态没有 disarm，候选生成命令从 main 模板复制该步骤，排在既有 teardown 之前；step_key 冲突或已有 disarm 被关闭时停止，交 Owner 审阅完整 steps。
生成的 proposed 文件只提供默认配置与最低墙钟，不推断现有步骤的有效参数。核验者必须合并目标版本的 default_params 与步骤 params 核算预算；目录读取命令沿用第 10 步的 active scripts GET，字段为 default_params。
如果参数超过默认预算，执行者必须提高墙钟并重新交 Owner 确认。
确认文件保存为 `approved-plan-ids.txt` 与 `plan-<id>-approved.json`；每份 JSON 的顶层只有 `expected_updated_at` 和 `steps`，各字段取 main 的 PlanUpdate/PlanStepIn。

> 警告：下一步整体替换存量计划的 steps。Owner 未确认清单与完整请求体时，执行者不得调用 PUT；否则可能丢失计划步骤或预算。409 后执行者必须重新取现态并重新确认。执行者不得自动覆盖并发修改。

```bash
while read -r B6_PLAN_ID; do
  jq -e '.expected_updated_at!=null and (.steps|type=="array")' \
    "$B6_EVIDENCE_DIR/plan-$B6_PLAN_ID-approved.json" || exit 1
  curl --fail-with-body -sS -X PUT -H "$AUTH" -H 'Content-Type: application/json' \
    --data-binary "@$B6_EVIDENCE_DIR/plan-$B6_PLAN_ID-approved.json" \
    "http://127.0.0.1:8000/api/v1/plans/$B6_PLAN_ID" \
    > "$B6_EVIDENCE_DIR/plan-$B6_PLAN_ID-updated.json" || exit 1
  curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/plans/$B6_PLAN_ID" \
    | jq '.data | {id,updated_at,steps}'
done < "$B6_EVIDENCE_DIR/approved-plan-ids.txt"
```

预期：HTTP 200、GET 与批准请求一致；422 表示版本/接入形态/有效参数/墙钟等校验未通过，409 表示并发修改。
重指后可按第 10 步刷新 presence，1.0.8 此时进入引用集，预热只作优化。
审计预期：每次成功 PUT 写 plan_updated（changed 含 steps），并调用 emit_plan_changed 通知；附录 A 与 GET 只读回查。
**停止：** 未确认、任何 404/409/422、部分计划成功而其余失败、预算或读回不符；逐项报告已改与未改。执行者不得整批盲重试。

### 13. 阶段 B 初始化 dry-run 与类别汇总

**执行者：初始化执行者。** 从新发布根、SOP §0 的站点运行环境执行现有 CLI；默认 dry-run 也显式写出：

```bash
cd "$B6_RELEASE"
venv/bin/python -m backend.scripts.init_device_return --dry-run \
  > "$B6_EVIDENCE_DIR/init-dry-run.log" 2>&1
```

CLI 只输出设备状态与总计，不直接给专项/类别汇总。以下命令复用同一逐设备 dry-run 入口汇总，不打印 serial、不提交事务：

```bash
venv/bin/python - <<'PY' > "$B6_EVIDENCE_DIR/init-dry-run-summary.json"
import json
from collections import Counter
from sqlalchemy import select
from backend.core.database import SessionLocal
from backend.models.host import Device
from backend.scripts.init_device_return import PAGE_SIZE, _fetch_device_id_page, _process_one_device
totals=Counter(); after=0
while True:
    ids=_fetch_device_id_page(after_id=after, limit=PAGE_SIZE)
    if not ids: break
    with SessionLocal() as db:
        classes={r.id:(r.platform or 'unknown',r.model or 'unknown') for r in db.execute(
            select(Device.id,Device.platform,Device.model).where(Device.id.in_(ids)))}
    for device_id in ids:
        result=_process_one_device(device_id,dry_run=True)
        assert result['status'] in ('ok','occupied_debt_recorded','retired_skipped','not_found'), result['status']
        for family, entry in result.get('families',{}).items():
            totals[(family,entry['state'],*classes[device_id])]+=1
    after=ids[-1]
print(json.dumps([dict(family=k[0],state=k[1],platform=k[2],model=k[3],count=n)
                  for k,n in sorted(totals.items())],ensure_ascii=False))
PY
```

预期：CLI exit=0、failed=0、conflict_exhausted=0，输出本次各专项/状态/平台/型号数量。核验者不得把历史 146 台当作本次预期。
平台/型号汇总不证明权限模型、APK SHA 等可信组合已精确匹配；组合未核验项单列，交 Owner 决定放行证据口径。
审计预期：dry-run 逐设备 rollback，无初始化条目/租约提交、无专门 init 审计；日志、计数与前后只读状态对账留证。
**停止：** 任一脚本非零、冲突耗尽、计数无法解释或超出 Owner 预期。初始化执行者不得进入 apply。

### 14. Owner 确认点 3：apply 与组合不可信设备的放行口径

**执行者：Owner 确认，初始化执行者操作。** Owner 审阅第 13 步最新计数，确认 apply 与允许的人工放行证据类别；执行者记录引用。

> 警告：apply 会建立欠账占用，并触发 Agent 自动解除；误放行会让未确认归还的设备重新获派。执行者必须先取得本确认。核验者不得把 executed_unverified 当成已清偿。执行者不得按历史数量批量放行。

```bash
cd "$B6_RELEASE"
venv/bin/python -m backend.scripts.init_device_return --apply \
  > "$B6_EVIDENCE_DIR/init-apply.log" 2>&1
```

```sql
BEGIN READ ONLY;
SELECT d.id, count(DISTINCT dl.id) AS active_leases
FROM device d JOIN device_return_debt debt ON debt.device_id=d.id AND debt.state='open'
LEFT JOIN device_leases dl ON dl.device_id=d.id AND dl.status='ACTIVE'
WHERE d.retired_at IS NULL GROUP BY d.id HAVING count(DISTINCT dl.id)<>1;
SELECT dl.device_id, dl.lease_type, dl.holder, dl.reason FROM device_leases dl
WHERE dl.status='ACTIVE' AND dl.lease_type='MAINTENANCE'
ORDER BY dl.device_id;
ROLLBACK;
```

预期：CLI exit=0、failed/conflict_exhausted=0；第一条查询 0 行，欠账租约 holder=platform、reason=归还欠账。
apply 后自动清偿可能立即关闭部分条目；只核对仍有 open 条目的设备。核验者不得把正常释放误判为少租约。
审计预期：初始化没有专门 init 审计行；device.return_state 的 stock 来源、stock 条目、创建时间与 CLI 回执是事实证据。
人工放行另按[开关机手册 §5.3](./powercycle-chain-disarm-runbook.md#53-人工放行与后续-init自-b6-激活起生效)调用既有接口，预期 device_return_override 审计、unverified/override 与关闭原因 overridden。
**停止：** 未确认、CLI 非零、任何未退役欠账设备租约数不为 1、载体标志不符或放行证据不满足口径。

### 15. 等待一轮清偿并统计剩余欠账

**执行者：核验者。** 等待现有心跳/重连清偿，读取本次结果。执行者不得创建额外清理 PlanRun：

```sql
BEGIN READ ONLY;
SELECT debt.family, debt.state, debt.closed_reason, d.platform, d.model, count(*)
FROM device_return_debt debt JOIN device d ON d.id=debt.device_id
GROUP BY debt.family,debt.state,debt.closed_reason,d.platform,d.model ORDER BY 1,2,3,4,5;
SELECT d.id, x.key AS family, x.value->>'state' AS state, x.value->>'source' AS source,
       x.value->>'updated_at' AS updated_at
FROM device d CROSS JOIN LATERAL jsonb_each(COALESCE(d.return_state,'{}'::jsonb)) x
WHERE x.value->>'source' IN ('discharge','override') ORDER BY d.id,x.key;
SELECT family, count(*) AS open_count, min(created_at) AS oldest
FROM device_return_debt WHERE state='open' GROUP BY family ORDER BY family;
ROLLBACK;
```

预期：可信登记组合的 power-cycle 解除结果为 confirmed，专项状态 returned、来源 discharge、所涉条目关闭原因 discharged；部分专项仍 open 时共用租约保留。
未验证组合的 executed_unverified 不关闭欠账；长期不可达、未接入专项与组合不可信分别列出。核验者不得把这些类别合并记为“清偿成功”。
清偿成功不写 Agent 日志；失败重试间隔至少 30 分钟，executed_unverified 等待重连/token 变化，409 丢弃旧 token，查法见开关机手册 §5.2。
审计预期：成功清偿没有专门成功审计，以上状态/条目是回查依据；被拒的特权探针结果有 unverified_probe_result，人工放行有 device_return_override（附录 A）。
**停止：** 可信组合的欠账未按预期关闭、状态/条目/租约不一致、剩余数无法解释；先报告 Owner。执行者不得为恢复 schedule 直接释放欠账租约。

### 16. 恢复原 schedule，允许正常触发

**执行者：执行者。** 第 11–15 步闸门通过后，只恢复第 4 步前原启用清单；先 GET，避免重复 toggle：

```bash
B6_RESUMED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '%s\n' "$B6_RESUMED_AT" > "$B6_EVIDENCE_DIR/dispatch-resumed-at.txt"
jq -r '.[].id' "$B6_EVIDENCE_DIR/enabled-schedules.json" | while read -r B6_SCHEDULE_ID; do
  B6_STATE=$(curl --fail-with-body -sS -H "$AUTH" \
    "http://127.0.0.1:8000/api/v1/schedules/$B6_SCHEDULE_ID") || exit 1
  if [ "$(printf '%s' "$B6_STATE" | jq -r '.enabled')" = false ]; then
    curl --fail-with-body -sS -X POST -H "$AUTH" \
      "http://127.0.0.1:8000/api/v1/schedules/$B6_SCHEDULE_ID/toggle" \
      | tee "$B6_EVIDENCE_DIR/schedule-$B6_SCHEDULE_ID-restored.json" | jq -e '.enabled == true' || exit 1
  fi
done
```

预期：原清单 enabled=true，next_run_at 已重算；原本关闭的 schedule 继续关闭。执行者在恢复完成后通知可以手工触发 PlanRun。
审计预期：toggle 无专门审计；GET/第 3 步 SQL、逐项回执与恢复通知是回查证据。
**停止：** 有未通过/未隔离的主机、初始化/清偿异常尚未裁决、HTTP 非 200 或恢复清单不符；保留暂停状态并报告。

### 17. L0/L1 只读核验，Owner 确认点 4 后做 L2，最后 L3

**执行者：核验者；L2 由 Owner 指定执行者与独立设备。** L0 重读 SOP 的运行 bundle/前端入口与第 7 步 schema，均绑定本次激活 SHA。
先用第 10 步的只读计算重取 `B6_CATALOG_VERSION`，再用以下 GET 读取同步状态；站点/各主机包 SHA 用第 10、11 步的完整只读命令复核：

```bash
curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/hosts?limit=200' \
  | jq -e '.total<=200 and
    all(.items[] | select(.status=="ONLINE"); .agent_code_sync_status=="matched")'
```

重开 §0 的 psql 入口，传入重取的 `B6_CATALOG_VERSION`，只读核对目录版本：

```sql
BEGIN READ ONLY;
SELECT id, status, script_catalog_version FROM host
WHERE retired_at IS NULL AND status='ONLINE'
  AND script_catalog_version IS DISTINCT FROM :'b6_catalog_version'
ORDER BY id;
ROLLBACK;
```

预期：GET 的 matched 断言成功，SQL 为 0 行；任何未退役 ONLINE 主机的 NULL 或不同版本都使本步停止。

L1 查询第 16 步之后首批真实 job 的 init 检查。核验者不得额外派发来伪造只读证据：

```sql
BEGIN READ ONLY;
SELECT DISTINCT ON (ji.id) ji.id AS job_id, ji.device_id, st.status, st.original_ts, st.exit_code
FROM step_trace st JOIN job_instance ji ON ji.id=st.job_id
WHERE st.stage='init' AND st.step_id='return_check.power-cycle'
  AND ji.created_at>=:'b6_resumed_at'::timestamptz
ORDER BY ji.id DESC,st.original_ts DESC,st.id DESC LIMIT 50;
SELECT id, status, disarm_results FROM job_instance
WHERE created_at>=:'b6_resumed_at'::timestamptz AND ended_at IS NOT NULL ORDER BY id DESC LIMIT 50;
ROLLBACK;
```

也可读单个 job 的最新步骤与未关闭条目：

```bash
B6_JOB_ID='<首批真实job整数ID>'
B6_DEVICE_ID='<该job设备整数ID>'
curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/runs/$B6_JOB_ID/steps" \
  | jq '.[] | select(.name=="return_check.power-cycle") | {name,phase,status,exit_code}'
curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/devices/$B6_DEVICE_ID/return-debts"
```

预期：init 最新 COMPLETED 表示通过，FAILED 是失败；没有首批样本则 L1 pending。真实成功的 init/清偿证据按可信组合解释。核验者不得从 job 绿态推断 returned。
Owner 在确认点 4 明确独立设备 id 清单、计划/场景与边界，执行者此前不得执行 L2。
L2 按 #3723 §5 第 6 条验证同时中止/重启窗口、已有残留、欠账阻断 claim、重连清偿、放行后 init、退役/取消退役。执行者不得扩大到未批准的设备。
L3 引用 U1–U5/U8 的既有独立复核与最终有限红绿证据。核验者不得用旧 SHA 结论代签新的回归或运行证据。
审计预期：L0/L1 GET/SELECT 无新增业务审计；L2 按实际动作回查 abort_plan_run、device_return_override、retire_device/unretire_device、unverified_probe_result 及 job/trace/条目事实。
**停止：** SHA/协议/能力不一致、init 或回读异常、Owner 未给 L2 清单、L2 场景失败、任一层仍 pending。核验者不得宣布整批验收完成。NX2 的 #3761 保持开放。

### 18. 生效证据、文档收尾与关单

**执行者：执行者汇报，文档实施者开后续 PR，规划者关单。** 先在 #3723 发布完整生效证据：SHA、18 步结果、四次确认、迁移/scan、发布八个旧包、主机能力、初始化/清偿与 L0–L3。
激活并贴出生效证据后，另开文档收尾 PR 删除开关机手册的过渡内容与各文档的激活前分支，按 ADR-0059 写作并保留历史链接。
命令使用现有 GitHub PR 流程；收尾 PR 核对通过并合入、全部验收通过后，规划者按以下顺序关单：

```bash
for B6_ISSUE in 3726 3727 3728 3729 3730 3731 3732 3752; do
  gh issue close "$B6_ISSUE" --repo DUElost/stability-test-platform
done
gh issue close 3723 --repo DUElost/stability-test-platform
gh issue view 3761 --repo DUElost/stability-test-platform --json number,state
```

预期：单元先关闭，批次最后关闭；#3761 仍 OPEN。本清单 PR 与上一个 U7 PR 均用 Refs。实施者不得在 PR 中预先自动关闭 issue。
审计预期：GitHub 生效评论、文档收尾 PR、issue 关闭事件与执行证据；无新增业务库审计。
**停止：** 没有生效证据、四确认缺失、任何验收 pending、文档收尾未合入。规划者不得提前关 #3732/#3723。

## 2. 回退与统一停止条件

Owner 决定回退后，执行者必须先停止新归还工作。如果第 16 步已恢复，复用第 4–6 步暂停与排空。执行者不得让旧消费者接管依赖新协议的在跑 job。

- **Agent 故障**：按 SOP §5 与其引用的回退就绪审计执行 Agent 回退；不复制通用回退命令。预期 hot_update_result/主机摘要可回查。
  新控制面兼容旧 Agent，但旧 Agent 没有 init 检查/清偿能力，已有欠账继续占用；B6 验收停止。
- **控制面故障**：只前滚修复。执行者不得指回旧 rev。执行者不得运行 alembic downgrade。执行者不得删除 guard 列/原始证据。迁移或 health 失败时停在第 7 步。
- **欠账**：日常出口只为清偿或按确认口径人工放行。`init_device_return --release-all-debt-leases` 不属于本清单的日常执行步骤，只有 Owner 另行明确授权后执行者才可以使用；该路径预期 release_all_debt_leases 审计。核验者不得把释放租约记为 confirmed。
- **立即停止**：health/迁移失败；scan 冲突或缺包超出 #3661；canary 非 matched、当前 disarm_protocol_unavailable、目录/SHA 不符；dry-run 超出 Owner 预期；Owner 确认缺失；在途 job 未排空。

每次停止时，执行者必须保留已完成/未完成清单、原始错误、状态快照与下一步待 Owner 决定事项。执行者不得把部分完成记为激活完成。

## 附录 A. 审计与事件的只读回查

以下查询在确认点 1 之后使用，仅取审计分类与必要字段，不输出可能含 token 的完整 details：

```sql
BEGIN READ ONLY;
SELECT id, timestamp, action, resource_type, resource_id,
       details->>'host_id' AS host_id, details->>'entry' AS entry,
       details->>'ok' AS ok, details->>'converged' AS converged,
       details->>'closed_count' AS closed_count, details->>'reason_category' AS reason_category
FROM audit_logs
WHERE timestamp>=:'b6_window_at'::timestamptz AND action IN
 ('scan','hot_update','hot_update_result','abort_plan_run','job_batch_terminalized',
  'plan_updated','device_return_override','unverified_probe_result',
  'retire_device','unretire_device','release_all_debt_leases')
ORDER BY id;
ROLLBACK;
```

执行者不得为没有专门审计行的步骤补写假审计。核验者必须按事实来源回查：发包看 SHA/文件与工具回执，toggle 看状态和 HTTP 回执，初始化看 stock 状态/条目/租约，清偿看 discharge 与 discharged，部署看 manifest/unit/health。
业务语义与入口索引见[执行协议 §9](../design/07-execution-protocol.md#9-b6-执行与设备归还自-b6-激活起生效)。
