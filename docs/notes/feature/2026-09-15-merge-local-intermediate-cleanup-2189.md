# merge 执行位置无状态化（I-13 方案 A）：本地中转的删除与兜底清理（#2189）

Status: implemented
Class: feature

## Decision

按 owner 裁决「I-13 先做 A（无状态化）」落地，**但走的是提案 Revisit 预留的兜底分支**——
因为关键前提经代码核实**不成立**：

> 方案 A 原文是「merge 工具产物落到**中心 staging**（`{center}/_staging/merge/{run}/`）」，
> 代价栏写着「依赖工具支持指定输出目录；否则仍需本地中转（收益打折）」。
> **实测：不可指定。** `run_merge_sync` 以 `cwd=工具脚本父目录` 调起工具，工具固定写
> `{工具目录}/merge_result/{ts}/`；代码只能靠**调用前后快照差异**（`_merge_output_dir_names` +
> `latest_merge_output_mtime`）识别新目录（`backend/services/dedup_scan.py`）。中心 staging 因此拿不到。

于是本单把 A 段落成三层（"本地中转 + 保留期清理"）：

| 层 | 行为 | 代码 |
|---|---|---|
| 成功路径 | 发布到中心**且**登记成功后，**立即删除**本机 `merge_result/{ts}/` | `_discard_local_merge_output`，调用点 `run_merge_sync` |
| 失败路径 | **保留**本机目录（#1074 可重试的前提，也是现场诊断对象） | 删除点在 `raise` 之后，失败走不到 |
| 兜底 | 超期（24h）残留清理，**仅中心已配置时**执行 | `sweep_stale_local_merge_outputs`，在 `run_merge_sync` 持锁后调用 |

关键约束（都在代码注释里写明，避免后来者"顺手清理"）：

- 删除**只在 `published is not None`**（中心已配置）时发生：中心未配置时 `artifact_dir == latest`，
  该目录**就是交付物本身**，删了即毁产物；
- 清理**不是交付前提**：`_discard_local_merge_output` 失败只 warning（产物已在中心且已登记），
  不把一次成功的 merge 变成失败；
- 兜底只挑**含 `Result_MergeFiles*.xls` 的子目录**（工具产物形态），锁文件 `.stp_merge.lock`
  与其它内容一律不碰；
- 保留期是**模块常量**（`_MERGE_LOCAL_RETENTION_HOURS = 24.0`）而非 env 键：它只影响失败残留的
  清理节奏、不改变任何交付语义；升格为配置项须同步 `backend/.env.example` 与
  `docs/development/environment-variables.md`（env-inventory 门禁）。

**判据 E-3**：稳态下控制面本机 `merge_result/` 无产物目录——测试直接断言
`ds._merge_output_dir_names(merge_root) == set()`。观测手段沿用 `measure_center_storage.py`
（它已报"本地 `merge_result` 存在/缺失"）。

### 锁语义重审的结论：**保留**

提案 §3 写「锁的语义随之重审」。重审结论是**必须保留**：工具的产物落点是**共享**目录
`{工具目录}/merge_result/`，而「快照 → 子进程 → 收割 → 发布 → 登记」全程必须跨进程串行
（#1072 / R10-F03：两轮交错会误绑另一 PlanRun 的产物目录）。**去锁的前提是工具能指定输出目录**，
那属方案 B（专用 worker/Agent 侧执行）+ ADR-0033（工具宿主，未落地）——不是本单能消掉的东西。
本单因此不动锁，只把"为什么 A 段不消除锁"写进注释与 note。

**涉及**：`backend/services/dedup_scan.py`（+ `backend/tests/services/test_dedup_scan_merge.py`）。
**无契约变更**、无 env 变更、无迁移。

## Alternatives

- **直落中心 staging**（A 段原文）：**不可行**。工具输出路径固定，无法从调用侧指定（见上）。
- **改工具让它支持输出目录**：**不做**（本单）。工具在另一仓/交付链上，且"工具宿主模型"属
  ADR-0033 域（提案 §6 评审清单第 7 条正是这个依赖顺序问题）——不能塞进本单。
- **只做成功路径删除、不做兜底**：**否决**。失败重试会不断产生新的 `{ts}/` 目录（每次重跑都新建），
  没有兜底就是"失败越多、本机越胀"——E-3 在失败场景下永不成立。
- **失败路径也删**：**否决**。会同时毁掉"可重试"（#1074 的失败语义）与现场诊断依据。
- **保留期升格为 env 键**：**本轮不做**。它只影响失败残留的清理节奏；升格会牵动 env 文档与
  env-inventory 门禁，收益不足。触发条件见 Revisit。
- **中心未配置时也清理**：**否决**。那时本机目录是交付物（artifact 直接指向它），清理等于丢报表。
- **把兜底做成定时任务**（cron/SAQ）：**否决**（本轮）。合并路径本身每个 plan_run 都会跑，
  顺带清理的边际成本≈0；独立定时器要新增调度面与观测面。

## Verification

- `pytest backend/tests/services/test_dedup_scan_merge.py` → **42 passed**（+3 新用例，
  另给既有 #1074 用例补了断言），新增覆盖：
  - **成功路径**：中心已配置 → 登记指向**中心**路径、中心副本存在、**本机目录已删除**、
    `_merge_output_dir_names() == set()`；
  - **中心未配置**：本机目录**保留**且 artifact 指向它（删了就毁产物）；
  - **发布失败**：#1074 用例补断言 → 本机目录**仍在**（可重试）；
  - **兜底**：48h 前的产物目录被删；新鲜目录、非产物目录（`notes/`）、锁文件均**不**被动。
- 组合复跑（我的用例 + `test_dedup_extract.py` + 该 host 退役用例）→ **64 passed**。
- 扩大范围 `backend/tests/{services,api,tasks}` → **2146 passed，1 个与本改无关的偶发**：
  `test_host_retirement_read_filters_1804.py::TestStatsFaces::test_file_server_active_hosts_exclude_retired`
  ——单跑**通过**，且在我的用例之后跑也**通过**（顺序/环境类，非本改回归；本改与该代码路径零交集）。
- `run_gates check:pr` → **[OK] 18 gates**。
- **一次真实红灯（记录）**：两个新用例首跑 `NameError: name 'Path' is not defined`——该测试文件
  对 `pathlib` 无模块级导入、既有风格是函数内局部导入，照抄后即绿（不是改断言绕过）。

## Revisit

- **方案 B 的优先级需重新评估（本单的必然后果，不是可选项）**：提案 §Revisit 第二条写明，
  一旦 A 受工具能力限制而退回后备形态，就要「**重新评估 B 的优先级**」。本单正是这种情况——
  A 的收益被打折（staging 拿不到、锁也没能去掉），所以"彻底解耦控制面"的 B 的相对价值上升。
  该评估属方向级，需 owner 裁决，**不在本单内**（B 必须与 ADR-0033 一起推进）。
- **工具将来支持指定输出目录**（或方案 B 随 ADR-0033 落地）：本机制应退化为"直接落中心 staging"，
  同时**删掉**成功路径删除与兜底清理——保留它们会让"本地中转"这个临时形态固化成事实标准。
- **E-3 实测仍 > 0**（`measure_center_storage.py` 显示本机有产物目录）：说明存在**别的写入方**
  （例如人工直接跑工具、或旧版本进程），先查清来源再决定是否需要更强的清理，不要先加清理频率。
- **失败残留量超出预期**（例如每轮都失败、24h 内堆积很多）：把保留期升格为 env 键，并把
  "清理掉的目录数"纳入观测（当前只有日志行 `merge_local_stale_swept`）。
- **若方案 B 立项**：本单的删除/兜底逻辑应作为 B 的过渡策略保留一段，并在 B 落地后一并移除。
