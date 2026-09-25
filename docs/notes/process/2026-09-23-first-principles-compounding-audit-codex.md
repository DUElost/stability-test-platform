# 第一性原理与长期复利审计落盘（Codex CLI）

Status: implemented
Class: process

## Decision

将 150 host / 3750 device 目标下的只读静态审计写入
[`STP_FIRST_PRINCIPLES_COMPOUNDING_AUDIT_2026-09-23_codex.md`](../../reviews/STP_FIRST_PRINCIPLES_COMPOUNDING_AUDIT_2026-09-23_codex.md)。
报告以 `main@8bc6bc1e` 为基线，区分代码事实、条件模型和待测运行态；独立于同日
`ae232a30` 基线的并行审计稿。用户给出的新容量上限作为目标，不把旧文档数字差异立为缺陷。

2026-09-25 同目标复审追加
[`STP_FIRST_PRINCIPLES_AUDIT_2026-09-25_codex.md`](../../reviews/STP_FIRST_PRINCIPLES_AUDIT_2026-09-25_codex.md)，
基线 `origin/main@1fafd052`。保留旧快照并增加后续链接，沿 #3230 的既有处置归属推进。
本次更新已完成代码项的状态，区分当前 CI、历史模拟数据与待验收运行态，并记录探针空过和包解压越界的隔离反例。
复用本 Note，不新建同主题流程决策。仅交付审计文档，不改变生产和状态机。

同日由另一会话/模型（Claude）独立复核，订正在复审稿 §9 **追加**（§1–§8 原文作为快照保留）：
A03 的生产生效（unit 无预算门禁与 StartLimit、告警副本未同步）与 A06 的保留期（生产
`PLAN_RUN_RETENTION_DAYS=36500`，owner 确认有意）均改为已核实事实；A01 归因补充、A02 定级、补入遗漏的 #3232。
保留期覆盖值同批入过渡台账 `plan-run-retention-disabled`，并在 `environment-variables.md` §1 写明新站点陷阱。

## Alternatives

- 只输出聊天摘要：不采纳；用户明确要求 Codex CLI 产出仓内 Markdown 文档，审计证据
  需要可复核的持久路径。
- 直接修改并行 Claude 稿：不采纳；其文件尚未跟踪，且基线、归属和部分结论不同。
- 将本轮建议直接写为 ADR 或实施变更：不采纳；连接预算、长期统计等方向仍需裁决和
  运行证据，审计报告不能替代决策。
- 2026-09-25 直接沿用旧报告缺陷清单：不采纳；ADR-0047 已 Accepted、源码回退已删除，
  当前 CI 又有新的失败事实。必须以当前代码重新校准结论。
- 复核订正直接改写 A01–A08 原文：不采纳；原文是带基线的快照，改写会抹掉「推论与现场不符」的证据，
  而这正是订正的价值（C2 即「默认值被当成生产值」）。只追加 §9 与文首指针。
- 保留期覆盖只写文档不入台账：不采纳；台账的到期执法才能让「无界增长」在 12-31 前被当面续期或收口。

## Verification

- 只读核对 `main@8bc6bc1e` 的数据库连接池、部署 PG 上限、产物队列、保留清理、
  脚本执行器与包注册、设备和结果读路径及相应 ADR。
- 与同日并行审计稿交叉核对，纠正 ADR-0051 Phase 3 状态和将条件模型写成现场吞吐的口径。
- 在独立 worktree 执行 `.venv/bin/python -m scripts.run_gates check:quick`：15 项门禁通过；
  其中 `schema-at-head` 因该 worktree 未配置 `DATABASE_URL` 而跳过数据库对齐检查。
  新增 Markdown 的本地链接逐一解析通过，`git diff --no-index --check` 无空白问题。
- 额外执行 `check:gov`：`gov-surface`、`gov-skills` 通过；`harness-ingest` 的首个
  Claude 探针超时，第二个探针运行时主动中止，因此 `check:gov` 未完成。
- 共享主检出此前同一门禁被并行未跟踪的 Claude 审计稿断链挡住；独立 worktree
  不包含该稿，本 PR 不修改它。
- 未运行容量测试、生产诊断、真机联调或恢复演练。

2026-09-25 复审：

- Agent 包缓存/strict 包解析/终态削峰/artifact 四文件 **58 passed**；连接预算与恢复脚本两文件 **19 passed**。
  使用清空环境和 cgroup 内存上限，不连生产库；恢复脚本测试不等于真实恢复演练。
- AST 执行容量探针原统计/断言代码：零样本、全失败、单端点失败均被其局部延迟断言放行。
- Python 3.13.5 临时目录实验：真实 `ensure_package` 接受正确 SHA 的目录软链组合包并越过解压根写入；
  全部路径位于临时目录，无现存文件或真实工具受影响。
- 读取当前基线 CI run 36058517011：后端 4 failed / 3675 passed，前端通过；本轮未本地重跑该全量 PG 套件。
- `python -m scripts.run_gates check:quick` → 16 gates 总流程通过，schema-at-head 因未配置数据库明确跳过；
  49 个本地链接解析通过，`git diff --check` 无空白问题。Tool Contract 门禁只覆盖 fixture 的范围已在报告说明。

2026-09-25 独立复核（§9）：

- 生产只读：`systemctl cat stability-backend`（无预算门禁 `ExecStartPre`、无 `StartLimit*`）；
  已加载规则文件 35 条、无 `StabilityTerminalBulkheadRejected`，与仓库 36 条逐字节不同；
  env 只 grep 保留期相关键（`PLAN_RUN_RETENTION_DAYS=36500`），未整读 env、未连生产库。
- CI run 36058517011 日志：`shed_503=602` 与舱壁用例的 `602.0 == 0.0` 对位；`pool_peak_async=0.0`。
  其修复与本地复现见 PR #3266 的 Note。
- 链接与锚点：复审稿全部相对链接与文首 §9 锚点按 GitHub slug 规则解析通过；
  `check_transitions.py` 与 `tests/test_transitions_registry.py`、`tests/test_env_inventory.py` 通过。

## Revisit

ADR-0047 裁决、产物丢弃对账、恢复演练、工具适配器试点或 150/3750 阶梯压测
取得新证据时，逐项修订报告；阶段性模型数据必须让位于实测分布。

2026-09-25 起，ADR-0047 的代码裁决已完成；复审关注运行生效与校准，另增加 CI 判据、
包解压边界和保留清理后长期统计的验证出口。阶段性建议不直接替代 ADR-0052 的裁决门槛。

§9 的 C1 在 #2959 手册 Step 1–2 执行后即过期，届时在 §9 追加一行生效证据，不改原表；
C2 随过渡台账 `plan-run-retention-disabled` 到期（2026-12-31）复核，或在长期事实层 ADR 立项时收口。
