# B6-U5：开关机接入 powercycle_disarm 1.0.8 与 powercycle_finish 1.1.0

Status: implemented
Class: feature

按 [#3723](https://github.com/DUElost/stability-test-platform/issues/3723) **v1.7.10** §3.0.2 / §3.6 / §3.6.1–§3.6.7 / §4 U5
与 ADR-0061 Accepted v1.1（含 D2.5）实施 [#3730](https://github.com/DUElost/stability-test-platform/issues/3730)。
方案 A 旧包处置见 [#3751](https://github.com/DUElost/stability-test-platform/issues/3751#issuecomment-6234224273)。
第 2 轮代码预审已通过（评论 6307749356）；1.0.8 真机及严格 B 补测已完成。补测亮度差异已恢复，按派单先回规划者（评论 6312504223）；最终独立复核尚未签署，PR 保持 draft。合入不等于生效（发包/scan/热更新/重指受 §5 整批闸门）。


## 历史记录：v1.7.1 返修（2026-10-19）

- 追加 `powercycle_disarm@1.0.4`；`1.0.3` → `retired:true`（树 `c832364b0fb1f1ef5f143835900b3a336d372f4d` / sha `9fd5c9fe…980ae`）。
- §3.6.2：等待上线进度戳窗口从上一次打戳起算；单次轮询 adb 超时受 15s 窗口约束；替身耗时 14s / 命中超时用例断言相邻戳 ≤15s。
- §3.6.1.7：删除脚本内置已验证类别清单；正式值一律未验证规则；evidence 增加 `permission_model` 与 `autotesttool_apk_sha256`；任何输入不输出正式 `confirmed` / `readback_failed`。
- ONBOARDED / 模板 pin → 1.0.4；方案 A 恢复清单含 1.0.0–1.0.3；该次历史提交尚无独立真机资源；现行真机结果见 Verification，NX2 转 #3761。

## Decision

1. 新族 `powercycle_disarm`：**1.0.8**（经 1.0.4–1.0.7 按 §3.6.1–§3.6.6 逐轮返修；
   当前实现见文末 v1.7.8 段）。参数 `mode`（`probe`/`disarm`）、`wait_device_online_seconds`。
2. job 级步骤角色 helper（§3.0.2）：`powercycle_finish@1.0.x` 仍解除；只有**登记为只收取**
   的 `@1.1.0` 判 collect；缺版本、无法解析与未登记的数字版本（1.1.1、2.0.0 等）一律
   `unknown_legacy_disarm` 并记 `unknown_legacy_version`；收取失败不得覆盖已完成 disarm。
3. Plan 保存入口 §3.6.5：三入口 422 `ONBOARDED_TEARDOWN_INVALID` + 四种 reason。
4. 旧条目 1.0.0–1.0.7 → `retired: true`（不删、不改 SHA）；
   `check_script_packages.py --publish-restored`（方案 A，仅临时站点目录验证）。
5. `ONBOARDED.disarm_min_version=1.0.8`；模板 pin
   `[powercycle_disarm 1.0.8 @660s, powercycle_finish 1.1.0 @420s]`；
   `verified_device_classes=[]`（本单只交 MLD_LX2、MLD_LX3 的真机事实，可信组合登记由 U8 承接；NX2 转 #3761）。
6. `powercycle_finish@1.1.0` **保持原包**。不改 U8 reaper/guard/类别政策；不 Ready/merge/publish/scan。

## Alternatives

- 把全部 finish 重新纳入解除或删 ONBOARDED：违反 §3.6.2，未采用。
- 方案 B（不发布旧包、改 SOP）：Owner 未选，未采用。
- 虚填已验证类别：违反 D2.5，未采用。

## Verification

- 门禁：`check_script_packages`（含 `--self-test` publish-restored 五项）；
  `check_tool_manifest --base origin/main`；`check:quick`。
- 临时站点目录对 1.0.0/1.0.1/1.0.2 真实 `--publish-restored`：写入 / 同字节跳过 / 异字节拒绝。
- 树对象可达并 SHA 与 manifest 一致。
- §3.6.5 / §4.1 真入口三入口反例与正向对照；Engine→runner 角色对照；15s 轮询进度。
- 第 2 轮代码预审通过，绑定 `04b2bd07895a0aa4f0311fb4e503123f7c00d1c4`；本次仅文档补证，不改变脚本、manifest、模板、包与测试。
- 1.0.8 真机：V552AA 的 MLD_LX2（H01，尾号 6490、6521）、MLD_LX3（H02，0034、0104）完成当前方案要求的判例证据；A 按 §3.6.6 第 8 条由 J 覆盖。I 非 root 每类 4 次，全部原始 clear；J 的 root/non-root 各 1 台为 not_installed 且零写入；K 拒读后不整写，原 mode/内容恢复。实际 APK/权限模型与次数见[完整报告](2026-10-21-b6-u5-realdevice-108.md)。root 下服务运行中的 disarm 未覆盖，不外推。
- B 原 0104 样本为 online=false 的离线分支，不计入。2026-10-21 17:18（+08）补测第 1 次先确认离线再重新在线，probe 自身 online=true、显式 boot_completed=""、原始 readback_failed、正式 failed，无 kind、无身份取证、无执行阶段 PROGRESS，10 项严格判据通过；与 LX2 两台及 LX3 0034 构成每类 2 台有效 B。
- 补测初次收尾亮度 61→28，RESULT.json 如实记录恢复比对失败；随后恢复为 61 并核对开机完成、prefs/应用/设置/进程/服务/adb server 与实验前一致，临时目录已删除。按“其它偏差先回规划者”已回告评论 6312504223，未直接交终签。
- 主实验与 D/F/H 补证没有逐次占用日志；旧日志只有收尾 64 条，不声称有可核验数据库 statement 日志。本次 B 与恢复逐次日志 167 条，ACTIVE 全为 0；规划者对原窗口无租约的独立查询见评论 6310794779。
- 原事件和初次错误辅助判定均保留；cases.py、experiment.py、j_cases.py 的留存版本在相关运行后有改动，select_j.py 实际未完成，不能以这些文件证明历史执行版本。B 新 harness 运行前/后摘要一致。证据目录 `/home/debian13/b6-u5-realdevice-108-20261021-04b2bd078/`，补测在 `B-retest/`。

## Revisit

- 按已回告的亮度恢复差异先回规划者；最终验收仍由原独立复核工作面签署。本次不登记可信组合，不追加脚本版本；U8 消费事实，NX2 见 #3761。
- U3/U6 的集成已在 `04b2bd078` 的代码预审与同 head CI 中核对；文档提交的新 HEAD 仍核对六项 required 与 informational，不沿用旧 SHA 代签。
- 激活按 #3723 §5；不得因本单元单独发包/scan/热更新/重指。

## 历史记录：v1.7.4 / 1.0.5（阶段一）

- `_wait_device_online`：轮询间隔 10s；get-state 2s + getprop 3s；迭代上界 timeout//10+2
- 上线后每次 adb 结束打 PROGRESS（含超时与 push_prefs_xml 直接子进程）
- `permission_model`：id -u → root/non-root/unknown；离线/开机未完成不取证
- probe 读 `sys.boot_completed`；evidence 记 `boot_completed`
- 追加 `powercycle_disarm@1.0.5`；1.0.4 `retired:true`；finish@1.1.0 未改


## 历史记录：v1.7.6 / 1.0.6（2026-10-20）

Diagnosis D1/D2（PR #3747 comment 6268508491）确认：AutoTestTool 收到
`POWER_CYCLE_STOP` 后在进程存活期间把 `auto_resume` 改回 true；1.0.5 先写
标志、再 STOP，服务运行中的解除 7/7 失败。另 `run-as` 的 `unknown package` /
`No such file or directory` / `Permission denied` 被 1.0.5 统一当作读空。

- `stop_task`：先 STOP + force-stop，确认服务已停后才写 `running=false` /
  `auto_resume=false`；`_verify_stop_flags` 同时验证两项，不一致重写一次。
- 非 root 读取按 `run-as` 的 rc + stdout/stderr 分 `ok` / `not_installed` /
  `absent` / `denied` / `transient`；`pm path` 记
  `autotesttool_installed` true/false/null；非 root 不用 `test -f`。
- 原始 readback 增加 `not_installed` / `absent`；未安装 disarm 不写设备、
  正式 `executed_unverified`；已安装但 prefs 不存在时停服务后写最小 prefs
  （非 root 先 `run-as mkdir -p shared_prefs`）；被拒/未知不整写、正式
  `failed`；probe 正式值一律 `failed`。
- 追加 `powercycle_disarm@1.0.6`（树与包 SHA 见 manifest）；1.0.5
  `retired:true`；ONBOARDED 最低版本、模板 pin、API/Worker 测试同步 1.0.6。
- 新增 `test_powercycle_disarm_v176_3730.py`：STOP 回写替身、顺序回退必红、
  四种读取分类、未安装零写入、absent 最小 prefs、完整 disarm 打戳。

该历史阶段尚未执行 1.0.6 的最终真机；1.0.5 的 12 台 E–H 与 D1/D2 只作对照。现行最终包为 1.0.8，证据见 Verification。


## 历史记录：v1.7.7 / 1.0.7（2026-10-20）

规划者核验 1.0.6 后指出两处读取规则与两项测试缺口（PR #3747，2026-10-20；
见 #3723 v1.7.7 §3.6.6 第 2 条与第 7 条 e、f）：

- 读取改为 **返回码优先**：rc=0 且输出含 `<map` 即判 `ok`，错误字样只在
  rc≠0 或无 prefs 内容时用于分类；`_root_read_prefs`、非 root
  `read_prefs_classified` 与 `get_prefs_xml` 同步。
- root 下空文件沿用 #2979：disarm 先停服务、删除空文件、写最小 prefs 并
  验证两项；probe 原始 `readback_failed`、`kind=empty`。
- 测试 e：写入后回写一次被验证发现并重写、持续回写以 failed 结束；
  “只验 running”的变异会漏掉回写。
- 测试 f：可读 prefs 的 string 值含错误字样仍判 `ok` 且不整写；root 空文件
  删除重建，probe `kind=empty`。
- 追加 `powercycle_disarm@1.0.7`；1.0.6 `retired:true`（SHA 不变）；
  ONBOARDED、模板 pin、API/Worker 测试同步 1.0.7；setup 与 finish@1.1.0 未改。

该历史阶段在规划者确认 1.0.7 前未开始真机；后续改为 1.0.8 并仅验 MLD_LX2、MLD_LX3，MLD_NX2 转 #3761。旧包结果只作对照，现行证据见 Verification。


## 历史记录：v1.7.8 / 1.0.8（2026-10-20）

U5 独立复核第 1 轮（PR #3747 comment 6291379603）判定 **需修改**；规划者回应确认
F1、F2 属清单内实施遗漏，F3 为真机材料欠项，F4 非阻塞。按 #3723 v1.7.8 §3.6.6
第 1、7 条与 §4.1 实施：

- **F1 服务停止按返回码确认**：`service_state` 返回 `alive`/`stopped`/`unknown`
  与 rc；查询 rc≠0（含超时）一律 `unknown`，不再当作已停。服务存活时 STOP、
  force-stop 的 rc 都必须为 0，且最后一次查询 rc=0 并无服务记录，才允许写停止标志。
  任一条件不满足抛 `StopNotConfirmed`（携带各步 rc），disarm 对 prefs 零写入、正式
  `failed`，evidence 的 `stop_steps` 记录各步返回码。
- **F2 finish 角色按登记表**：新增 `FINISH_COLLECT_ONLY_VERSIONS={"1.1.0"}`；只有登记
  版本判 collect，1.0.x 判 disarm，缺版本/不可解析/未登记数字版本（1.1.1、2.0.0）
  一律 `unknown_legacy_disarm` 并记 `unknown_legacy_version`。
- **测试**：g 项覆盖状态查询失败、STOP/force-stop 失败、STOP rc 非 0、末次仍存活
  四种组合（对 prefs 零写入、正式 failed、evidence 含 rc），并含「unknown 改回已停」
  的变异自证；F2 经真实 `_execute_teardown_best_effort(disarm_only=True)` 验证
  1.1.1/2.0.0 被执行且记 `unknown_legacy_version`，并含回退「≥1.1 即收取」的变异自证。
- 追加 `powercycle_disarm@1.0.8`；1.0.7 `retired:true`（SHA 不变）；ONBOARDED
  最低版本、模板 pin、API/Worker 测试同步 1.0.8；setup 与 finish@1.1.0 未改。
- 当时处于代码预审前，尚未开始最终包真机；1.0.7 结果只作对照。现行最终证据已绑定 1.0.8 包 SHA，见 Verification。


## 历史记录：v1.7.10 / 欠账转换后的完成重放（2026-10-21，§3.6.7）

独立复核第 1 轮判定「需修改」，规划者把 backend 全量的 1 个失败分流为 U3×U5
叠加出的集成缺口，本轮只做 §3.6.7，**不改脚本包**（1.0.8 的包 SHA
`98cc16f8…` 已确认，`backend/agent/scripts/powercycle_disarm/` 未改动）：

- **§3.6.7 第 1 条（只读重放）**：`agent_completion.complete_agent_job` 的已终态
  分支里，历史 JOB 租约行不存在（U3 原地转换把租约改成 MAINTENANCE 且清空
  `job_id`）时，改用「已接受终态载荷的摘要」识别重放：
  `job.terminal_payload_digest` 与本次载荷摘要一致 → 按只读重放返回 200
  （`idempotent=true`），不改任何事实、不记 `stale_job_completion_rejected`；
  摘要不一致 → 沿用现行规则（先迟到 confirmed，不满足再 409）；历史租约存在但
  令牌不符 → 仍 409。摘要为空（历史 job 未留摘要）时同样按现行规则处理。
- **§3.6.7 第 2 条（测试清理）**：`test_review_u2_recycler_disarm_v15_3727.py` 的
  清理改为先按**设备**删除租约再走共用清理，覆盖已转换、`job_id` 为空的欠账
  租约行（原来按 `job_id` 删会漏掉它，随后删 device 触发
  `device_leases_device_id_fkey`）。断言未删、未放宽。
- **§3.6.7 第 3 条（回归）**：新增
  `backend/tests/services/test_agent_completion_replay_u5_3730.py`（5 例，真实
  `/api/v1/agent/jobs/{id}/complete` 入口）：同载荷重放 200 且无新增审计、事实
  未变；不同载荷仍 409 `STALE_COMPLETION_TOKEN`；欠账转换后令牌不符但载荷相同
  的重放同样只读放行且不改事实、不记审计；历史租约仍在时令牌不符仍 409；
  迟到 confirmed 仍能关掉同源欠账。
- **变异自证**：撤回第 1 条 → 上述回归与 U2 用例共 3 failed；撤回第 2 条（清理）→
  U2 用例 1 failed（IntegrityError）；恢复后 15 passed。

该历史代码提交不产出真机证据；第 2 轮预审通过后已完成 1.0.8 真机，见 Verification。
