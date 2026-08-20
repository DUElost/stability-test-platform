# MTBF P0 实施：脚本三件套 + validate 端点 + 冒烟 Plan

- 日期：2026-08-20
- 类型：feature
- 上游：[ADR-0030](../../adr/ADR-0030-multi-case-suite-management.md) D6 P0、[P0 设计](../../design/2026-08-mtbf-p0-runner-design.md)
- 背景研究：[MTBF_MULTI_CASE_RESEARCH_2026-08-19](../../reviews/MTBF_MULTI_CASE_RESEARCH_2026-08-19.md)

## 决定了什么

1. **脚本三件套** `mtbf_setup`（init：部署/启动）/ `mtbf_check`（patrol：轮询 + PROGRESS + 停滞钟）/ `mtbf_finish`（teardown：停止/收取/解析），
   自 `stability_MTBF-Test/scripts/*.ps1 + lib.ps1` 逐函数移植为 Python。**当前版本（2026-08-20 冒烟后）**：
   `mtbf_setup` **v1.3.0**（adb root fail-fast 前置）、`mtbf_check` **v1.2.0**、`mtbf_finish` **v1.3.0**（adb pull 目录层级修正）。
   版本演进：setup v1.0.0 → v1.1.0（env 回退）→ v1.2.0（resources 路径相对化）→ v1.3.0；finish v1.2.0 → v1.3.0（均按 ADR-0020 新建版本，不原地改）。
   **注意 catalog 的 `is_active` 语义**：scan（`script_catalog.py:276`）会把磁盘存在且 sha 一致的版本重新置 active——
   「active」= 磁盘存在性而非「当前推荐版本」；已发布目录按 ADR-0020 保留在磁盘 → deactivate 端点只对
   **已从磁盘删除**的版本有效。计划按 name+version 精确引用 + sha 校验，多版本 active 无功能影响。
2. **配置解析顺序 = `STP_STEP_PARAMS` > `STP_MTBF_*` env > 代码默认**（`_lib.py:param_or_env`）。
   实测平台惯例：scan 注册的脚本 `default_params` 恒为空（既有脚本全部 `{}`），且逐计划参数通道不存在
   （ADR-0029 D1 挂起）——原设计「default_params 预置 expected_testpoint_count」不可行，改为部署级 env 注入。
   P1 的 suite 绑定（ADR-0030 D2）不变。
3. **`@@var` 引用校验降级为 advisory warning**（`GLOBAL_REF_CUSTOM` / `OSM_FIXED_REF_UNVERIFIED`）。
   反编译实锤：OfflineScriptManager 的固定 `@@` 清单字段只读不写（解析结果为空串），`@@g*` 由测试 APK 消费——
   「引用必须在 global_params 有定义」会误伤真实文件（`@@gWifiName` vs SIM 键 `wifiName`）。
   自定义引用按 g 前缀约定做大小写不敏感命中推断（advisory）。
4. **结果回填**：摘要 metrics + `suite_sha256` 走 step_trace（规避 64KiB 截断）；逐条结果写
   `{STP_AEE_NFS_ROOT}/mtbf/{project}/results/{run_dir}.json`（P2 `test_case_result` 数据源）；不扩 artifact 白名单。
5. **realresult schema 定稿**（反编译 + 真机实样）：单文件/单运行、`id` 恒 0（name 为 join 键）、按轮追加同名多条、
   INCOMPLETE 落盘进 `<error>`、进度计数用 `<testpoint `（尾空格排除根元素）。
6. validate 端点 `POST /api/v1/mtbf/runtask/validate`：multipart 主路径 + JSON path 备选；只读校验、登录即可。
7. **resources 默认路径相对 Agent 目录**（`_lib.py:_default_resources_root`，aimonkey_paths 先例同构）：
   部署布局为 `/opt/stability-test-agent/agent/resources/mtbf/{project}/`（含 `agent` 层级）。
8. **hot-update rsync `--delete` 修复**（冒烟 #214/#216 根因，平台缺陷）：`agent/resources/mtbf/` 里的 APK 三件套
   不在仓库 tarball 内，hot-update 的 rsync `--delete` 会把该目录整目录清掉。已在
   `backend/services/host_updater.py` 的 rsync excludes 增加 `resources/mtbf/`（含单元测试）；
   依赖该修复，APK 布放与 hot-update 的顺序不再敏感。
9. **env 通道分档（部署说明见 `docs/operations/mtbf-api.md` §1.5）**：
   - `STP_MTBF_EXPECTED_TESTPOINT_COUNT` → **fleet 同步**（`_FLEET_ENV_KEYS`，控制面 `.env.backend` 设置，hot-update 下发全 fleet）；
   - `STP_MTBF_TASK_TIMES` → **host 级手工 .env**（故意不进白名单：冒烟=1、生产=100、未来相机套件按项目分化）；
   - 改后需 `systemctl restart stability-test-agent.service`（env 在进程启动时读入）。

## 放弃的备选

- **default_params 预置参数**：平台无此通道（scan 注册即 `{}`，版本内不可变）→ env 注入。
- **`@@var` 未定义为 error**：对真实文件是假阳性 → warning。
- **逐条结果塞 stdout**：`_MAX_STEP_OUTPUT_CHARS = 64KiB` 截断风险 → NFS JSON。
- **v1.0.0 原地修正**：违反 ADR-0020 版本不可变 → 新建版本，旧版本停用。
- **mtbf_check 从 NFS runtask.xml 自推 expected 数**：可消除 env 漏配类故障，但改动面更大；P0 维持 env 通道，
  P1 suite 绑定（ADR-0030 D2 派发注入）一并解决。

## 如何验证

- 单元：`test_mtbf_suite.py`（23）+ `test_mtbf_scripts.py`（**25**：含 setup v1.3.0 root preflight 3 例、
  finish v1.3.0 pull 层级 2 例）——golden 用真实 runtask.xml 快照（130/137/5 多 testcase、CRLF、`@@g*` 推断）；
  realresult 样例覆盖 PASS/FAILURE/ERROR/多轮/回归/电量。
- API：`test_mtbf_validate.py`（9，testcontainers PG）+ 鉴权回归注册表（73）——multipart/JSON 两种输入源、401/422/400/413。
- 平台侧修复：`test_host_updater.py`（rsync exclude 断言）+ `test_agent_env_sync.py`（EXPECTED 同步 / TASK_TIMES 不同步）。
- 门禁：ruff 0；agent 全量 **1073**（含 v1.3.0/v1.4.0 新增用例）；相关 backend API 104。
- 集成（生产控制面）：scan 注册 3 脚本（setup/finish v1.3.0、check v1.2.0；catalog active 语义见决定了什么 #1）；
  冒烟 Plan「MTBF-专项-冒烟-P0」(id=10) 三步（init 900s / patrol 300s+stall 600s / teardown 3600s，barrier 1800s）；
  真机冒烟 PlanRun #214~#218（见下节）。

## 何时重议

- P1（用例实体 + suite 绑定）落地时：env 预置让位 dispatcher 注入，删除 `STP_MTBF_*` 回退。
- 真机冒烟收尾（#218 finish JSON 落盘）后：复核 realresult schema 与解析器（设计 §6 冒烟闭环）——
  ✅ 已做（38/38 逐条一致，见收尾记录）；P1 建 `test_case_result` 时以 NFS JSON 为数据源做二次抽样。
- 若 OfflineScriptManager 新版本接线了固定 `@@` 清单 → `OSM_FIXED_REF_UNVERIFIED` 可升级为 error。
- 若真机轮仍大量快速失败：查用例前置条件（wifi/SIM/`@@g*` 解析——validate 的 GLOBAL_REF_CUSTOM advisory 相关）。

## 2026-08-20 部署执行（已落地，勿重复部署）

| 项 | 结果 |
|----|------|
| `stability-backend.service` 重启 | ✅ 已重启，validate 端点线上验收：multipart 200（valid/130/`@@g*`/advisory）/ JSON path 200 / 未鉴权 401 |
| hot-update（脚本同步） | ✅ 34/34 host `agent_code_sync_status=matched`（2026-08-20 晚些完成，含 9-93 重试） |
| APK 三件套 | ✅ 34/34 host sha 匹配（`e626ad00e845 25f6f0cd564d 6852139d452a`；74/75 在 rsync 修复后补布） |
| env | ✅ `STP_MTBF_EXPECTED_TESTPOINT_COUNT=130` 34/34（fleet 同步）；⚠️ `STP_MTBF_TASK_TIMES=1` 12 台（见遗留 #1） |
| 中心存储 | ✅ `{STP_AEE_NFS_ROOT}/mtbf/legacy/`（runtask.xml + UiAutomatorTestData.xml）已同步；`results/` 写权限实测通过 |

## 2026-08-20 冒烟执行（第 1~2 轮：记录与根因）

| PlanRun | 结果 | 根因 / 结论 |
|---------|------|-------------|
| #214（用户触发） | FAILED ~15s | **根因①**：hot-update 的 rsync `--delete` 清空 `agent/resources/mtbf/`（APK 不在仓库 tarball）→「APK 不存在」。平台缺陷，已修（决定了什么 #8） |
| #215（用户触发） | FAILED ~20s | **根因②**：device 1（A2WENX6628000033，MLD-LX3，`ro.debuggable=0`）user 构建无法 adb root → prefs push `rc=1`。设备资格前置不满足，非平台缺陷 |
| #216 | FAILED ~15s | 同根因①（8-195 hot-update 后 APK 被清，当时未修复）→ 触发 rsync 修复 + 全量补布 |
| #217 | init ✓ → patrol ✓ → abort → teardown finish ✗ | **整条 init→patrol→teardown 链路首次跑通**：setup 28s（v1.3.0 preflight、APK、prefs、RunTaskService、run_dir）；check seq=1..3 + PROGRESS + patrol-heartbeat；设备端一轮 130 testpoint 全量跑完（**全 FAILURE：`UiAutomationService ... already registered`**——设备残留 UiAutomation 连接，重启设备清除）；abort → teardown → **finish 暴露 pull 路径 bug**（`adb pull` 目录保留远端末级名 → v1.2.0 定位 `local_dir/run_dir` 缺 `realresult/` 层）→ finish **v1.3.0 修复** |
| #218 | **FAILED（abort 收尾，验收通过）** | 设备重启后用例真实执行（首轮 38 条：**30 PASS / 8 FAILURE**）；abort → teardown → **finish v1.3.0 成功落盘** NFS JSON（`mtbf/legacy/results/2026.08.10_18.46.43.036.json`）；**§6 复核 0 不一致**；suite_sha256 与 #217 一致（649d8d2d…） |

**关键实证（冒烟教训）**：

1. **设备资格**：MLD-LX2（`use` 构建 + `ro.debuggable=1`）→ adb root 可用（395 实测 uid=0）；
   MLD-LX3（`use` + `ro.debuggable=0`）→ 不可 root（device 1）；Z2581（usedebug）可 root 但项目不符（APK↔项目严格对应）。
2. **运行结束协议（重要）**：patrol 循环无完成判定（plan/步骤 timeout=0 不限时）——设备端跑完后
   **用 plan-run abort 结束**（`POST /api/v1/plan-runs/{id}/abort`，teardown→finish 收结果）；
   **不要用 manual-exit（EXIT_REQUESTED）**：ADR-0022 BO4 规定 manual_exit 跳过 teardown。
3. **宿主时钟不稳**（78 被 NTP 回拨 ~26min；74/75 慢 ~15h）：patrol 周期按宿主墙钟 sleep，回拨会拉长周期、
   日志时间戳错乱——勿按日志静默判「卡死」；以 `/tmp/mtbf_check_{serial}.json` 的 `seq` 判定循环推进。
4. **设备时钟慢 ~9 天**：run_dir 命名 `2026.08.10_*` 系今日目录（设备 RTC 落后），勿按名字误判为历史数据；
   同一天内按名字字典序仍可正确取最新。
5. **step_trace 观察以 DB 为准**（`step_trace` 表，`step_id` 列）：patrol 成功轮默认不落 trace
   （`suppress_success_trace`）；teardown trace 在 job 已终态后上传会被拒（`upload_rejected_ack`）——
   run 终态后查 finish 结果直接看 NFS JSON。
6. **设备端单轮时长取决于用例质量**：快速失败轮 ~5min（130 × ~2.5s）；真实用例轮 6~8h。
7. **fleet 补洞脚本坑**：`while read ... | ssh` 会因 ssh 吞 stdin 只处理第一行——批量 SSH 循环用
   `for ip in $(cat ...)` 或 `ssh -n`（本 Note 两次踩坑，勿再犯）。

## 冒烟收尾记录（#218，已完成）

按判据执行：`POST /api/v1/plan-runs/218/abort` → run 终态 FAILED（aborted=1）→ teardown 执行 →
**finish v1.3.0 成功**（pull 路径修复生效）→ NFS `mtbf/legacy/results/2026.08.10_18.46.43.036.json` 落盘
（`entries=38, passed=30, failed=8, error=0`）→ **§6 复核**：设备端 XML 与 JSON 逐条比对
**38/38 顺序+状态一致**（PASS/FAILURE 派生、name join 键在真实数据上验证通过）。

- 平台侧证据链：init trace `suite_sha256`（#217/#218 同值）✓；check PROGRESS + patrol-heartbeat ✓；
  abort→teardown 协议 ✓；finish 结果落盘 ✓。**ADR-0030 D6 P0 真机验收判据全部达成。**
- **finish v1.4.0**（收尾后新增）：metrics/JSON 补 `suite_sha256`（与 init trace 闭环，对齐设计 §3.5）；
  Plan 10 已引用 v1.4.0，host 78 已 hot-update；后续轮次 JSON 自带 suite 关联键。
- 已知平台行为（非缺陷）：teardown step_trace 在 job 终态后上传会被拒（`upload_rejected_ack`）——
  收尾后 finish 的权威记录是 NFS JSON，不是 DB trace。

## 遗留（运维跟踪，按优先级）

1. ~~**`STP_MTBF_TASK_TIMES=1` 回滚**~~ —— ✅ **已完成**（12 台 → `=100`，.env + 进程环境双重验证，2026-08-20）；
2. ~~**冒烟 #218 收尾**~~ —— ✅ **已完成**（abort → finish JSON → §6 复核 0 不一致，见上节）；
3. **宿主机时钟同步**（74/75/78 等 drift/回拨，lab NTP 问题）——建议 Ops 处理，影响 patrol 周期准确性；
4. **设备「UiAutomationService already registered」**：重启设备可清；若复现需查 OSM/test APK 的
   UiAutomation 资源释放（工具链侧，非平台）；
5. 真机轮用例质量：首轮 38 条 30 PASS / 8 FAILURE（正常波动）；批量跑时关注失败率与失败模式分布；
6. **代码合入**：`mtbf_finish/v1.4.0/`（suite_sha256）与本文收尾段落为 `4bbfefd` 之后的新变更，
   随下次 commit 合入（含 Plan 10 引用 v1.4.0、host 78 已 hot-update 的部署状态）。
