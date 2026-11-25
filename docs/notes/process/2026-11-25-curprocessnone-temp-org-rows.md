# `Result_MergeFiles_org.xls` 中的 `CurProcessNone` / `*_temp` 行

Status: implemented
Class: process

检索词：`CurProcessNone`、`ExpClassNone`、`ExpTypeNone`、`PackageNone`、
`*_temp`、`Result_MergeFiles_org.xls`、`db.<随机>`、解析失败占位。

## Decision

`dedup/<run>/merge/mtk/Result_MergeFiles_org.xls`（以及各 host 的 `*_org.xls`）里出现的
`CurProcessNone` **不是**进程名，而是 `start-log-scan`（`BaseScan`）在
`recognizeexception` 解不出 `cur_process` 时写入的占位串；同批常见还有
`ExpClassNone` / `ExpTypeNone` / `PackageNone`。这些行进入去重前全量清单，但工具在进
最终问题表时会跳过（membership 常见 `filter_reason=not_in_pkglist`，因占位串不在
`pkglist`）。因此 `Result_MergeFiles.xls` / `statistics.txt` 的问题计数通常为 0 条
`CurProcessNone`。

现场形态（PlanRun **1594**，合并产物约 2026-11-25）：

| 观察 | 数值 / 事实 |
|---|---|
| org 总行 | 1078 |
| `CurProcessNone` | 101（且全部同时为 `ExpClassNone` / `ExpTypeNone` / `PackageNone`） |
| Path 形态 | 全部落在事件目录名 `*_temp` |
| 最终表 | `Result_MergeFiles.xls` 115 行，`CurProcessNone=0` |
| 中心映射 | mapping 常为 `mapped=false`；`devices/<run>/` 下通常无对应 `*_temp` 目录 |

单行溯源样本（合并 org 第 3 行，`DeviceId=AHFFNX6930000182`，事件
`1795524594934_temp`）：

1. 来源 host：`.85` 当日 `*_org.xls` 同行；mapping path 指向
   `.stp-scan/.../aee_exp/1795524594934_temp/db.0VHyZO/__exp_main.txt`
   （`db.<随机>` 而非正常的 `db.00.<TYPE>.dbg.DEC`）。
2. 同串号同 org 姐妹目录：约 16s 后的 `…_db.00.ANR`，以及
   `2026_1122_023401_271_db.00.ANR`，二者 `ExpTime` / Detail 一致，均为
   `com.google.android.calculator` 的 ANR（`2026-11-22 02:34:01`）。
3. 更早一轮 membership：该 `_temp` 行 `branch=filtered`；两个完整 ANR 同属
   similarity 组，代表为 calculator ANR。

结论：`CurProcessNone` 行是 **未完成 / 残缺的 `*_temp` AEE 目录被扫进 org** 的副作用；
真实异常通常可在同串号的完整 `*_db.00.<TYPE>`（或日期命名）姐妹事件上读到。交付与
Jira 计数以去重后的 `Result_MergeFiles.xls` 为准，不得把 org 里的占位行当独立问题。

速查步骤（只读）：

1. 在 `Result_MergeFiles_org.xls` 取一行 `CurProcessNone`，记下 `Path` 末段事件目录名与
   `DeviceId`。
2. 在 `dedup/<run>/mtk/*_org.xls.mapping.json` 按事件目录名搜 `path`，确认 host 与
   `.stp-scan` 源路径、`mapped`。
3. 打开对应 host 的 `*_org.xls`，列出同 `DeviceId` 下相邻事件；优先对照
   `*_db.00.*` / 日期命名完整目录的 `ExpClass` / `CurProcess` / `ExpTime`。
4. 若有 `*_members.json`，看该 org 行的 `branch` / `filter_reason` / `signature`。
5. 核对 `Result_MergeFiles.xls` 是否已无该占位行。

影响面：本 note 与 skill / 运维参考的交叉链接；不改生产代码、契约、环境变量或 CI。

涉及文件：

- `docs/notes/process/2026-11-25-curprocessnone-temp-org-rows.md`（本文）
- `.claude/skills/monkey-aee-dedup-handoff/SKILL.md`（踩坑入口）
- `docs/operations/2026-10-30-monkey-aee-jira-handoff-reference.md`（参考资料入口）

## Alternatives

- 只在聊天里解释、不落盘：同题会反复出现，检索成本高。放弃。
- 改扫描工具不再把解析失败行写入 org：属工具行为变更，需单独需求与回归；本轮只记录
  现行语义。
- 在平台 merge 阶段物理删除 org 中的占位行：改变 `_org.xls` 全量可复核性；现行
  「org 全量 + final 过滤」已足够，不在本轮改。

## Verification

- 对 PlanRun 1594 的 `Result_MergeFiles_org.xls` / `Result_MergeFiles.xls` /
  `statistics.txt` 与 `.85` 的 `*_org.xls` + `*.mapping.json` + 轮次
  `*_members.json` 只读对拍，确认上表数字与单行溯源链。
- 检索：在仓库内 `grep CurProcessNone` / `*_temp` 应能落到本文与 skill 入口。

## Revisit

- 若 `start-log-scan` 改为对 `*_temp` 直接跳过或不写占位串，更新本文「现行语义」并链到
  工具版本说明。
- 若平台对 org→中心的 `*_temp` 映射策略变化（例如开始上送残缺目录），重核
  `mapped=false` 与中心路径段落。
- 再发现同形态但**没有**同串号完整姐妹事件时，补一条反例，避免把「必有姐妹 ANR/JE」
  写成绝对规律。
