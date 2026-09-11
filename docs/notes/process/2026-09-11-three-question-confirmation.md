# 三问确认稿（R01–R15 审查第三意见）落地

Status: implemented
Class: process

## Decision

- 新增确认稿
  [`REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-confirmation.md`](../../reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_b77c27-confirmation.md)：
  对「R01–R15 覆盖面 / 已闭环修复质量 / 审查→issue→PR→main 修复模式」三问的独立确认（第三意见，
  `CF-*` 编号），与已合入的 `CA-*` 稿（#1307）、`IV-*` 稿（#1313）同题平行，供综合轮汇聚。
- 定位为**确认 + 增补**而非第三份全量审计：CA/IV 已列缺口与量化不再复述；本稿独有增量 =
  ①横切系统性断言（redis 无 socket 超时 / `_locks` 无淘汰 / 零 jitter / 时钟混用 / 无客户端幂等）
  的代码级 grep 独立复核（全部实证成立）；②9-PR 逐单测试抽样（9/9 带专项测试、人工身份、当日关单）；
  ③backstop 红灯修复「pending 而非 verified」的状态声明；④open issue 吞吐边际更新（243→237）；
  ⑤三稿计数口径差异说明。不新增 `CF-D*` 待裁决项。
- **2026-09-11 追加轮（Q4，用户追加问「是否存在临时止血 / 是否需组合治理或新 ADR」）**：
  确认稿新增 §4（`CF-S01–S04`）——①#1028 修复实为四脚本各发新版本、各带 `_lib.py` 副本
  （版本域点修簇实锤，GB-06/07）；②ruff 无 BLE/E722/TRY/S110、ADR-0008 零 downgrade、
  ADR-0017 零死信（契约空洞实证）；③半套 ADR 链（ADR-0036 仍 Proposed 而实现单已先行、
  ADR-0035 Accepted 而实现侧全 open）；④组合治理代表 26 issue 全 OPEN。定位不变：
  三份盘点（CA-B01–B04 / IV-B01–B06 / 综合 §8 GB-01…15）为明细载体，本稿只做证据增补；
  标记 CA/IV 在「吞异常护栏是否新立 ADR」上的**载体分歧**（§4.3）留综合轮显式裁决，
  仍不新增 `CF-D*`。追加轮 resume 后在同一 PR 上提交（§1–§3 观测基线 `d00273d0`，
  §4 观测基线 `3207e56c`）。
- 在 [DOC-MAP](../../DOC-MAP.md) Living 审查层登记一行（紧跟 `IV-*` 稿行）。
- 不修改业务代码、ADR、总纲或在窗综合稿；不新建/关闭 issue；用户直接需求无关联 issue，
  declare 未带 `--issue`（查重靠 requirement ID，hint 已知悉）。

## Alternatives

- **只留在会话不落文档**：不采纳——用户明确要求落地，且综合轮需要可引用的稳定编号与证据面。
- **写第三份完整全量审计**：不采纳——与 `IV-*` 稿大面积主题重叠（覆盖缺口、backstop、三元组、
  FIFO 均已被两稿覆盖），重复会产生第三套并行计数与双轨跟踪成本；确认+增量定位信息密度更高。
- **直接编辑在窗综合稿（cursor，CODING）或其引用的 failure-mode 备忘**：不采纳——文件为并行
  Execution 持有（本地未跟踪、编辑中），按 §5.2 不得触碰他人在窗 scope。
- **把增补内容评论到 #1314（CA 附录 B，open）**：不采纳——该 PR 归属 codex Execution，
  外部会话不应向他人 PR 注入内容。
- **等 backstop 09-10 run 出结果再落文档**：不采纳——pending 状态本身即为有价值的诚实输入
  （综合轮须知道「修复已合入但未验证」），run 结论落地后由 Revisit 跟进。
- **把 Q4 核验写成第三份盘点文档或评论到他人 PR**：不采纳——CA 附录 B（#1314）与 IV §9（#1316）
  均已合入 main，明细载体已齐；本稿只做证据增补与分歧标记，避免第三套并行计数与双轨。

## Verification

- 通过：`venv/bin/python scripts/run_gates.py check:quick`（独立 worktree `/tmp/stp-tq-confirm`
  内运行，7 项门禁全绿）。
- 通过：报告与 Note 的相对链接指向 worktree 内实存路径；DOC-MAP 新增行链接有效。
- 通过：`git status`/`git diff --check` 审阅——变更仅涉及本报告、本 Note 与 DOC-MAP 一行；
  未包含凭据、无关格式化或本地 Harness 状态。
- 口径声明：报告内全部计数 / run 结论为 2026-09-10 UTC 19:15–19:50 观测值并附只读复现命令；
  未运行 pytest/Vitest/迁移/部署，未做目视验证，未触发 CI。文档变更不涉及行为语义，
  不进行运行时验证。

## Revisit

- **backstop 09-10/09-11 run 结论**：#1272/#1273 修复有效性的首个全量证据；转绿前综合轮引用
  CF-Q02 时必须保持「pending」口径；若连续第三晚 failure，IV-Q04 应升格 P0 证据。
- **在窗引用稳定性**：CF-C01 当前以「在窗综合稿 §1.4」软引用 cursor 产出与 failure-mode 备忘；
  两文档合入后应把引用改为稳定文件路径（可由综合轮在汇聚时一并完成，不必单独开单）。
- **DOC-MAP 并行增行**：cursor 综合稿仍在窗（CODING）；本分支追加轮已合并 origin/main
  （#1314 的 DOC-MAP 改动已吸收，无冲突），若综合稿后合入同一表格区，按行序解决即可。
- **综合轮显式裁决项**：CA-B03 vs IV-B05 在「吞异常护栏是否新立 ADR」上的载体分歧
  （确认稿 §4.3 标记）；CA-D08–D12、IV-D08/D09 与 GB-01…15 的汇聚归属。
- 观测数据随时间失效；后续轮次复用本稿数值前必须重跑附录命令。
