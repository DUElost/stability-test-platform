# 脚本评审 checklist 固化到版本契约文档（#705）

Status: implemented
Class: process

## Decision

#507 的 B 节（新增脚本准入项）此前只存在于 issue 正文里，没有落在评审时能读到的
文档位。本单把四条准入项固化为
[`docs/development/script-versioning.md`](../../development/script-versioning.md)
新增一节「新增脚本评审 checklist（#507 B 节）」：

1. 行为随设备而变的脚本**自行读指纹路由**，不得要调用方按机型传参（`step.params` /
   `default_params` / 多 Plan / 多脚本版本都不行）；
2. 路由未匹配**一律 fail-fast**，错误信息带实测指纹值 + 已知集合（闭集白名单，
   不得静默落默认分支）；
3. **路由决策进 step_trace**（依据字段 / 实测值 / 选中分支 / 选中版本）；
4. **指纹来源差异鲁棒**（`getprop` 连字符 vs `adb devices` 下划线）。

为什么放这份文档而不是另起一份清单文件：该文档已是 CLAUDE.md / AGENTS.md
「按需入口 → 脚本版本、参数与退役」的权威落点，评审新脚本版本时必然打开；
另起文件需要同时改 DOC-MAP 与入口表（当前 DOC-MAP 有在窗 Execution 声明），
收益仅是目录美观。**没有改 DOC-MAP.md / AGENTS.md / CLAUDE.md 等共享元文件**，
避免与并行 Execution 撞车。

**引用复核**：checklist 里的样板引用按当前主线版本
`flash_firmware/v1.3.14/flash_firmware.py` 逐行核对（fail-fast :1222-1228、
`metrics.route` :1091/:1128-1132/:1284-1292、双拼写 :67/:170-173/:585-594）——
已发布脚本版本目录不可变，这些行号不会漂移。

## Alternatives

- **新建 `docs/development/script-review-checklist.md`**：弃——见 Decision（入口分散 +
  必须动共享元文件），且 #507 原文建议就是「固化为脚本评审清单的**一节**」；
- **写进 ADR-0020（脚本目录契约）**：弃——ADR 记录的是决策与权衡，不是随评审演进的
  操作清单；把 checklist 塞进 ADR 会让 ADR 变成活文档，与 ADR 的 immutability 语义冲突；
- **写进各脚本目录的 README/AGENTS.md**：弃——约束是跨脚本的准入面，分散到目录后
  新脚本作者看不到；
- **同时把 A 节（存量普查）与 C 节（触发点）也搬进文档**：A 节是已完成的普查任务
  （结果在 #507），搬进来会变成过期快照；C 节作为「触发点」已随 checklist 同节收录，
  保留其「何时优先怀疑约束被绕过」的提示作用。

## Verification

- 文档改动，无代码行为变化。核对项：
  - 四条 checklist 与 #507 原文 B 节逐条对应（未增删判据，只补样板路径与验收动作）；
  - 样板行号在 `flash_firmware/v1.3.14`（当前主线最新版本，不可变目录）上逐行核对通过；
  - `python scripts/run_gates.py check:quick` → `[OK] check:quick (7 gates)`
    （含 gov-surface 结构检查）。

未做：没有把新章节登记进 `docs/DOC-MAP.md` 的文档地图（见 Decision 的撞单理由）——
`script-versioning.md` 已在 AGENTS.md 按需入口表中，可达性不受影响。

## Revisit

- **A 节遗留判定**：#507 A 节点名的 `install_apk`（纯参数驱动）与
  `monkey_test` / `mtbf_setup` / `oobe_skip` 三脚本的「三要素核对」仍未闭环；
  本单只固化 B 节准入项，存量判定属 #507 本体或独立 Requirement；
- **checklist 未加门禁**：目前靠评审人自觉。若发现约束反复回归，可考虑把「新脚本
  版本必须声明路由决策字段」做成 `capabilities.json` 或脚本扫描门禁（需 ADR 级裁决，
  因涉及脚本契约扩展）；
- **DOC-MAP 登记**：待当前在窗的 DOC-MAP 修改结束，可把本节补进文档地图（非必需）。
