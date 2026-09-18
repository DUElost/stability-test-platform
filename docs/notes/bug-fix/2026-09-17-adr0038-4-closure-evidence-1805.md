# ADR-0038 ④ 收口对账：派发 fatal/claim/scan/WS/执行拒绝 + 一处真缺口（Phase A0b）

Status: implemented
Class: bug-fix

## Decision

本单**不是**按计划重写 ④ 的全部清单——领单后的逐项代码审计显示：#1805（开单
09-14）列出的点位，绝大部分已被中间 PR 分片消化（#1920 push-error 归因、
#2059 HOST_RETIRED fatal 归因、#2047 尾读审计、#2262/#2270、#2447、#2531、
#2564 archive 切片……）。对已落地项**加测试再加倍冗余**没有增益；本单的交付 =
**验收程序本身**（逐点回归 + 反例实证 + 场景表映射）+ **一处真缺口的实现**：

### 唯一代码缺口：准入 Phase A0b 退役预检（`admission_pump.plan_admission_task`）

182d4e-F7 场景表「prepare 与准入竞态」行要求：*已准备后退役的任务**不做
verify/SSH/物化***。此前链路是 A0(suite)→**A(verify RPC + 漂移 push 回退)**→
B(终检才认退役)：

- SSH 写面已被 `precheck/sync` 两个命名收口点挡住（D5 点名的共享收口点，已有
  守卫+测试）——所以**没有**真实的 SSH 触碰退役机；
- 但 verify 的 **RPC 读**与 push 回退**仍会对退役机发起**，HOST_RETIRED 归因
  绕道「push 失败串匹配」才成立；退役事实一次只读 DB 查询即可判定，没有理由
  排在慢路径之后。

新增 Phase A0b：join `PlanRunHost`×`Host.retired_at` 活读，命中 →
`_FatalAdmission("HOST_RETIRED", {"hosts":[...]})`——走既有 `_fail` 链路
（FAILED + 审计 + 快照/PlanRunHost 不删，D5bis）。**Phase B 终检原样保留**：
两点是同一判据在不同时刻的复查（TOCTOU），不互替、不遮蔽——场景表
「两个 DB session barrier 交错」的要求由 A0b+B 的双活读满足：retire 提交
落在任一时刻都进 HOST_RETIRED，不存在「退役成功且新授权物化」的交错。

新用例 `TestAdmissionRetirePrecheck1805`：核心断言是 `calls == {"verify": 0,
"push": 0}`（**根本没被触碰**，不是「触碰后在内部拒」）+ FAILED/HOST_RETIRED +
审计行 + 快照保留。

### 反例实证（12 点位，逐点 mutation 纪律）

判据按 182d4e-F7：一次只旁路**一个**过滤点 → 该点契约用例必须**行为**失败
（非导入/语法错）→ `git checkout` 恢复 → 复绿。每点清 `__pycache__`。运行记录：

| # | 过滤点（旁路） | mutate 后 | 恢复后 |
|---|---|---|---|
| ✅ M1 分类器退役分支→遮蔽反例 | `plan_dispatcher_sync.py` | 3 failed, 1 passed, 36 deselected, 2 warnings in 1.39s | 4 passed, 36 deselected, 2 warnings in 1.36s |
| ✅ M2 fatal 集合去 host_retired | `plan_dispatcher_sync.py` | 2 failed, 38 deselected, 2 warnings in 1.18s | 2 passed, 38 deselected, 2 warnings in 1.17s |
| ✅ M3 pump A0b 预检旁路（重跑，见下） | `admission_pump.py` | 1 failed (RetirePrecheck) in 0.99s | 34 passed（整文件）in 6.91s |
| ✅ M4 pump B 段归因旁路 | `admission_pump.py` | 1 failed, 33 deselected, 2 warnings in 1.16s | 1 passed, 33 deselected, 2 warnings in 1.27s |
| ✅ M5 push-error 归因旁路 | `admission_pump.py` | 1 failed, 31 deselected, 2 warnings in 0.96s | 1 passed, 31 deselected, 2 warnings in 0.87s |
| ✅ M6 claim 退役跳过旁路 | `agent_claim.py` | 1 failed, 1 passed, 19 deselected, 2 warnings in 1.13s | 2 passed, 19 deselected, 2 warnings in 1.04s |
| ✅ M7 classify_recycle 旁路 | `plan_run_scan_scope.py` | 1 failed, 1 passed, 6 deselected, 2 warnings in 1.63s | 2 passed, 6 deselected, 2 warnings in 1.52s |
| ✅ M8 sync 热更新兜底守卫旁路 | `sync.py` | 1 failed, 1 passed, 13 deselected, 1 warning in 1.03s | 2 passed, 13 deselected, 1 warning in 1.10s |
| ✅ M9 sync SFTP 推送守卫旁路 | `sync.py` | 1 failed, 1 passed, 13 deselected, 1 warning in 0.92s | 2 passed, 13 deselected, 1 warning in 0.80s |
| ✅ M10 begin_host_upgrade 旁路 | `host_upgrade_gate.py` | 1 failed, 14 deselected, 2 warnings in 0.71s | 1 passed, 14 deselected, 2 warnings in 0.71s |
| ✅ M11 reload-config 守卫旁路 | `dedup.py` | 1 failed, 34 deselected, 1 warning in 1.19s | 1 passed, 34 deselected, 1 warning in 1.06s |
| ✅ M12 install 守卫旁路（换唯一锚点重跑，见下） | `hosts.py` | 1 failed (install_retired_is_409) in 1.28s | 3 passed (retired_is_409 全组) in 2.05s |
| ✅ M13 watcher 守卫旁路 | `hosts.py` | 1 failed, 59 deselected, 1 warning in 1.08s | 1 passed, 59 deselected, 1 warning in 1.16s |

判据：mutate=**行为断言失败**（非导入/收集错），restore=绿。全部 ✅ 即 ④ 反例实证完成。

### 首轮两处不达标与处置（如实）

- **M12 首轮误伤**：驱动以 `rfind(短锚点)` 定位 install 守卫，实际命中的是
  hot-update 路由守卫（hosts.py 内 4 处同形 `if host.retired_at is not None:`）——
  旁路了错误的层，测试当然仍绿。改用**注释级唯一锚点**重跑 → 行为红 → 恢复绿。
  这正是 F7 警告的「多层重复防护互相遮蔽」在**驱动自身**上的复现。
- **M3 首轮事故**：驱动在旁路我**未提交**的 Phase A0b 后执行 `git checkout -- 文件`
  恢复——把 A0b 本体一并抹掉（恢复侧因此恒红）。重新落盘 A0b 后改为
  **备份副本恢复**（`cp`，不用 git checkout 覆盖未提交面）重跑 → 红/绿齐。
  另：首轮驱动以 `&` 脱离会话，被会话回收 SIGKILL 后在仓库里留下过一帧
  M1 变异态——已 `git checkout` 复原并在提交流程前用 `git status` 核实干净。
  教训：变异跑批必须跑在受控前台会话 + 提交后基线上。


### 矩阵面 → 实现锚点 → 测试锚点（本次逐点回归的对应表）

| 面（#1805 验收所列） | 实现锚点（现状） | 测试锚点 |
|---|---|---|
| 1 派发快照/准入分类 + fatal 归位 + 遮蔽修正 | `plan_dispatcher_sync.py:72,152-154`；`admission_pump` A0b/`_attribute_fatal`/Phase B | `test_plan_dispatcher_device_validation.py`（RetiredFatal 类，引 182d4e-R01）、`test_admission_queue_step4.py`、`test_execution_state_signals_step5a.py:519` |
| 2 claim 活读 + 可区分信号 | `agent_claim.py:182`（`claim_skipped_host_retired`，对照 maintenance 先例） | `test_agent_api_watcher.py:1031,1073` |
| 3 scan/archive 扇出（admin 例外 + skipped_retired 不虚报） | `plan_run_scan_scope.classify_recycle_targets`；调用点 `saq_tasks`/`plan_run_archive`/`dedup.py`/`ai_assistant/plan_run_ops`（allow_retired=False） | `test_plan_run_archive_endpoint.py:160,191`、`test_dedup_scan_endpoints.py:537,558` |
| 4 Socket.IO 保留连接、只下行判据；reload-config | `dedup.py:573`；`socketio_server.emit_agent_control`（房间保留，判定在调用点守卫） | `test_dedup_jira_endpoints.py:399` |
| 7 install 拒绝（新增门禁） | `hosts.py` install 端点 409 `HOST_RETIRED` | `test_hosts.py:748` |
| 8 upgrade-gate 拒绝 | `host_upgrade_gate.begin_host_upgrade:259`（hot-update 路由与 batch `--direct` 共用此闸） | `test_host_upgrade_gate.py`、`test_hosts.py:740` |
| 11 预检 SSH 同步/准入 Phase A 过滤点前移 | `precheck/sync` 两守卫（命名收口点）+ **本单 Phase A0b**（前移完成） | `test_precheck_sync.py:223,239`、新用例 |
| 12 Agent 自服务写面（recovery/sync 等） | recovery-sync 退役 abort 分支（⑤ 邻面已先行） | `test_agent_dual_write.py:3495,3540` |
| 13 管理写路径（watcher 切换等） | `hosts.py` watcher-admin-state 409 | `test_hosts.py:760` |
| 回收类放行（D-5） | `scripts` 尾读允许+审计 | `test_agent_log_query.py:80` |

未在本单范围：面 5/6/9/10/14 属 ③/#735 工具链/⑤心跳/⑥前端（④票面自明）。

## Alternatives

- **无视审计、按票面从零重写 ④**：弃——对已有守卫+测试的行为面重写只会造出
  第二实现（§3.5 决策实体唯一性的实现面翻版），且 F7 纪律要求的是「反例实证」
  而不是更多代码；
- **只跑回归、不补 A0b**：弃——「已准备后退役的任务不做 verify」写在验收场景表
  字面上，现状靠慢路径内部的守卫兜底，归因链绕道；预检是一次只读查询的成本，
  不补就是验收缺口；
- **把 Phase B 的终检退役归因删掉、只留 A0b**：弃——两活读是 TOCTOU 双时刻，
  删任一都会开「预检通过后才退役仍被物化」的窗。

## Verification

- **反例实证 13/13 ✅**（上表；F7 纪律：逐点旁路→行为红→恢复绿；两处首轮
  不达标均已如实记录并重跑达标）；
- 新增用例 `TestAdmissionRetirePrecheck1805`：A0b 存在时 `verify/push` 调用数
  恒 0、FAILED+HOST_RETIRED+审计+快照保留；旁路 A0b → 该用例红；
- 逐点回归（整文件）：dispatcher/admission/step5a/precheck-sync/upgrade-gate/
  archive/dedup-scan/dedup-jira/hosts/log-query/heartbeat-1806 → **246 passed**；
  agent watcher + dual_write → **96 passed**；admission step4 整文件 34 passed；
- 变异跑批全部在隔离 worktree、`-p no:cacheprovider` + 清 `__pycache__`；
  提交前 `git status` 核实仓库无残留变异态；
- `run_gates.py check:quick` 与 required checks → 见 PR。

## Revisit

- **（已建，2026-09-18 追做）交错用例**：`TestAdmissionRetireInterleaving1805`
  把「retire 落在 A0b→Phase B 窗口」钉成**确定性**交错——交错点=被替换的
  `_verify_scripts_phase`，独立 DB 会话在其中提交退役，无需 `threading.Barrier`
  （不靠线程时序，判定确定，符合 #2679 的「等异步状态」判据方向）。断言
  「退役成功与新授权物化不能同时提交」：FAILED+HOST_RETIRED、Job 零条、
  快照/PlanRunHost 保留。与 A0b 用例（窗口前界）合钉窗口两端。
- 面 5/6/9/10/14（统计/批量工具/AI 助手读写/设备面/前端）不在 ④；批量
  `--direct` 已共享 `begin_host_upgrade`（M10 红即证其闸在），但若 batch 工具
  将来绕过服务层直连 SSH，须重跑本矩阵；
- 若 A0b 的只读 JOIN 在超大 PlanRunHost 集上成为热点（当前面 ≤20 host），
  再议索引——现不加。
