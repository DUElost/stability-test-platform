# 三问确认稿：R01–R15 审查覆盖、修复有效性与修复模式（第三意见）

> **状态**：待综合——独立确认稿（第三意见），供多 Harness 综合审查汇聚（Mode C：先独立、后汇聚）。
> 与 [`REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fb87d5f1.md`](./REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_fb87d5f1.md)（`CA-*`，第一意见）、
> [`REVIEW_INDEPENDENT_VERIFICATION_2026-09-11_4a955874.md`](./REVIEW_INDEPENDENT_VERIFICATION_2026-09-11_4a955874.md)（`IV-*`，第二意见）同题独立完成。
>
> **核验基线**：`d00273d0`（origin/main tip，本 Execution declare 时点）
> **日期**：2026-09-11（观测时点：UTC 2026-09-10 19:15–19:50 前后，各节注明）
> **方法**：只读审计 + GitHub REST（gh）+ Git 事实 + 代码级 grep 实证。
> 未运行 pytest / Vitest / 门禁，未修改代码，未触发 CI，未新建或关闭 issue。
> **边界**：本文是确认与增补输入，不是裁决本身。不重复 CA/IV 已列明细，只保留判定、
> 本稿独立证据与差异；不新增待裁决项，裁决项以 `CA-D*`、`IV-D*` 与在窗综合稿为准。
> **编号**：本文使用 `CF-*` 前缀（Confirmation Findings），与 `CA-*` / `IV-*` 不冲突。

---

## 0. 摘要

| 问题 | 本稿判定 | 与 CA / IV 的关系 |
|---|---|---|
| Q1 R01–R15 之外是否还有未覆盖审查面 | **有**——两稿已列治理缺口全部确认；本稿增补：横切系统性断言的代码级独立复核（CF-C01，**全部实证成立**）与 R15 台账自身状态（CF-C02） | 一致 + 增补 |
| Q2 已闭环 issue 的修复是否准确 / 有效 / 可持续 | **准确 = 是；有效 = 是；可持续 = 条件尚未充分满足**——采纳 IV 稿「条件已在实际层面被违反」的更锐利判定（IV-Q04）；本稿增补 9-PR 逐单测试抽样与 backstop 修复验证 pending 声明（CF-Q01） | 一致（采 IV 口径）+ 增补 |
| Q3 审查→issue→认领→worktree/PR→CI→main 模式 | **方向准确且已产生治理复利；尚未充分可持续，瓶颈在集成收尾侧**——与两稿一致；本稿增补吞吐趋势边际更新（CF-W01）与三稿计数口径说明（§3.2） | 一致 + 增补 |

**本稿对 CA/IV 无重大更正**；最高优先级单点与两稿一致：`#1246`（FIFO 单车道无解毒路径，仍 open）。

---

## 1. Q1 覆盖面：确认与增补

**判定：确认「有未覆盖面」。** CA-C01（跨区收口未启动，总纲 §6 硬交付物）、CA-C02（覆盖证据链缺失
与总纲 §5 漂移）、CA-C03（无主候选面）、IV-C03（44 条 pre-R 存量缺陷池，30 条零 R 关联）、
IV-C04（性能容量 / 凭据生命周期 / 助手成本 / 数据保留）本稿均独立确认成立，证据不再复述。

### CF-C01 横切系统性断言的代码级独立复核（本稿独有）

在窗综合稿（cursor 会话，Registry `docs-r01-r15-synthesis-2026-09-11`，CODING）§1.4 及其引用的
failure-mode 备忘提出一组系统性断言。本稿对其中可 grep 实证的四类做了**独立复核（复现命令见附录），
全部成立**：

| 断言 | 复核结果（`d00273d0`） | 判定 |
|---|---|---|
| redis 客户端无 socket 超时，黑洞分区挂死风险 | `backend/main.py:147` `aioredis.from_url` 实存；全文件 `socket_timeout|socket_connect_timeout` 零命中 | ✅ 成立 |
| `backend/realtime/log_writer.py:20` `_locks` 无淘汰机制 | 模块级 `Dict[int, asyncio.Lock]`，`_get_lock` 仅增不删，随 job_id 单调增长 | ✅ 成立 |
| 全仓业务代码零 jitter（重连风暴同频风险） | `grep -ri jitter backend`（排除 test）命中 0 | ✅ 成立 |
| 客户端写请求无端到端幂等机制 | `Idempotency|X-Request-Id` 在 api/services 共 13 处命中，**全部为服务端内部幂等**（派生键 / ON CONFLICT / 注释），无客户端 header 机制 | ✅ 成立 |
| agent 脚本时钟风险（naive `time.time()` 死等） | `backend/agent/scripts` 72 个文件用 `time.time()`；`backend/agent` 51 个文件用 `time.monotonic()`——混用确认（量级口径，未逐文件核场景） | ✅ 成立 |

**价值**：综合轮可直接采信这组断言，无需二次核验；同时「混沌 / 断死 / 时钟 / 幂等」从候选面
升格为**已实证缺陷面**（此前仅 CA-C03 以候选面形式列出「动态验证」，未落到具体断言）。

### CF-C02 R15 台账自身状态（本稿独有）

R15（#1302，测试 / CI / 工程治理——即修复模式的自审面）2026-09-11 当日登记，9 条全部 open（0 收口）。
审查模式自身的可审计性与可改进性依赖 R15 先收口；综合轮排期时应将其视为「模式演进的前置」而非普通 P2。

---

## 2. Q2 修复有效性：确认与增补

**判定：确认「准确 = 是、有效 = 是、可持续 = 条件尚未充分满足」。** IV-Q01（105/105 issue↔PR↔关闭
三元组完整、0 reopen、原生关单秒级）、IV-Q02（09-09 起 100% 人工身份合入）、IV-Q04（backstop 连续
失败且不阻断合入）本稿独立确认；CA-Q01–Q04 的聚合量化与本稿逐单抽样相容。

### CF-Q01 9-PR 逐单测试抽样（本稿独有，补充 IV 的聚合口径）

对 2026-09-10 合入的 9 个 PR（横跨 R08 / R12 / R13 / R14 与缺陷批次）逐一核对：

| PR | 主题 | files | 专项测试 |
|---|---|---|---|
| #1305 | 巡检状态按 Job 隔离（#1028，R08-F10） | 14 | `backend/agent/tests/test_check_state_per_job.py` |
| #1304 | AI 助手 P1 五单（#1213–#1217） | 21 | 5 个后端测试 + 1 个前端测试 |
| #1303 | agentctl readiness（#1254，R14-F08） | 3 | `tests/test_agentctl_contract.py` |
| #1301 | PG 模板强制口令（#1262，R14-F16） | 5 | `tests/test_deploy_postgres_hardening.py` |
| #1292 | Ansible 资源保护（#1248，R14-F02） | 5 | `tests/test_rsync_host_local_protection.py` |
| #1286 | TRUNCATE 死锁有界重试（#1273） | 3 | `backend/tests/test_truncate_deadlock_retry.py` |
| #1281 | flush 串行化（#1275） | 3 | `backend/tests/services/test_run_console.py` |
| #1283 | seed sha 回填（#1276） | 3 | `backend/tests/migration/test_seed_sha_backfill_1276.py` |
| #1290 | PlanRun 刷新 + 分页（#1193/#1194，R12-F06/F07） | 10 | 3 个前端测试 |

**9/9 带专项测试文件；9/9 由人工身份（DUElost）合入；对应 issue（#1028/#1213–#1217/#1254/#1262/#1248/#1273/#1275/#1276/#1193/#1194）全部于 09-10 当日 CLOSED**——与 IV-Q01/Q02 的聚合结论在逐单粒度上一致。

### CF-Q02 backstop 红灯修复的验证状态：pending 而非 verified（本稿独有观测）

时间线（`gh run list --workflow main-ci-backstop.yml`）：09-07 success → **09-08 failure → 09-09 failure**
→ 根因 triage 为 #1272（saq merge 确定性断言失败）与 #1273（清库死锁非确定性）→ 修复 #1286（#1273）
等于 09-10 合入，两 issue 均于 09-10 关闭（#1272 为人工关闭、无 commit 关联，stateReason=COMPLETED）。
**截至观测时点（UTC 09-10 19:48），09-10 的 backstop run 尚未出现（该 schedule 历史实际触发约 20:00–21:00Z）**。

即：红灯修复「有效」的最终证据是下一轮全量转绿，当前状态为 **pending**——综合轮与后续引用
须注明这一点，不得在 backstop 转绿前把 #1272/#1273 记为「已验证修复」。

---

## 3. Q3 修复模式：确认与边际更新

**判定：确认「方向准确、已产生治理复利、尚未充分可持续；瓶颈在集成收尾侧」。**
CA-W01–W03、IV-W02/W03/W04 本稿确认；#1246 仍 open；IV-Q04（验证网红且不阻断）是当前最硬的
可持续性反证，本稿 CF-Q02 补充其修复验证的时间线。

### CF-W01 吞吐趋势边际更新（本稿观测）

open issue 计数：UTC 09-10 19:15 前后为 **243**，同日 19:45 前后为 **237**——期间 #1308–#1313
合入波次集中核销其关联 issue（含 R13 五单 P1 由单 PR #1304 一并收口）。**当前窗口内收口速率
> 开单速率**，对 CA/IV「发现能力领先消化能力」的判断构成时间维度上的边际缓和；但存量未变
（237 open + IV-C03 的 44 条零 R 关联存量池），趋势性结论应交由综合轮以更长窗口数据裁定。

### 3.2 三稿计数口径差异（无对错，仅提示并表时统一）

本稿首轮统计 R 区条目 **118 closed / 93 open（共 211 条）**，口径 = 各总表「条目行」按其关联子
issue 实时 state 计数，含 `Rxx-Ryy` 与「映射既有 issue」复用项；IV 稿为 **166 = 105 已关 + 61 开放
（另 8 条风险开放）**，口径 = issue 标题 `Rxx-Fyy`/`Rxx-Ryy` 检索。差异主要在「映射复用项是否计入」
与「子 issue 关联 vs 标题检索」，不构成事实冲突；综合轮并表时应统一口径并注明。

---

## 4. 供综合轮的输入清单

1. **主题并表映射建议**：CA-C* ≈ IV-C* ≈ CF-C01/C02；CA-Q* ≈ IV-Q* ≈ CF-Q01/Q02；CA-W* ≈ IV-W* ≈ CF-W01。
   本稿不新增 `CF-D*` 待裁决项——裁决项以 CA-D*、IV-D* 与在窗综合稿为准，本稿只供确认与证据增补。
2. **pending 核验点（最高优先）**：backstop 09-10 / 09-11 run 结论——#1272/#1273 修复有效性的
   首个全量证据；若连续第三晚 failure，IV-Q04 的「验证网」判定升级为 P0 级证据。
3. **在窗依赖**：综合稿（cursor，CODING）与其引用的 failure-mode 备忘合入后，本稿 CF-C01 的
   「在窗综合稿 §1.4」表述应更新为稳定文件引用；CA 附录 B（#1314，open）落地后同理。
4. 本稿全部计数与 run 结论为观测时点值，随时间失效；复用须重跑附录命令，不得引用本稿数值。

---

## 附录：复现命令（`d00273d0`）

```bash
# backstop 结论与时间线
gh run list --workflow main-ci-backstop.yml --limit 6

# 9-PR 抽样（逐单：合入者 / 文件数 / 测试文件）
for pr in 1305 1304 1303 1301 1292 1286 1281 1283 1290; do
  gh pr view $pr --json title,mergedAt,mergedBy,files
done

# 代码级断言（CF-C01）
grep -nE 'socket_timeout|socket_connect_timeout' backend/main.py        # 期望零命中
sed -n '15,30p' backend/realtime/log_writer.py                          # _locks 仅增不删
grep -ri jitter backend --include='*.py' | grep -v test | wc -l         # 期望 0
grep -rinE 'Idempotency|X-Request-Id' backend/api backend/services --include='*.py'
grep -rln 'time.time()' backend/agent/scripts --include='*.py' | wc -l  # 72
grep -rl  'time.monotonic' backend/agent --include='*.py' | wc -l       # 51

# 吞吐计数
gh issue list --state open --limit 1000 --json number | jq length
```
