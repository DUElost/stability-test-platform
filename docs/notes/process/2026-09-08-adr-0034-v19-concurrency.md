# ADR-0034 v1.8：并发上限反转——移除 ≈2-3 会话数上限

Status: implemented
Class: process

## Decision

移除 ADR-0034 §2.6 / 契约 §8 / repository-workflow.md 三处的「≈2-3 并发上限」与
「上限不放宽」表述（用户 2026-09-08 裁决），瓶颈原则校准为「在集成收尾侧（人的
审阅吞吐 + 外部平台可靠性），不在 agent 并行侧」，守的对象从会话数重锚为**在窗
Execution（risk 集合）规模与集成收尾负载**（合入后核销、reconcile、冲突返工）。
「任务排队」主策略与同文件串行排程不变；auto mode（08-26 synthesis）与人驱动多
会话并行为正交两轴，不随本裁决松动。

裁决依据（用户陈述 + 仓库实测交叉验证）：

1. **数字未实测继承**：≈2-3 自 2026-09-04 约定原文照搬，ADR-0034 v1.0「保留」时
   未重新测量；而 09-04 约定本身即为 N≥5 场景否决过协同协议——与本 ADR 立项目的
   （为多 Harness 并行建协同机制）自相矛盾；
2. **常态超出而无后果**：多 Harness 批次实际常态 5+ 会话并行（含单 Harness 多开），
   2026-09-07/08 批次实测 6-7 并发会话（A-D 四轨 + errata + 归因会话 + 本会话），
   吞吐未崩塌；被常态违反 2-3 倍、无机械强制的规范不是限制而是文档漂移；
3. **计数器错位**：会话数 ≠ worktree 数 ≠ 在窗 Execution 数（registry 只统计
   declare 过的 Execution，评审/scratch 会话按 #919 指引补登记；多会话共享主
   checkout 时 worktree list 系统性低估）——原上限守的计数器从未被正确测量；
4. **测量问题交还仪器**：Registry 上线后真实约束（在窗 Execution 规模 + 收尾
   负载）已可直接观测，静态数字被活体观测取代；数据恶化时按 ADR §6 新增触发器
   重议，是否引入收尾自动化（如 post-merge 自动 reconcile）由数据裁决。

同步落点：ADR-0034 v1.8（§2.6 重写、§4 Alternatives 行拆分、§6 增触发器、版本
记录）+ 契约 v1.6（§8 重写 + 版本头，同 PR 原子，遵循 §10 先后纪律）+
repository-workflow.md 并行 worktree 节 + adr/README.md 索引两行（L85 版本链 /
L97 里程碑行）。

## Alternatives

- **保留上限仅降格为更弱的 advisory**——放弃：它本来就是 advisory（「≈」「建议」、
  无机械强制），B1 四轨照跑没人被拦；降格不解决描述性失真，只是把漂移合法化；
- **换成新数字（如 ≤5）**——放弃：同样的测量问题换一个数（会话数计数器依旧错位、
  异构会话成本不同），且与「让数据决定复杂度」方法论相逆；活体观测（registry
  risk 集合）已可用，静态数字是劣化替代品；
- **等 B2 批次数据再改**——放弃：描述性失真已由既有事实确立（4 的证据不足以支撑
  2-3 的表述），等待只推迟文档同步；B2 作为新守对象（在窗 Execution 密度）的首个
  高压测试窗口的地位不变，但那是验证重锚后的观测指标，不是维持旧数字的理由。

## Verification

- `grep` 全库清扫：权威文档中「≈2-3 / 并发上限」仅存于本批四处落点 + 被取代的
  2026-09-04 note（历史记录不追溯修改）+ ADR-0026（OperationScheduler 设备并发，
  无关域）；AGENTS.md / DOC-MAP.md / harness-adapters.md / 门禁脚本零命中；
- `python3 -m tools.dev.ai_work --self-test` 绿；declare/rollback 数据见 registry
  `docs-adr0034-v18-concurrency`；
- `python scripts/run_gates.py check:quick` 结果见 PR 描述（本节 Verification 以
  实际运行为准）。

## Revisit

- **审计吞吐实测恶化**（集成冲突/返工率、合入后核销与 reconcile 负载、登记交互
  成本上升）→ 重议 §2.6 并发姿态与收尾自动化（ADR §6 v1.8 新增触发器）；
- B2 主线（#900→#903 串行 + #904/#887 同文件串行 + 独立轨）为重锚后守对象的
  首个高密度观测窗口，跑完复核一次实测曲线；
- auto mode 前提（治理面写者 >1 常态化）独立于本裁决，维持 08-26 synthesis 现状。
