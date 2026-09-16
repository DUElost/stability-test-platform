# 低危杂项批 4 处：展示面缺字段不得崩 / exposition 控制字符 / kind 静默猜身份 / aee 文档漂移（#2016）

Status: implemented
Class: bug-fix

## Decision

四条同源不同面：**「局部取不到值」被表达成了「整体不可信」或「另一个真值」**。逐条裁决：

### 1. `ProcessMemorySection` 缺字段 → 空态，不是整页崩溃

`frontend/src/pages/storage/FileServerPage.tsx:372` 的 props 改为
`processes?: ... | null` / `totalSeries?: ... | null`，组件内 `processes?.items ?? []`、
`processes?.error`、`processes?.available`、`series = totalSeries ?? []`。

根因不是类型写错，而是**部署次序**：静态 `dist-prod` 可以比运行中的后端新（控制面不自动重载，
`deploy/control-plane/README.md` 明确要求重启进程），旧响应里这两个字段**根本不存在**。
`frontend/src/utils/api/types.ts` **刻意不动**——硬不变量是前端 API 类型与后端 schema 同步，
当前后端确实必发这两个字段；把契约改成可选会掩盖真实契约，也会让新后端的消费方失去护栏。

### 2. `stp-mem-top.sh`：在取值处压掉 LF/CR/TAB，一处覆盖两处 sink

新增 `stp_sanitize`（`deploy/control-plane/node-exporter/stp-mem-top.sh:39`），对
`comm`/`unit`/`cmdline`/`name` 四个取自外部的值在赋值后立刻规范化（`\n`/`\r`/`\t` → 空格）。

- 为什么在**取值处**而不是 exposition 渲染处：同一批值还写进 `stp-mem-top.tsv`；换行会
  把一行日志变成多行，`read -r key anon` 又会在首个 TAB 处错切列。只救 Prometheus 一处，
  TSV 仍是坏的。
- 为什么连 TAB 一起做（issue 只点名 LF/CR）：TAB 是同一个键值对的同类注入面，且
  `name` 来自 argv0 基名，与 `comm` 走同一条 `key="${name}|${unit}"`。留一半等于留一条通路。
- `\` 与 `"` 的转义**保持原样**：cgroup 名里的 `\x2d` 是 systemd 的真值（本机 10 条序列里
  就有 1 条含 `\\`），不是待清理的控制字符。
- 实现用 `printf -v` 就地改写命名变量而非 `$(stp_sanitize ...)`：本函数每 pid 调 4 次，
  timer 每 2 分钟一轮，热循环里不 fork。

### 3. `read_artifact_digest`：表外 kind 是「读不到」，不是「读另一份身份」

`backend/agent/version_info.py:19` 改成显式全集 `_ARTIFACT_DIGEST_FILES`，未知 kind
→ 返回 `""` 并 `logger.warning`。

原实现 `filename = "ARTIFACT_DIGEST" if kind == "code" else "ARTIFACT_DIGEST_RESOURCES"`
把 `"resource"`/`"full"`/`""` 全部静默导向 **resources 那份真实摘要**——报出去的是一个
看似合法的身份，控制面据此算 aligned/drift。**错值比缺失更坏**（缺失会走
`resolve_agent_code_sync_status` 的 `unknown`，错值会给出「对齐」的假结论）。
不抛异常的理由与 #2320 同族：一个展示/心跳字段不该把局部失败升级成 500 或心跳中断。

顺带发现：**写侧早已 fail-closed**（`backend/agent/stp_agent_priv.py:738`
`if kind not in _DIGEST_FILENAMES: _fail(...)`），只有读侧在猜——本单让两侧同判据。
候选目录提出为 `_ARTIFACT_DIGEST_DIRS` 常量（行为不变），否则「不读另一份身份」无用例可钉。

### 4a. `backend/agent/aee/AGENTS.md` 的 UNISOC 路径与 `detect` 双重陈旧

`:19` 与 `:52` 仍写「detect 探测 `/data/uniview` + `/data/vendor/uniview`，读
`unievent_info.json`」。真值（`collectors/unisoc.py:45`、`unisoc_reconciler.py:37`、
`collector.py:33` 的 Protocol docstring）是：

- 事件根 `/data/ylog/uniview_exception/{Type}.{event_id}/unievent_info`（**JSONL**，无 `.json`；
  旧文件名仅兼容回退，真机从未观测到）；`/data/uniview` 是**框架侧**目录，真机从未在其下
  出现事件目录 → 旧文档描述的实现**恒采不到**（#73）；
- `PlatformCollector` 协议**只有** `parse_metadata`，`detect` 已随 R4-a a1 删除（全仓零调用点，
  平台判定唯一权威是 `backend.agent.device_platform`）。文档里连「QCOM stub 的
  `detect→False`」都是不存在的返回。

### 4b. 漂移指标 label：历史 Note 不覆写，追加复核段

`docs/notes/feature/2026-09-13-counter-drift-metrics-77.md:10` 的
`{plan_run_id, mode}` 已被 #1927 收敛为 `{mode}`。本仓约定历史记录不追改
（`docs/notes/bug-fix/2026-09-15-docs-drift-batch-2037-2043.md`：「属历史记录（非现行指引），
不追改历史」），故在文末追加「复核（#2016）」段指向现行口径
（`docs/operations/adr-0026-admission-and-scale-gray-rollout.md:247` + `backend/core/metrics.py`），
不改动当时的决策正文。operations 侧那一处已正确，无需改。

## Alternatives

- **1 · 把 `types.ts` 的两个字段改成可选**：否。它描述的是当前后端契约，改了等于用前端类型
  给部署事故让路；且新后端的消费方会一起失去护栏。
- **1 · 在调用点补 `?? { available: false, error: null, items: [] }`**：否。空态形状会散到
  每个调用点，多消费方后各自漂移；组件是唯一持有该语义的地方。
- **2 · 按 exposition 规范转义成 `\n`**：不选。转义只救 Prometheus 一处，TSV 那处仍会断行；
  且 comm/cgroup 里的换行不是需要还原的信息，规范化成空格在两处都成立。
- **3 · 未知 kind 抛 `ValueError`**：否。调用点在心跳与状态上报路径（`backend/agent/main.py:1134`），
  抛错把一个展示字段变成上报中断。
- **3 · 只加 `Literal["code","resources"]` 不加运行时判据**：否。`tsc`/类型检查不覆盖
  运行期传入的动态值，静默兜底会原地复现。
- **4b · 另开独立勘误文件（ADR-0037 v0.2 先例）**：否。那用于方向级裁决；本条是一行 label
  事实，追加复核段与问题比例相称。
- **合并 `_DIGEST_FILENAMES`（写侧）与 `_ARTIFACT_DIGEST_FILES`（读侧）为一份**：本单不做。
  写侧在 privileged wrapper 内（有自己的测试与 `--fail` 语义），跨面重构超出本 Requirement；
  记入 Revisit。

## Verification

实跑（worktree `.wt/stp-2016-misc-batch`，起步 base `b7917bc6`；下列前 6 行在该 base 上跑，rebase 到 `112212da` 后复跑标注为「rebase 后」）：

- `pytest backend/agent/tests/test_version_info.py -q` → **4 passed**（新增 3 条）；
- `pytest tests/test_stp_mem_top_label_escaping.py -q` → **4 passed**（新文件）；
- `pytest backend/agent/tests/ -q` → **2107 passed**（重构无涟漪，rebase 前）；
- `pytest tests/ -q` → **1188 passed**；rebase 后 `pytest tests/ backend/agent/tests/test_version_info.py -q` → **1198 passed**；
- `npx tsc --noEmit -p tsconfig.json` → exit 0；`npx eslint` 两个改动文件 → 无输出（exit 0）；
- `npx vitest run src/pages/storage/FileServerPage.test.tsx` → **8 passed**；
- `bash -n stp-mem-top.sh` → OK；脚本在临时目录真跑 `exit=0`，10 条序列 + total 全部
  通过严格文法（含 1 条带 `\\x2d` 转义反斜杠的真实行）；
- `ruff check backend/agent/version_info.py tests/test_stp_mem_top_label_escaping.py` → passed；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**（两个 base 各跑一次）。

**破坏性对照（红绿双向，逐条还原）**

| 对照 | 预期 | 实测 |
|---|---|---|
| 组件还原成修复前（无 `?.`/`??`） | 新用例红 | 红：`TypeError: Cannot read properties of undefined (reading 'items')`，其余 7 条仍绿 |
| 只把读侧改回 `"code" if ... else` | 身份用例红 | 红：`kind="resource"` 返回 `sha256:bbb…`（resources 身份）——正是本单故障 |
| 摘掉 `stp_sanitize comm/unit` 接线 | 接线用例红 | 红：`test_every_externally_sourced_value_is_sanitized` |
| 删掉 sanitize 里的 LF 分支 | 行为用例红 | 红：`test_sanitize_strips_every_line_or_column_breaking_char` |
| 单验文法 oracle 判别力 | 畸形行必须被拒 | 4 类畸形（裸 LF / TAB / 未转义引号 / 少 label）全部拒收；10 条真实行全部接受 |

## Revisit

- **两份 kind→文件表并存**：`backend/agent/version_info.py`（读）与
  `backend/agent/stp_agent_priv.py:726`（写）。出现第三处引用时必须合并到单一模块，
  否则又会开始各自漂移。
- **同型风险未全覆盖**：`FileServerPage` 其余区块仍假定 `data.control_plane`/`data.history`
  存在。真正的通用解是「按后端实际版本降级」，属 #2341（build_info 报真实版本）落地后的事。
- **降级与「真没装采集器」不可区分**：后端未重启时进程内存面板会显示
  「未检测到进程内存采集器（stp-mem-top.timer 未部署或未运行）」——这是有意的空态，
  但操作员无法据此分辨两者。要分辨需要版本通道（同上，#2341）。
- `stp_sanitize` 只处理 ASCII 控制字符；U+2028/U+2029 之类的行分隔符不在内
  （Prometheus 文本协议按 `\n` 分行，故不构成同类注入，仅记录）。
- **未做**：#2320（`GET /hosts` 现算 digest 无兜底）与本批第 1 条同族，但
  `backend/api/routes/hosts.py` 此刻由 #2229 在窗改动（真实 diff 73 行），留待其合入后另开。
