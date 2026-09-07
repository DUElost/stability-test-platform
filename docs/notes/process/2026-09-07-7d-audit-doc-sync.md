# 09-07 七日审计文档同步（map/apply 终态 / Honor runbook v1.3.10 / powercycle v1.0.6 / ADR 索引）

Status: implemented
Class: process

## Decision

把 2026-09-07 七日审计（08-31→09-07，先 HEAD `c3afca79` 后复核至 `8aa799e7`，只读）确认的
常驻文档漂移逐项修复。所有改动仅来自 HEAD 代码/迁移/提交可直接核对的证据，不夹带推测。

- **map/apply 大小写终态与 decision note 相反**（审计发现 1）：`docs/notes/simplification/2026-08-31-model-case-normalize-and-cleanup.md`
  记录 PR #661「写端统一大写 + join 全等」，同日夜已被 `0b5804e0`（PR #675）回归为
  **读端 `func.lower` 归一匹配 + 写设备事实原值 + IntegrityError→409 兜底**
  （`projects.py` 读端 `_map_preview`、写端 `model_facts.get(model, model)`），与兄弟文档
  `docs/notes/bug-fix/2026-08-31-map-apply-case-roundtrip.md` 矛盾且无 supersede 标注。
  已在文首加被取代标注，并把「决定 1 / 放弃的备选 / 如何验证 / 何时重议」四处的
  写端大写表述改为终态口径。
- **Honor runbook 样例仍钉 v1.2.0**（审计发现 2）：§0 前置已要求 `flash_firmware v1.3.10`
  （`418190a0`，08-31），§3 建 Plan 样例 `script_version` 与 §4 验证 curl note 仍写 v1.2.0
  （`e587e51c`，08-22 遗留）。已同步升 v1.3.10。
- **powercycle_check 判死语义文档停在 v1.0.5「最终验收 + 遗留」**（审计发现 3）：
  `05101efa`（08-31 夜）已以 v1.0.6 定稿「结果文件 mtime 停滞」判死，收口了
  `docs/notes/feature/2026-08-31-toolkit-android-tools-g15-alignment.md` 记为「遗留」的项。
  已在该 note 加 v1.0.6 增补段并指到边界 issue #761。
- **ADR-0029 修订记录缺 v2.5 行**（审计发现 7）：`29e48956`（08-31）写入 v2.5 派生归属
  重设计（§v2.5）但修订记录表止于 v2.4，违反本文自带传播清单。已补 v2.5 行。
- **plan_run chain docstring 单跳旧契约**（审计发现 6）：`f3bdcca5`（09-01）后 chain 沿
  `next_plan_id` 展开到链尾、每未触发链节一个 pending 节点，docstring 仍写「0..1 个」。
  已同步为「0..N 个，仅链首承载 is_blocked/block_reason」。
- **DOC-MAP execution-contract 行 Living v1.0**（审计发现 5 残面）：契约正文已 Living v1.1
  （§9 启动判据 09-07 增补 + §1.2 `branch`），DOC-MAP 该行未随。已升 v1.1。

未在本 PR 处理的审计发现 4/5 主体（adr/README 的 ADR-0032 v0.6、ADR-0034 v1.0，DOC-MAP
的 ADR-0034 v1.1，ADR-0034 状态行 v1.3）已由并行 PR #920（fix/867-adr-index-drift-gate，
S12 门禁 + 9 处存量同步）覆盖，避免同批文件重复修改；本 PR 只补 #920 未覆盖的 DOC-MAP
execution-contract 行。

## Alternatives

- 整篇重写 dated decision note 使其只含终态：保留历史决策记录是仓库惯例（挂起/被取代
  语义原样保留），故采用「文首 supersede 标注 + 逐处表述改终态」而非删除历史。
- 把 §3/§4 样例标注为 v1.2.0 历史快照而非升版：runbook 是「建计划 → 单台验证」的现行 SOP，
  样例即模板，升到 §0 要求版本更符合操作语义。
- ADR-0029 只在头部状态行补注而不加修订行：违反本文传播清单（修订记录即本文），已按
  清单补行。

## Verification

- 未触碰 #920/#921 并行分支的文件集（除 DOC-MAP 单行 L85，与 #920 的 L84 行不重叠）；
  两个 worktree（/tmp/stp-867、/tmp/stp-868）的 diff 已实读核对。
- `python scripts/run_gates.py check:quick` 与 `python -m compileall`（plan_runs.py 变更面）
  结果见提交前实际输出。
- diff 复核：无凭据/内网地址/序列号，无无关格式化；改动文件逐一显式列出。

## Revisit

- #867 关闭前复核 ADR-0034/0032 索引行与 DOC-MAP 是否已到正文版本（#920 合入后）。
- #761（powercycle v1.0.6 判死边界）与 #830（teardown 排队）合入后，g15 note 增补段如需
  指向新版可再补注。
