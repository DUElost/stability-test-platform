# B7 GPU 设备归还激活清单

> **状态：未执行，待 U3 独立复核与 Owner 激活授权。最后更新：2026-10-28。**
> 权威：[#3809 当前正文 v1.5](https://github.com/DUElost/stability-test-platform/issues/3809)，
> [U3 验收裁定](https://github.com/DUElost/stability-test-platform/issues/3809#issuecomment-6444209519)。
> 方法：[分层验收](../development/ai/methods/batch-activation-acceptance.md)。
> 参照：[B6 激活清单](./2026-10-25-b6-device-return-activation-runbook.md)。

本文件是待执行清单，命令未在本单元的实施中运行。代码合入、包已登记、CI 成功与生产生效是不同事实。
本批没有新增迁移、表、状态或 env；最终集成 SHA 如携带其他迁移，由部署执行者按部署 SOP 另行报告 Owner。

## 执行约定

> 警告：第 2–4 步改变生产状态，第 6 步 L2 改变设备状态。执行者必须先取得 Owner 对本次窗口、
> 切换、发布、scan、Agent 热更新和 L2 的明确授权。执行者不得把本文件或 B6 的历史授权当成本次授权。
> 第 5 步只产出计划清单和预算，执行者不得据此重指计划。

执行者必须按第 0–6 步顺序执行。核验者必须在每一步保存时间、实际命令、退出码、闸门结果和 Owner 确认引用。
执行者遇到停止条件必须停止后续步骤并报告 Owner。核验者不得把 pending、skip、读失败或部分成功记为通过。

通用部署细节沿用 [control-plane-deploy SOP](../../.claude/skills/control-plane-deploy/SKILL.md)，
发包细节沿用 [script-version-lifecycle SOP](../../.claude/skills/script-version-lifecycle/SKILL.md)。
本清单的第 3 步先发布后 scan，覆盖通用部署 SOP 的首次 scan 顺序。

按顺序设置本次窗口变量：

```bash
set -euo pipefail
umask 077
B7_REPO=/home/debian13/stability-test-platform
B7_RELEASE=/home/debian13/stp-releases/current
B7_EVIDENCE_DIR='/home/debian13/stp-evidence/b7-<本次窗口编号>'
B7_U3_PR='<U3合入PR号>'
mkdir -p "$B7_EVIDENCE_DIR"
```

核验者必须按 [production-diagnostics](./production-diagnostics.md) 从站点配置确认 PGHOST、PGPORT、PGDATABASE。
核验者必须使用操作者提供的 `stp_ro` 身份与口令来源。核验者不得复用应用账号或仓根 env。
核验者可以用下列入口执行本文 SQL；执行者不得把连接串、token、SSH 清单或完整序列号写进 GitHub 摘要：

```bash
b7_ro() {
  PGUSER=stp_ro PGAPPNAME=diag-b7-activation \
    PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=30000' \
    psql -X -v ON_ERROR_STOP=1 "$@"
}
b7_prod() (
  cd "$B7_RELEASE"
  set -a
  . ./.env.backend
  set +a
  "$@"
)
```

`b7_prod` 只供已授权的部署、发包与热更新执行者使用。API 的 `AUTH` 按部署 SOP §0 在发布根取得，
保存在当前 shell，不打印或落盘。核验者必须先用以下只读查询核对后续 SQL 的表、列与枚举：

```sql
BEGIN READ ONLY;
SELECT table_name, column_name, data_type FROM information_schema.columns
WHERE table_schema='public' AND table_name IN
 ('device','host','device_leases','job_instance','plan','plan_step','script',
  'plan_run','step_trace','device_return_debt','task_schedules')
ORDER BY table_name, ordinal_position;
SELECT t.typname,e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid=t.oid
ORDER BY t.typname,e.enumsortorder;
ROLLBACK;
```

停止条件：目标、只读身份或 schema 无法确认。回退：无生产写入，保留缺口报告。

## 第 0 步：固定最终集成 HEAD 与完整测试

1. 读取 U1、U2、U3 的复核评论及合入事实：

   ```bash
   cd "$B7_REPO"
   git fetch origin main
   B7_ACTIVATION_SHA=$(git rev-parse origin/main)
   for B7_PR in 3815 3818 "$B7_U3_PR"; do
     gh pr view "$B7_PR" --repo DUElost/stability-test-platform \
       --json number,state,headRefOid,mergeCommit,reviews,comments \
       > "$B7_EVIDENCE_DIR/pr-$B7_PR.json"
     jq -e '.state=="MERGED" and .mergeCommit.oid!=null' "$B7_EVIDENCE_DIR/pr-$B7_PR.json"
     git merge-base --is-ancestor \
       "$(jq -r .mergeCommit.oid "$B7_EVIDENCE_DIR/pr-$B7_PR.json")" "$B7_ACTIVATION_SHA"
   done
   printf '%s\n' "$B7_ACTIVATION_SHA" > "$B7_EVIDENCE_DIR/activation-sha.txt"
   ```

2. 如果任一必须复核单元没有绑定其最终 head 的「通过」结论，停止本流程。
   核验者必须从上述 comments/reviews 核对结论，不能只看合入状态。

3. 在最终集成 SHA 上运行完整 CI：

   ```bash
   gh workflow run ci.yml --ref main --repo DUElost/stability-test-platform
   gh run list --repo DUElost/stability-test-platform --workflow ci.yml \
     --event workflow_dispatch --commit "$B7_ACTIVATION_SHA" --limit 20 \
     --json databaseId,headSha,status,conclusion
   B7_FULL_CI_ID='<上面同SHA运行的ID>'
   gh run watch "$B7_FULL_CI_ID" --repo DUElost/stability-test-platform --exit-status
   gh run view "$B7_FULL_CI_ID" --repo DUElost/stability-test-platform \
     --json headSha,conclusion,jobs > "$B7_EVIDENCE_DIR/full-ci.json"
   jq -e --arg sha "$B7_ACTIVATION_SHA" '
     .headSha==$sha and .conclusion=="success" and
     any(.jobs[]; .name=="backend-test" and .conclusion=="success" and
       any(.steps[]; .name=="Run backend tests" and .conclusion=="success") and
       any(.steps[]; .name=="Run agent tests" and .conclusion=="success") and
       any(.steps[]; .name=="Run repo-level tests" and .conclusion=="success"))
   ' "$B7_EVIDENCE_DIR/full-ci.json"
   git fetch origin main
   test "$(git rev-parse origin/main)" = "$B7_ACTIVATION_SHA"
   ```

   预期：完整 backend、Agent、根 tests 均实际执行成功；六项 required checks 也分别成功。
   核验者必须核对日志中的实际测试汇总；PR CI 不替代集成 HEAD 的完整 CI。

停止条件：未合入、缺复核、SHA 前进、测试失败/跳过/未结束。回退：仅 GitHub Actions 记录，无生产变更；重新冻结并测试。

## 第 1 步：只读盘点 GPU 残留并报告 Owner

> 警告：盘点不清理设备。执行者不得调用 disarm、重跑初始化、修改设置、删脚本或卸载 APK。
> 裁决 A 继续有效：已有 GPU 存量条目保持原状，残留由后续 job 的 init 检查处理。

1. 查询在役设备及占用，保存受限原始清单：

   ```sql
   BEGIN READ ONLY;
   SELECT d.id,d.host_id,d.serial,d.platform,d.model,d.status,d.adb_connected,
          h.status AS host_status,d.return_state->'gpu' AS gpu_return_state,
          EXISTS(SELECT 1 FROM device_leases l
                 WHERE l.device_id=d.id AND l.status='ACTIVE') AS has_active_lease,
          EXISTS(SELECT 1 FROM job_instance j WHERE j.device_id=d.id
                 AND j.status IN ('PENDING','RUNNING','UNKNOWN')) AS has_active_job
   FROM device d JOIN host h ON h.id=d.host_id
   WHERE d.retired_at IS NULL AND h.retired_at IS NULL ORDER BY d.host_id,d.id;
   SELECT state,count(*) FROM device_return_debt WHERE family='gpu' GROUP BY state;
   ROLLBACK;
   ```

2. 根据第 1 项和既有 SSH 配置准备 `inventory-targets.tsv`，每行是 device_id、serial、SSH 目标，制表符分隔。
   核验者必须把离线、无 SSH、占用中的设备单列为未读取；不得把未读取当作 clear。
   核验者必须对账清单总数与 SQL 行数；公开报告只包含 device_id 和分组计数。

3. 在控制面受限证据目录保存下面的只读程序为 `gpu-inventory.py`：

   ```python
   import json, re, subprocess, sys
   device_id, serial = int(sys.argv[1]), sys.argv[2]
   def run(args):
       try:
           p = subprocess.run(args, capture_output=True, text=True,
                              errors="replace", timeout=15)
           return p.returncode, p.stdout.strip(), p.stderr.strip()
       except (OSError, subprocess.TimeoutExpired):
           return -1, "", "read_failed"
   rc, listing, err = run(["adb", "devices", "-l"])
   matches = [line for line in listing.splitlines()
              if line.split()[:2] == [serial, "device"]]
   transports = [re.search(r"transport_id:(\d+)", line) for line in matches]
   result = {"device_id": device_id, "readback": "readback_failed", "reads": {}}
   if rc == 0 and len(transports) == 1 and transports[0]:
       prefix = ["adb", "-t", transports[0].group(1), "shell"]
       commands = {
           "boot": "getprop sys.boot_completed",
           "loop": "pgrep -f '[g]pu_stress_loop.sh'",
           "instrument": "pgrep -f '[T]estStressGpu|[A]ndroidJUnitRunner'",
           "file": "if [ -e /sdcard/Auto/gpu_stress_loop.sh ] || [ -L /sdcard/Auto/gpu_stress_loop.sh ]; then echo PRESENT; elif [ -r /sdcard ] && [ -x /sdcard ] && { [ ! -d /sdcard/Auto ] || { [ -r /sdcard/Auto ] && [ -x /sdcard/Auto ]; }; }; then echo ABSENT; else exit 2; fi",
           "airplane": "settings get global airplane_mode_on",
           "wifi": "cmd wifi status",
       }
       reads = {k: run(prefix + [v]) for k, v in commands.items()}
       # Do not retain PID lists or raw command output in the public summary.
       values = {}
       for k, (code, out, error) in reads.items():
           value = None
           if k in ("loop", "instrument"):
               if code == 0 and out and all(x.isdigit() and int(x)>0 for x in out.split()):
                   value = True
               elif code == 1 and not out and not error:
                   value = False
           elif code == 0:
               if k == "boot" and out == "1": value = True
               if k == "file" and out in ("PRESENT", "ABSENT"): value = out == "PRESENT"
               if k == "airplane" and out in ("0", "1"): value = out == "1"
               if k == "wifi":
                   states = re.findall(r"^(?:Wi-Fi|Wifi) is (enabled|disabled)\s*$", out, re.M)
                   if len(states) == 1: value = states[0] == "enabled"
           values[k] = value
           result["reads"][k] = {"rc": code, "value": value}
       if all(v is not None for v in values.values()):
           residue = any(values[k] for k in ("loop", "instrument", "file", "airplane")) or not values["wifi"]
           result["readback"] = "footprint" if residue else "clear"
   print(json.dumps(result))
   ```

4. 对清单中已确认无占用、可访问的设备逐台运行只读程序：

   ```bash
   : > "$B7_EVIDENCE_DIR/gpu-inventory.ndjson"
   while IFS=$'\t' read -r B7_DEVICE_ID B7_SERIAL B7_SSH_TARGET; do
     [[ "$B7_DEVICE_ID" =~ ^[0-9]+$ && "$B7_SERIAL" =~ ^[A-Za-z0-9._:-]+$ ]]
     ssh "$B7_SSH_TARGET" /opt/stability-test-agent/venv/bin/python - \
       "$B7_DEVICE_ID" "$B7_SERIAL" < "$B7_EVIDENCE_DIR/gpu-inventory.py" \
       >> "$B7_EVIDENCE_DIR/gpu-inventory.ndjson"
   done < "$B7_EVIDENCE_DIR/inventory-targets.tsv"
   jq -s 'group_by(.readback) | map({readback:.[0].readback,count:length})' \
     "$B7_EVIDENCE_DIR/gpu-inventory.ndjson"
   ```

   预期：每台一条事实快照；进程检查用括号模式，不匹配查询者自身；序列号重复或 transport 无法唯一定位时为读取失败。
   核验者必须在读取前重查第 1 项占用；新占用设备移入未读取清单。

5. 向 Owner 报告：窗口时间、覆盖的设备总数、clear/footprint/readback_failed/未读取台数，
   循环进程/instrument/脚本/飞行模式/Wi-Fi 各项残留数，按平台与原始 model 分组的计数，
   以及存量状态、欠账与后续 init 处理方式。核验者必须保留每个 device_id 的分类，避免同一设备多项残留重复计入总台数。

   报告格式：

   ```text
   观测窗口 / 站点 / 操作者 / 证据引用：
   在役总数 / 已读 / clear / footprint / readback_failed / 未读取：
   足迹分项：loop / instrument / file / airplane_on / wifi_disabled（各为去重设备数）
   平台 × model：各组总数与上述分类计数
   未读取原因：占用 / 主机离线 / 设备离线 / SSH失败 / transport不唯一
   存量归还状态 / GPU欠账数 / 后续init处理说明：
   Owner收到报告与未知项处置裁定的引用：
   ```

停止条件：查询/SSH 失败、清单不完整、读失败或未读取项未经 Owner 裁定，或 Owner 未收到报告。
回退：无设备写入，无需恢复；保留未知项，不主动清理或重新初始化。

## 第 2 步：部署控制面

> 警告：共享契约登记随控制面部署进入新的判定路径。执行者必须先按部署 SOP §0.5 阶段 A
> 报告随行变更及新增迁移，并取得 Owner 本次窗口授权。执行者不得部署未合入的 U3 head。

1. 按部署 SOP §0.5 阶段 A 第 1–14 项准备已测的最终 main、前端和 bundle。
   以下命令用于绑定构建源与记录基线，bundle 的 venv/物料按 SOP §1 第 3 项准备：

   ```bash
   B7_BASE=$(jq -r .source.revision "$B7_RELEASE/release-manifest.json")
   cd "$B7_REPO"
   git status --short --branch
   git pull --ff-only origin main
   test "$(git rev-parse HEAD)" = "$B7_ACTIVATION_SHA"
   b7_prod sh -c 'cd "$1"; exec ./tools/dev/check-deploy-source.sh' sh "$B7_REPO"
   b7_prod "$B7_REPO/scripts/project_python.sh" tools/dev/check-deploy-readiness.py --diff-base "$B7_BASE"
   git diff --name-only --diff-filter=A "$B7_BASE" "$B7_ACTIVATION_SHA" -- backend/alembic/versions/
   B7_NEW_RELEASE='/home/debian13/stp-releases/<本次新rev>'
   test ! -e "$B7_NEW_RELEASE"
   ./scripts/project_python.sh tools/release/build_bundle.py --repo-root . --out "$B7_NEW_RELEASE"
   test "$(jq -r .source.revision "$B7_NEW_RELEASE/release-manifest.json")" = "$B7_ACTIVATION_SHA"
   ```

2. 如果源不干净、不在 main、存在未解除的 DO-NOT-SWITCH、SHA 不同或物料/就绪检查失败，停止本流程。

3. 在 Owner 确认的维护窗口内，按 B6 清单第 3–6 步暂停新派发并复查在途运行。
   核验者必须使用下列只读查询核对排空；执行者不得沿用历史“已中止”快照：

   ```sql
   BEGIN READ ONLY;
   SELECT id,plan_id,status FROM plan_run WHERE status IN ('QUEUED','PRECHECK','RUNNING') ORDER BY id;
   SELECT id,plan_run_id,host_id,device_id,status FROM job_instance
   WHERE status IN ('PENDING','RUNNING','UNKNOWN') ORDER BY id;
   ROLLBACK;
   ```

4. 如果存在在途运行或新派发未暂停，停止本流程，交 Owner 选择等待或另行批准中止。
5. 按部署 SOP §0.5 阶段 B 的切换步骤执行，停在 scan 之前：

   ```bash
   B7_OLD_RELEASE=$(readlink -f "$B7_RELEASE")
   ln -sfn "$B7_NEW_RELEASE" /home/debian13/stp-releases/current.tmp
   mv -Tf /home/debian13/stp-releases/current.tmp "$B7_RELEASE"
   sudo systemctl restart stability-backend
   systemctl is-active stability-backend
   timeout 30 sh -c 'until curl -sf http://127.0.0.1:8000/health; do sleep 1; done'
   ```

   预期：current 的 source.revision 为激活 SHA，服务 active、health 200，unit 的迁移和 schema-at-head 检查成功。
   前端如随本次部署更新，执行者必须按部署 SOP §1.5 从独立构建目录完成双 rename 与 nginx reload，保留旧入口与备份目录。

停止条件：健康/迁移/前端失败或 bundle SHA 不同。回退：先停止后续激活；无新增迁移且 Owner 确认兼容时，
按上述 ln/mv/restart 将 current 指回 `B7_OLD_RELEASE`，前端按 SOP §1.5 双 rename 回退。
如已执行新迁移或存在 B6 revision 兼容限制，执行者必须保留新 bundle，只前滚修复，不得 downgrade 或擅自指回旧 rev。

## 第 3 步：发布两个 GPU 版本后 scan

> 警告：`--publish` 的现有 CLI 会遍历每族最新版本。执行者必须先核对其他族的最新包均已在位且同字节，
> 从而把本次新增发布限制为 GPU 两个版本。执行者不得覆盖已有异字节包、修改 manifest 或用 force_rebaseline 消除冲突。

1. 按站点环境解析包源，并核对与 scan 的默认包源一致：

   ```bash
   B7_PACKAGES_ROOT=$(b7_prod venv/bin/python - <<'PY'
   from backend.services.script_catalog import default_packages_root
   root = default_packages_root()
   assert root is not None
   print(root)
   PY
   )
   cd "$B7_REPO"
   test "$(git rev-parse HEAD)" = "$B7_ACTIVATION_SHA"
   ./scripts/project_python.sh tools/dev/check_script_packages.py
   ./scripts/project_python.sh - "$B7_PACKAGES_ROOT" <<'PY'
   import hashlib, json, sys
   from pathlib import Path
   from tools.dev.check_script_packages import latest_entry, script_families
   doc = json.loads(Path('tool_manifest.json').read_text())
   expected = {
       'gpu_disarm': ('1.0.0', 'c464779d695402473bf5afb173bc743d68eac307a680c46bfec2ebbf4299ead7'),
       'gpu_finish': ('1.1.0', '3056b659c802df8ebf2c47fe76c3c82af0a578f8e5dee1e4c92cec802fd7d88e'),
   }
   missing = []
   for name, versions in script_families(doc).items():
       row = latest_entry(versions)
       if row is None: continue
       if name in expected:
           assert (row['version'], row['package_sha256']) == expected[name]
       path = Path(sys.argv[1])/name/(row['version']+'.tar.gz')
       if path.exists():
           assert hashlib.sha256(path.read_bytes()).hexdigest() == row['package_sha256'], name
       else:
           assert name in expected, 'out-of-scope missing package: '+name
           missing.append(name+'@'+row['version'])
   print({'new_publications': missing})
   PY
   ```

2. 发布站点包：

   ```bash
   ./scripts/project_python.sh tools/dev/check_script_packages.py --publish --packages-root "$B7_PACKAGES_ROOT"
   sha256sum "$B7_PACKAGES_ROOT/gpu_disarm/1.0.0.tar.gz" "$B7_PACKAGES_ROOT/gpu_finish/1.1.0.tar.gz"
   ```

   预期：只新增上述两个 GPU 包，已有同字节包跳过；两个 SHA 分别为上面 expected 的值。
   核验者必须记录实际新落位列表，不能只看命令退出 0。

3. 按部署 SOP §0 重新取得 AUTH 后 scan：

   ```bash
   curl --fail-with-body -sS -H "$AUTH" -X POST http://127.0.0.1:8000/api/v1/scripts/scan \
     > "$B7_EVIDENCE_DIR/scan.json"
   jq '.data | {created,skipped,conflicts,package_missing,package_conflicts}' "$B7_EVIDENCE_DIR/scan.json"
   curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/scripts?is_active=true' \
     > "$B7_EVIDENCE_DIR/active-scripts.json"
   jq -e '.data | any(.[]; .name=="gpu_disarm" and .version=="1.0.0" and
     .package_sha256=="c464779d695402473bf5afb173bc743d68eac307a680c46bfec2ebbf4299ead7") and
     any(.[]; .name=="gpu_finish" and .version=="1.1.0" and
     .package_sha256=="3056b659c802df8ebf2c47fe76c3c82af0a578f8e5dee1e4c92cec802fd7d88e")' \
     "$B7_EVIDENCE_DIR/active-scripts.json"
   ```

   预期：两个版本 active 且 SHA 相符，conflicts/package_conflicts/package_missing 均为空；created=0 可以是幂等结果。

停止条件：其他族缺包、包 SHA 不符、scan 失败/冲突/缺包或 active 记录不齐。回退：停止后续步骤，保留不可变包与登记；
发布和 scan 不自动重指旧计划，执行者不得删包、改版本、停用仍被引用的脚本。异常通过修复 PR 前滚。

## 第 4 步：Agent canary 后批量热更新

1. 按部署 SOP §0.5 阶段 C 第 1 项选 Owner 确认的空闲 ONLINE 主机，并保存详情：

   ```bash
   B7_CANARY_HOST_ID='<Owner确认的host_id>'
   curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/hosts/$B7_CANARY_HOST_ID" \
     | jq -e '.status=="ONLINE" and .active_job_count==0'
   ```

2. 对该主机执行 canary 热更新：

   ```bash
   curl --fail-with-body -sS -H "$AUTH" -X POST \
     "http://127.0.0.1:8000/api/v1/hosts/$B7_CANARY_HOST_ID/hot-update" \
     > "$B7_EVIDENCE_DIR/canary-update.json"
   jq -e '.ok==true' "$B7_EVIDENCE_DIR/canary-update.json"
   curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/hosts/$B7_CANARY_HOST_ID" \
     | jq '{status,agent_code_sync_status,last_heartbeat,active_job_count}'
   ```

   预期：ok=true、ONLINE/matched，心跳晚于更新完成，设备数无异常下降。
   核验者必须等待新心跳；digest-matched 的 no-op 也不免除该检查。

3. 通过既有 SSH 目标核对 canary 的共享契约文件与两个站点包 SHA：

   ```bash
   B7_CANARY_SSH='<既有SSH访问目标>'
   sha256sum "$B7_RELEASE/backend/agent/contracts/device_return.py"
   ssh "$B7_CANARY_SSH" 'sha256sum /opt/stability-test-agent/agent/contracts/device_return.py'
   ```

   在证据目录保存以下程序为 `host-b7-readonly.py`，然后经 SSH 标准输入运行：

   ```python
   import hashlib, json, os, sqlite3
   from pathlib import Path
   from dotenv import load_dotenv
   os.chdir('/opt/stability-test-agent')
   load_dotenv('.env', override=False)
   from agent.config import BASE_DIR
   from agent.tool_cache import _packages_root
   root = _packages_root(os.environ)
   assert root is not None
   expected = {
       'gpu_disarm/1.0.0.tar.gz': 'c464779d695402473bf5afb173bc743d68eac307a680c46bfec2ebbf4299ead7',
       'gpu_finish/1.1.0.tar.gz': '3056b659c802df8ebf2c47fe76c3c82af0a578f8e5dee1e4c92cec802fd7d88e',
   }
   for path, digest in expected.items():
       assert hashlib.sha256((root/path).read_bytes()).hexdigest() == digest
   with sqlite3.connect((BASE_DIR/'agent_state.db').as_uri()+'?mode=ro', uri=True) as db:
       row = db.execute('SELECT value FROM agent_state WHERE key=?',
                        ('return_init_check_available',)).fetchone()
   assert row and row[0] == '1'
   code = hashlib.sha256(Path('agent/contracts/device_return.py').read_bytes()).hexdigest()
   print(json.dumps({'packages': expected, 'contract_sha256': code, 'init_check_available': True}))
   ```

   ```bash
   ssh "$B7_CANARY_SSH" '/opt/stability-test-agent/venv/bin/python -' \
     < "$B7_EVIDENCE_DIR/host-b7-readonly.py" > "$B7_EVIDENCE_DIR/canary-packages.json"
   ```

   核验者不得调用 LocalDB.initialize 或用 script-presence 空目标集的全零结果代替实际包存在。
   主机包源不存在或包尚不可读时，执行者必须停止并报告，不能把“热更新成功”记为脚本到位。

4. canary 达到上述预期后，执行批量热更新：

   ```bash
   b7_prod venv/bin/python -m backend.scripts.batch_hot_update --direct \
     > "$B7_EVIDENCE_DIR/batch-hot-update.log" 2>&1
   tail -n 5 "$B7_EVIDENCE_DIR/batch-hot-update.log"
   curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/hosts?limit=200' \
     > "$B7_EVIDENCE_DIR/hosts-after.json"
   jq -e '.total<=200 and all(.items[] | select(.retired_at==null and .status=="ONLINE");
     .agent_code_sync_status=="matched")' "$B7_EVIDENCE_DIR/hosts-after.json"
   ```

   预期：SUMMARY fail=0。执行者必须保留 skipped 主机清单；忙碌主机按 SOP 等自然结束后补更，离线主机单列 pending。
   如同窗 diff 涉及 `agent_env_sync.py` 或站点同步键，执行者必须按部署 SOP §3 的判据报告并使用 `--force`。
   执行者不得加入 `--include-active` 或 `abort_running_jobs`。

5. 按 B6 清单第 11 步逐台核对在用 ONLINE 主机的包 SHA、共享契约文件 SHA 和 init-check 声明，核对目标集合与成功回执一一对应。

停止条件：canary 不健康/不 matched、心跳不新、文件或包 SHA 不同、协议不可用、批量失败/覆盖不完整。
回退：停止批量和新的 GPU 派发，保留欠账；由 Owner 按部署 SOP §5 的三项回退前检决定回退。
执行者不得直接改发布根 Agent 源码或让旧消费者接管在跑 job；修复通过 PR 与新 bundle 前滚。

## 第 5 步：列出待 Owner 确认的在用 GPU 计划与预算

> 警告：以下只有只读查询和候选报告。执行者不得在本步 PUT steps、重指 plan_step、创建计划、启用 schedule 或派发 PlanRun。

1. 查询全部 GPU 候选计划与实际步骤：

   ```sql
   BEGIN READ ONLY;
   SELECT p.id,p.name,p.updated_at,p.timeout_seconds,p.next_plan_id,
          ps.step_key,ps.script_name,ps.script_version,ps.stage,ps.sort_order,
          ps.enabled,ps.timeout_seconds AS step_wall,ps.stall_seconds,ps.retry,ps.params,
          sc.param_schema,sc.default_params
   FROM plan p JOIN plan_step ps ON ps.plan_id=p.id
   LEFT JOIN script sc ON sc.name=ps.script_name AND sc.version=ps.script_version
   WHERE EXISTS(SELECT 1 FROM plan_step g WHERE g.plan_id=p.id AND
     g.script_name IN ('gpu_setup','gpu_check','gpu_disarm','gpu_finish'))
   ORDER BY p.id,ps.stage,ps.sort_order,ps.id;
   SELECT id,plan_id,enabled,next_run_at FROM task_schedules ORDER BY plan_id,id;
   SELECT id,next_plan_id FROM plan WHERE next_plan_id IS NOT NULL ORDER BY id;
   SELECT id,plan_id,status FROM plan_run WHERE status IN ('QUEUED','PRECHECK','RUNNING') ORDER BY id;
   ROLLBACK;
   ```

2. 对每个候选计划读取完整详情：

   ```bash
   B7_PLAN_ID='<候选plan整数ID>'
   curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/plans/$B7_PLAN_ID" \
     > "$B7_EVIDENCE_DIR/plan-$B7_PLAN_ID-before.json"
   jq '.data | {id,name,updated_at,timeout_seconds,steps}' "$B7_EVIDENCE_DIR/plan-$B7_PLAN_ID-before.json"
   ```

3. 向 Owner 逐个列出 plan id、在用依据（启用 schedule/链引用/Owner 确认）、原 pin、拟 pin、步骤顺序、enabled、
   retry、stall_seconds、原预算与拟预算，附 before 快照及 updated_at。
   目标 teardown 是先 `gpu_disarm@1.0.0`，再 `gpu_finish@1.1.0`；其他步骤保持原意图。
   默认模板的解除墙钟为 1200 秒（等待设备 600 + 执行/回读预留 600），收取墙钟为 600 秒，retry 均为 1。
   核验者必须按 schema 默认值、Script.default_params、PlanStep.params 的覆盖顺序解析有效参数后计算解除预算；
   不得把默认 1200 当作所有计划的固定值。
   查询实际 callable 下限的命令是：

   ```bash
   cd "$B7_REPO"
   ./scripts/project_python.sh - <<'PY'
   from backend.agent.contracts.device_return import ONBOARDED
   minimum = ONBOARDED['gpu']['min_wall_clock_seconds']['gpu_disarm']
   print({'default_min_wall_seconds': minimum({}),
          'wait_900_min_wall_seconds': minimum({'wait_device_online_seconds': 900})})
   PY
   ```

   预期：默认 1200、wait=900 时 1500；报告还包含重试造成的总时长上界和计划预算影响。

停止条件：清单/预算不完整或 Owner 尚未逐个确认。回退：本步没有计划写入，before 快照仅用于后续获授权变更的对账。
重指执行与其回退由 Owner 单独确认的操作承担；本清单不提供写入命令。

## 第 6 步：L0–L3 分层验收

### L0：只读核对部署与能力

1. 重读运行 bundle 的 SHA、共享契约文件与两个站点/主机包 SHA，使用第 2–4 步的只读命令。
2. 读取 active script 目录并计算当前目录版本，再用 `b7_ro` 核对 ONLINE 主机上报值：

   ```bash
   curl --fail-with-body -sS -H "$AUTH" 'http://127.0.0.1:8000/api/v1/scripts?is_active=true' \
     > "$B7_EVIDENCE_DIR/l0-scripts.json"
   B7_CATALOG_VERSION=$(jq -c '[.data[] | [.name,.version,(.content_sha256//"")]] | sort' \
     "$B7_EVIDENCE_DIR/l0-scripts.json" | "$B7_REPO/scripts/project_python.sh" -c \
     'import hashlib,json,sys; print(hashlib.md5(json.dumps(json.load(sys.stdin)).encode()).hexdigest()[:12])')
   b7_ro -v catalog_version="$B7_CATALOG_VERSION" <<'SQL'
   BEGIN READ ONLY;
   SELECT id,status,script_catalog_version FROM host
   WHERE retired_at IS NULL AND status='ONLINE'
     AND script_catalog_version IS DISTINCT FROM :'catalog_version' ORDER BY id;
   ROLLBACK;
   SQL
   ```

   预期：SQL 为 0 行；任一 NULL 或不同版本都不通过。核验者必须核对在用 ONLINE 主机集合不为空并与第 4 步相同。

### L1：只读核对真实 init 与归还事实

1. 用 Owner 确认的生效开始时间查询真实 job，执行者不得额外派发来补只读样本：

   ```bash
   B7_ACTIVATED_AT='<本次生效ISO8601时刻>'
   b7_ro -v activated_at="$B7_ACTIVATED_AT" <<'SQL'
   BEGIN READ ONLY;
   SELECT DISTINCT ON (j.id) j.id AS job_id,j.device_id,st.step_id,st.status,st.exit_code,st.original_ts
   FROM step_trace st JOIN job_instance j ON j.id=st.job_id
   WHERE st.stage='init' AND st.step_id='return_check.gpu'
     AND j.created_at>=:'activated_at'::timestamptz
   ORDER BY j.id DESC,st.original_ts DESC,st.id DESC LIMIT 50;
   SELECT id,status,disarm_results->'gpu' AS gpu_result FROM job_instance
   WHERE created_at>=:'activated_at'::timestamptz AND ended_at IS NOT NULL ORDER BY id DESC LIMIT 50;
   SELECT d.id,d.platform,d.model,d.return_state->'gpu' AS gpu_return_state
   FROM device d WHERE d.retired_at IS NULL ORDER BY d.id;
   SELECT device_id,family,state,closed_reason FROM device_return_debt
   WHERE family='gpu' ORDER BY device_id,id;
   ROLLBACK;
   SQL
   ```

   预期：每个 job init 执行 GPU 检查；返回结果按六组合及实际 evidence 解释。
   MTK 的批准型号、权限、APK/包身份全部匹配才可以 confirmed；UNISOC、UNKNOWN 等表外组合不能升级为 confirmed。
   executed_unverified 不是 returned，也不关闭既有欠账。没有样本时 L1 为 pending。

### L2：Owner 指定设备的两类金丝雀

> 警告：执行者必须先取得 Owner 对设备 id、最短计划、时长/次数预算、占用和恢复责任人的确认。
> 执行者不得在非授权设备制造足迹、临时缩短解除墙钟、放行欠账或用 helper 代替真实 Engine。

1. 在 Owner 指定的 GPU 计划上，用平台计划页核对最短时长/次数配置，并准备仅含指定 device_ids 的触发请求。
   触发 schema 为 `PlanRunTrigger`，时长与次数来自已确认的计划，不是 run 请求的任意新字段：

   ```bash
   B7_L2_PLAN_ID='<Owner确认的金丝雀plan整数ID>'
   B7_L2_DEVICE_ID='<Owner确认的独立device整数ID>'
   jq -n --argjson id "$B7_L2_DEVICE_ID" \
     '{device_ids:[$id],note:"B7 GPU L2 approved canary"}' > "$B7_EVIDENCE_DIR/l2-trigger.json"
   curl --fail-with-body -sS -H "$AUTH" -H 'Content-Type: application/json' -X POST \
     --data-binary @"$B7_EVIDENCE_DIR/l2-trigger.json" \
     "http://127.0.0.1:8000/api/v1/plans/$B7_L2_PLAN_ID/run" \
     > "$B7_EVIDENCE_DIR/l2-trigger-result.json"
   ```

   预期：记录实际 PlanRun/job id、冻结步骤版本/包 SHA，前置回读包含 GPU 循环、instrument、脚本、飞行模式开和 Wi-Fi 关。
2. 在 GPU 实际运行后，按 Owner 批准中止该 PlanRun：

   ```bash
   B7_CANARY_RUN_ID='<本次批准中止的PlanRun整数ID>'
   curl --fail-with-body -sS -H "$AUTH" -H 'Content-Type: application/json' -X POST \
     -d '{"reason":"B7 GPU canary acceptance"}' \
     "http://127.0.0.1:8000/api/v1/plan-runs/$B7_CANARY_RUN_ID/abort"
   ```

   预期：真实 Engine 执行 gpu_disarm，未执行只收取的 gpu_finish 1.1.0；回读确认两个进程不在、脚本不在、飞行模式关、Wi-Fi 开。
   核验者必须用第 1 步的只读设备程序、L1 SQL、PlanRun 详情与 steps 回查实际终态和租约，HTTP 200 不代表解除完成。
3. 在另一台 Owner 指定、已存在 GPU 残留且无其他 job 占用的设备上进入下一个 job 的 init。
   预期：先保留残留前置读数，再从真实 return_check.gpu 的探测/解除 trace 和设备回读证明已处理残留，
   然后才运行该 job 的业务 init；核验者不得用干净设备替代此场景。
4. 查询两个金丝雀 job 的全部步骤，核对原始 evidence 与归还状态：

   ```bash
   B7_JOB_ID='<本次金丝雀job整数ID>'
   curl --fail-with-body -sS -H "$AUTH" "http://127.0.0.1:8000/api/v1/runs/$B7_JOB_ID/steps" \
     > "$B7_EVIDENCE_DIR/l2-job-$B7_JOB_ID-steps.json"
   ```

   核验者必须在证据中绑定 job、device、包 SHA、实际权限、前后五项回读、解除结果、终态与欠账。
   核验者不得因 Run 结束或租约释放就宣称设备已归还。

### L3：有限反例与独立复核

核验者必须引用最终集成 HEAD 的第 0 步完整测试，以及 U1/U2/U3 的独立复核和各自有限清单。
U3 的权限反例按 v1.5 裁定：单条隔离表上互换为 combination_unverified，完整表上互换为 confirmed，未知权限为 permission_unknown。
L3 不在生产制造负向故障；U2 的最终包真机矩阵和既有局限见 #3809 §3.3。

停止条件：任一层缺样本/pending、SHA/包/能力不符、回读失败、L2 场景失败或 Owner 未授权 L2。
回退：停止新的 GPU 派发，保留已产生的欠账与原始证据；设备恢复、计划回退与部署回退分别交 Owner 裁定。
执行者不得自动清债、重跑初始化或把读取失败当成已解除。

验收执行者必须在 #3809 汇总第 0–6 步实际结果、Owner 授权、同 SHA 测试、残留盘点、部署/包/机队证据、
待重指计划清单及 L0–L3 结果。后续关单由规划者与 Owner 按 ADR-0058 D9 处理，本单元不关 issue。

## 文档事实集中勘误

本节依据 U1 #3815、U2 #3818 合入后的代码。Owner 已确认 U3 保持原 scope；以下原文保留历史语境，
现行 GPU 操作以本节与本文激活闸门为准。

- [ADR-0061 §1.1](../adr/ADR-0061-device-return-contract.md#11-问题本质) 的 GPU 行把负责停止的脚本写成 gpu_finish：
  这是背景基线。现行登记的解除是 gpu_disarm 1.0.0；只有 gpu_finish 1.1.0 是只收取，旧 1.0.x 仍按承担解除处理。
- [ADR-0061 阶段 B 初始化规则](../adr/ADR-0061-device-return-contract.md) 将 GPU 残留举作未接入存量：
  这是 B6 阶段 B 的历史分类，不能推出 B7 代码仍未接入 GPU。B7 不重写已有 stock 条目，也不重跑初始化。
- [B6 规划 §3.0](../design/2026-b6-v17-planning-proposal.md) 的 gpu→gpu_finish、ONBOARDED 只有 power-cycle：
  这是 B6 的覆盖范围；当前 ONBOARDED 同时登记 power-cycle 与 gpu，GPU 的 disarm_onboarded 为 gpu_disarm。
- [B6 规划末尾预算观察](../design/2026-b6-v17-planning-proposal.md) 将 gpu_finish 举作未登记墙钟的例子：
  当前 GPU 解除墙钟登记在 gpu_disarm callable，默认 1200 秒；gpu_finish 1.1.0 的 600 秒是模板收取预算，二者不能互代。
- [testing.md §9](../development/testing.md#9-真机清理三态回归夹具2162) 把 gpu_finish 泛称清理入口并给出
  `teardown_cleanup_states.py --script gpu_finish`：该夹具仍调用旧 `_cleanup_device_script`，不适用于只收取的 1.1.0。
  执行者不得用该命令验收 B7；替代是 U2 的最终包矩阵、本文 L2 和 `test_gpu_disarm_b7_u2_3811.py`。
  夹具重构属于 #3809 §8 的后续事项，不在 U3 修改。

历史 Agent Note 中明确指向 gpu_finish 1.0.4–1.0.8 的清理说明仍是对应旧版本事实；本单元不改写历史结论。
U1 Note 的“可信组合为空”描述的是 U1 交付时点，U3 登记依据是规划者已核对的 U2 最终包证据。
