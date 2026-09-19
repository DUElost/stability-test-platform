# inotifyd-only DLE 创建/上送真机 E2E（#310 收口）

Status: implemented
Class: testing

Issue: #310（ADR-0028 Phase 3 signoff §4 残余验收；执行程序
`docs/operations/2026-09-19-inotifyd-only-e2e-procedure.md`）

## Decision

在生产 MTK host（172-21-x-x / 设备 A2WENX6814000151）上完成「Reconciler
关闭/抑制时，inotifyd 兜底路径独立完成 DLE 创建与上送」的真机受控验收。
方法：单机 env 翻转 `STP_WATCHER_AEE_RECONCILE_ENABLED=false` + 控制面
reload_config（免重启，只影响 reload 之后开场的 per-Job 会话），骑周期回归
链的真实 job 会话注入事件，全链采证后立即还原。

两轮执行（第二轮换注入物重放上送半程）：

- **第 1 轮（job 27117，plan_run 443，18:06:59 开场）**：最小合成目录
  （mkdir + 文本 .dbg）。证明创建半程 + 过滤模型闸。
- **第 2 轮（job 28197，plan_run 445，21:20:53 开场）**：重放真实历史条目
  （`2026_0827_221918_553_db.01.ANR`，17MB，此前轮次已被解析入 xls——保证
  可解析），意在补完上送半程；实际结果为定位出上送断链根因（#2822，
  见验收标准 2）。

## 证据（验收标准对照）

1. **Reconciler off/suppressed → inotifyd 触发 pull → DLE 创建可用
   `local_path`** ✅ 两轮均验：
   - 会话日志无 `platform_reconciler_active`（对照：翻转前的 26582 会话有
     该行）；`device_log_watcher_started capability=inotifyd_root puller=on`。
   - `device_log_watcher_emit_fallback serial=A2WENX6814000151 job=27117
     cat=AEE file=310e2e_db.00.NE`（18:11:04）；
     `... job=28197 ... file=2026_0827_221918_553_db.01.ANR`（22:02:40）。
   - DLE 行：`b41499aa…`（LOCAL，20B，job 27117 / run 443）、
     `704d68f7…`（LOCAL→，17,259,399B，job 28197 / run 445），
     `local_path` 指向 puller 落地的 NFS 目录。
2. **UPLOAD_PENDING → EventUploader → REMOTE（过滤模型）** ⚠️ **未走通，根因已定位，按
   验收标准「or documents explicit limitation」关闭，修复跟踪 #2822**：
   - 合成不可解析内容停在 LOCAL＝过滤模型设计行为（scan xls 只收可解析条目）；
   - **真实条目重放（run 445）同样未达标记**——根因：puller 落地名带
     `<epoch_ms>_` 前缀，scan xls 保留该名，而
     `event_dir_basename_from_path` 的年首时间戳正则对 `1789…` 开头返回
     None → 标记永不命中。同一 xls 中 reconciler 落地名（无前缀）提取成功
     （`saq_upload_marked plan_run=445 marked=9` 全为 reconciler 事件），
     分叉行为已对真实字符串实证。此为**静默断链**：Reconciler 主路下无感，
     灰度回退场景（inotifyd 变实际主路）材料永不上送。
   - 途中实录 puller 严格校验重拉自愈（22:01:45 attempt=1 目录为空 →
     22:01:48 attempt=2 verify_recovered，#828 机制）与 inotifyd 源 31 次断连
     退避重连全自愈（含 adb root 重启 adbd 造成的断连）——创建半程的
     真实环境韧性无保留通过。
3. **`job_log_signal.device_log_event_id` 关联** ✅：signal 7938
   （source=inotifyd，seq_no=1）→ DLE b41499aa；重放 DLE 的 signal 关联同构。
4. **程序留档** ✅：`docs/operations/2026-09-19-inotifyd-only-e2e-procedure.md`
   （选机 SQL、翻转/恢复、注入（含重放法）、全链判据、风险边界），signoff
   §4 已回填并链接本 Note。

## 关键发现（副产物）

- **全生产史 inotifyd 源 DLE 数 = 本测试创建的 2 条**（此前 0 条）——
  Reconciler 可靠到 inotifyd 创建路径从未被生产触发过，#310 的空白是真空白，
  本次为首次真实走通。
- **发现上送断链（#2822）**：puller 落地 `<epoch_ms>_` 前缀 vs
  `event_dir_basename_from_path` 年首时间戳正则不兼容 → watcher 源事件
  标记永不命中、材料永不上送。Reconciler 主路下无感（全库 0 条 inotifyd
  DLE 即证明），灰度回退场景静默失效。E2E 的价值正在于此——该缺口
  单元测试测不到（`test_device_watcher_dle` mock 了 client），只有全链
  真机验收能暴露。
- 上送标记闸是「scan xls 解析引用」（ADR-0028 方案 A 过滤模型）：不可解析
  内容永远停在 LOCAL——这部分是设计行为（CIFS 只收精选子集），非缺陷。
- 现场坑（已写进程序文档）：`adb push`/`mv` 因 SELinux 拒绝写入
  `aee_exp_data_file`（adbd 域与跨上下文 rename），必须 `adb root` 后用
  shell 域 `mkdir+cp`；dir 创建事件若落在 watcher 重连间隙会被错过，
  rm 后重建即可重触发。

## Alternatives

- **手动造 PlanRun 承载测试事件**：否——与周期回归链抢设备调度，干预面大；
  骑链零额外调度干预。
- **重启 agent 使 env 生效**：否——`reload_config` 走 `load_dotenv(
  override=True)` 免重启达成，且 restart 会打断在跑 job。
- **改动生产链路代码以「放行」合成事件上送**：否——那会破坏过滤模型本身；
  验收应测真实行为而非为测试改行为，故第二轮改用真实条目重放。
- **仅交付创建半程 + 标注限制**：作为 fallback 保留过，但重放法成本可控
  （一轮等待），选择拿全量证据。

## Verification

- 执行程序文档 field-test 通过（两轮真实执行，含第一轮发现的合成内容过滤
  行为与第二轮的 SELinux/root 现场坑，均已回写文档）。
- 证据链：agent 日志（emit_fallback / puller 重拉 / 重连自愈）、控制面 DB
  （DLE 两行 + signal 关联 + 状态迁移）、中心存储落地文件（signoff §4 回填
  时引用具体值）。
- 环境还原：两轮翻转各自在采证完成后立即还原 + reload 验证
  （16:48:53 / 20:23:06 / 22:03:21 三次 `env_reloaded=True`；最终 `.env`
  无开关行，设备侧注入物已清理）。
- 未触碰其他 host；DB 全程只读；凭据未落任何文档/日志/PR。

## Revisit

- 上送断链修复：#2822（正则侧最小修 / puller 命名契约归一，二选一）。修复
  落地后可用本程序（含重放法）复测 LOCAL→UPLOAD_PENDING→REMOTE 半程，
  预期一轮即可闭合。
- 若未来 Reconciler 长期下线（灰度回退场景），inotifyd-only 将成为常态主路，
  届时本程序的翻转/还原步骤可直接复用为演练脚本（修复 #2822 前注意其上送
  断链影响面）。
