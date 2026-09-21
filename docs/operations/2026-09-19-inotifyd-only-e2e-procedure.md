# inotifyd-only DLE 创建/上送 E2E 执行程序（#310）

> 用途：ADR-0028 Phase 3 signoff §4 钉住的残余验收——「Reconciler 关闭/抑制时，
> inotifyd 兜底路径独立完成 DLE 创建与上送」的真机受控测试。
> 自动化面已由 `backend/agent/tests/test_device_watcher_dle.py`（PR #347）覆盖；
> 本程序补的是真机全链证据（#310 唯一残余）。
> 日期：2026-09-19；首验记录见 signoff §4 与 Agent Note
> `docs/notes/testing/2026-09-19-inotifyd-only-e2e-310.md`。

## 0. 机制速览（为什么这样做）

- **抑制开关**：`STP_WATCHER_AEE_RECONCILE_ENABLED`（默认 true）。设 false 后
  `is_reconciler_enabled()` 返回 false → JobSession 不启动 Reconciler，
  watcher 创建时 `aee_reconciler_active=False` → inotifyd 对 AEE/VENDOR_AEE
  不再被抑制（`device_watcher.py::_maybe_register_device_log_event`）。
- **生命周期是 per-Job 的**：watcher / Reconciler 都挂在 JobSession 上，
  开关在**会话创建时**读取一次。改 .env 后必须等到「reload 完成之后启动的
  新 job」才生效——已运行中的 job 维持旧状态。
- **免重启生效**：控制面 `POST /api/v1/plan-runs/hosts/{host}/reload-config`
  → agent `control_handler` 执行 `load_dotenv(override=True)`，运行中进程的
  `os.environ` 直接翻新，无需 systemctl restart。注意 `/proc/<pid>/environ`
  是 exec 时快照，**不能**用它验证，只能看日志。
- **上送链**（ADR-0028 方案 A 过滤模型）：inotifyd pull 成功 → DLE 以
  `LOCAL` 落库（watcher **不**入队，#287）→ 本 job 所属 PlanRun 收尾时
  `scan_task` 把本地 AEE 目录收进 scan xls → `upload_task` 按 xls Path 列
  引用把 LOCAL 标记 `UPLOAD_PENDING` → agent `EventUploader`（30s 轮询）
  copytree 到中心存储 → `REMOTE` → `merge_task` 后 `extract_task` 复制到
  `jira/{plan_run_id}/`。
- **「骑链」策略**：不手动造 PlanRun。链式周期回归每 1.2–1.5h 一环，
  环间 ~5 分钟。在设备空窗期翻开关，等下一环 job 自然开场后注入事件，
  该 run 的收尾链会自然走完上送半程。

## 1. 前置检查

1. **选机**（控制面 DB 只读；连接姿势见
   `docs/operations/production-diagnostics.md`，连接串取 `.env.backend`
   的 `DATABASE_URL`，剥 `+asyncpg` 前缀）：

   ```sql
   SELECT h.id, h.name, count(*) FILTER (WHERE d.platform='MTK' AND d.status='ONLINE') AS mtk_online
   FROM host h JOIN device d ON d.host_id=h.id
   WHERE h.status='ONLINE' AND h.retired_at IS NULL
   GROUP BY h.id HAVING count(d.id) FILTER (WHERE d.platform='MTK') > 0
   ORDER BY count(d.id) ASC LIMIT 5;
   ```

   选 MTK 设备最少、且目标设备 `status='ONLINE'`、无 ACTIVE 租约的 host
   （影响面最小）。查目标设备近几轮 job 节奏（`job_instance WHERE
   device_id=… ORDER BY id DESC`），确认下一环开场时间。

2. **SSH 与设备前提**（`ssh <ssh_user>@<host_ip>`，密钥见控制面 host 表）：

   ```bash
   systemctl is-active stability-test-agent
   adb -s <SERIAL> shell 'id; which inotifyd; ls -d /data/aee_exp /data/vendor/aee_exp'
   ```

   非 root（`uid=2000(shell)`）可接受——能力层级会落在 `inotifyd_shell`
   （probe 只要求 `ls -d` 可读）。**前提**：`/data/aee_exp` 对 shell 可写
   （0777）或设备已 root；否则注入步骤需调整（见 §5 风险）。

3. **控制面 API token**（本机即生产控制面）：

   ```bash
   set -a && . ./.env.backend && set +a
   TOK=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
         -H 'Origin: http://127.0.0.1' \
         --data-urlencode "username=${STP_ADMIN_USER}" \
         --data-urlencode "password=${STP_ADMIN_PASSWORD}" | jq -r '.access_token')
   ```

   （CSRF 要求 Origin 头；响应是扁平 OAuth2 体取 `.access_token`。）

## 2. 翻开关（单 host 范围，免重启）

1. 备份 + 追加（`.env` 尾部行覆盖此前同名值）：

   ```bash
   ssh <user>@<host> 'cp /opt/stability-test-agent/.env /tmp/env.bak.e2e310 && \
     printf "\n#310 E2E temporary flip (restore after test)\nSTP_WATCHER_AEE_RECONCILE_ENABLED=false\n" \
     >> /opt/stability-test-agent/.env'
   ```

2. 下发 reload：

   ```bash
   curl -s -X POST "http://127.0.0.1:8000/api/v1/plan-runs/hosts/<host_id>/reload-config" \
     -H "Authorization: Bearer $TOK" -H 'Origin: http://127.0.0.1' -H 'Content-Type: application/json'
   ```

3. **日志验证**（唯一可信验证点）：

   ```bash
   ssh <user>@<host> 'grep control_reload_config_done /opt/stability-test-agent/logs/agent_error.log | tail -1'
   ```

   必须看到 `control_reload_config_done env_reloaded=True`。**记录该时刻**：
   只有 `started_at` 晚于它的 job 才是 inotifyd-only 会话。

## 3. 等待干净会话 + 验证抑制已解除

1. 等下一环 job 在目标设备开场（轮询 `job_instance` 或看 agent 日志）。
2. 会话开场后立即确认（`agent_error.log`）：

   ```bash
   grep "<job_id>" /opt/stability-test-agent/logs/agent_error.log \
     | grep -E "device_log_watcher_started|platform_reconciler_active|platform_reconciler_start"
   ```

   判据：
   - `device_log_watcher_started ... capability=inotifyd_shell puller=on`（能力可用）；
   - **没有** `platform_reconciler_active job_id=<job_id>` 行——有则说明该会话
     仍在旧开关下（时序判断错了），等再下一环。

## 4. 注入合成 AEE 事件 + 采证

1. **注入**（shell 可写 `/data/aee_exp` 的前提下；目录名任意，puller 按目录
   整体拉取；严格校验要求「至少一个非零 `.dbg` 文件」——见
   `processor.py::_verify_pulled_aee_log_strict`）：

   ```bash
   ssh <user>@<host> "adb -s <SERIAL> shell 'mkdir -p /data/aee_exp/310e2e_db.00.NE && \
     echo e2e-marker-310 > /data/aee_exp/310e2e_db.00.NE/db.00.dbg && \
     echo body > /data/aee_exp/310e2e_db.00.NE/ZZ_INTERNAL'"
   ```

   竞态窗口：watcher 的 puller 有 3 次重试（`STP_WATCHER_AEE_PULL_VERIFY_ATTEMPTS`，
   默认 3 次 × 2s），文件几秒内补齐即可过验。

   > **2026-09-19 实测补充（上送半程的两层结论）**：
   >
   > ① 最小合成目录只能验证**创建半程**（inotifyd → pull → DLE LOCAL +
   > signal 关联）。`upload_task` 的标记闸是「scan xls Path 列引用」，scan
   > 解析器只把**可解析的真实异常条目**写进 xls——文本占位的合成目录永远
   > 不进 xls，停在 LOCAL。这是过滤模型按设计工作（CIFS 只收精选子集）。
   >
   > ② 重放真实历史条目（从 host day tree 取一条已解析入 xls 的原始条目
   > `cp` 到 /tmp 后，`adb root` + shell 域 `mkdir+cp` 进 `/data/aee_exp/`；
   > `adb push`/`mv` 被 SELinux 拦在 `aee_exp_data_file` 外）同样**未走通
   > 上送**：puller 落地名带 `<epoch_ms>_` 前缀（`_compose_local_path`），
   > scan xls 保留该名，`event_dir_basename_from_path` 的年首时间戳正则
   > 对 `1789…` 开头返回 None → 标记永不命中。**这是 #2822 跟踪的断链**
   > （Reconciler 主路无感，灰度回退场景静默失效）。#2822 修复落地前，
   > 本程序只能验收创建半程；修复后用重放法一轮即可复测上送半程。
   >
   > ③ **[2026-09-21 更新] 断链已修复并复测通过**：#2822 已合入（`27b060a0`，
   > 识别 13 位 epoch 毫秒前缀、返回全名作标记匹配键）。控制面 09-21 重启
   > 生效后，对 run-445 `POST /plan-runs/{id}/dedup/scan?is_final=true` 重扫
   > 即免设备复测（重扫作用域继承原 job 日戳，仍覆盖卡滞事件所在日树）：
   > 卡滞的 watcher 源 DLE 全链 LOCAL→UPLOAD_PENDING→REMOTE→ARCHIVED
   > 4 分钟闭合，extract 产物 `jira/445/<epoch_ms>_<原名>` 实盘在。
   > 此后本程序上送半程（真实条目重放注入）应自然到达终态——若再卡 LOCAL，
   > 按新回归处理而非已知限制。

2. **Agent 侧证据**（`agent_error.log`，按时间窗截取）：

   ```bash
   grep -E "device_log_watcher_emit_fallback serial=<SERIAL>|device_log_watcher_pull|log_puller" \
     /opt/stability-test-agent/logs/agent_error.log | tail -20
   grep "event_uploader_ok event_id=<event_id>" /opt/stability-test-agent/logs/agent_error.log
   ```

   判据：`device_log_watcher_emit_fallback ... cat=AEE file=310e2e_db.00.NE`
   ——**这就是 inotifyd-only 发射的签名行**（抑制态下不会有）；
   上送半程后出现 `event_uploader_ok`。

3. **控制面 DB 证据**（只读）：

   ```sql
   SELECT id, serial, state, local_path, remote_path, plan_run_id, job_id, signal_seq_no, size_bytes
   FROM device_log_event WHERE local_path LIKE '%310e2e%' ORDER BY id DESC LIMIT 3;

   SELECT id, job_id, seq_no, category, source, device_log_event_id
   FROM job_log_signal WHERE device_log_event_id = <上查 event_id>;
   ```

   时间线判据：
   - 注入后数秒：`state='LOCAL'`，`job_id`/`plan_run_id` 正确，`signal_seq_no` 非空；
   - `job_log_signal.device_log_event_id` 关联（验收标准第 3 条）；
   - 本 job 终态、scan 链跑过 `upload_task` 后：`state='UPLOAD_PENDING'`
     （`updated_at` 跳变；upload_task 日志 `saq_upload_marked`）；
   - EventUploader 轮询到后（≤30s + 上送时长）：`state='REMOTE'`，
     `remote_path` 指向中心存储（验收标准第 2 条）。

4. **（顺带）extract 半程**：`merge_task` 成功后 `extract_task` 会把
   `remote_path` 目录复制进 `jira/{plan_run_id}/`——控制面日志
   `saq_extract_start/extracted` 或中心存储 `ls` 可见即 Extra。

## 5. 恢复与收尾

1. 还原开关（**必须**，Reconciler 是机队主采集路径）：

   ```bash
   ssh <user>@<host> 'cp /tmp/env.bak.e2e310 /opt/stability-test-agent/.env && \
     grep -c STP_WATCHER_AEE_RECONCILE_ENABLED /opt/stability-test-agent/.env'
   ```

   （`grep -c` 应为 0——备份是翻转前的。）再发一次 reload-config，
   日志确认 `env_reloaded=True`。下一环 job 恢复 `platform_reconciler_active`。

2. 若 `/data/aee_exp/310e2e_db.00.NE` 残留在设备上且不想让它进后续 scan，
   留在设备上无害（job 会话结束时 puller 已拉走；后续 scan 按当日 MonkeyAEE
   目录收口，不重复入库）。

## 6. 风险与边界

- **影响面**：单 host 单开关，reload 免重启、不动在跑 job；恢复同样免重启。
  其余 host 零影响。
- **失败模式**：
  - 注入时序错（会话早于 reload）→ 该轮证据作废，等下一环即可，无副作用；
  - puller 校验失败 → 信号仍落 outbox（`pull_failed` 路径，DLE 记
    `create_pull_failed_event`）——这本身也是 #310 关心的兜底分支，可作为
    附加证据保留；
  - run 收尾链未跑（job FAILED 且链中止）→ LOCAL→UPLOAD_PENDING 半程不会
    触发；此时可用控制面 dedup 手动 scan/upload 端点补触发，或仅交付
    DLE 创建半程证据并如实标注。
- **红线**：全程只读 DB；不 restart agent；不碰其他 host；凭据不落文档/日志。
