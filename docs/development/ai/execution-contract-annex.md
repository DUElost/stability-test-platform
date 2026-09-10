# Execution Contract 附录（规范组成部分）

- **状态**：规范附录，**与正文同版本演进**（当前 v1.12）。正文为 [`execution-contract.md`](execution-contract.md)（语义唯一权威源）；本附录是其**规范组成部分**，承载实现级细则与历史留档。
- **优先级**：本附录与正文冲突时**以正文为准**；本附录只在不与正文冲突的前提下补充细则。方向裁决与理由见 [`ADR-0034`](../../adr/ADR-0034-multi-harness-execution-contract.md)。
- **分层依据**（#1238，2026-09-10 用户裁决）：正文承载**语义面**（术语与数据模型、真值表、transition table、scope 谓词、overlap/查重判定、字段封闭性）——所有 harness 每次执行都要读，受紧预算约束（S6）；本附录承载**实现级细则与历史留档**——按需查阅，不挤占语义面预算。两者混住同一文件时，任何语义增补都要先与细则抢字节（#1232/#1234 实测）。

---

## A.1 Registry 写入协议细则（正文 §2.2 的细则面）

**九步全序（硬约束）**：

```text
flock(registry.lock)
→ read registry.yaml
→ validate          ← schema 校验：必填字段/枚举值/scope 语法（正文 §5），非法即拒绝写入
→ modify
→ write same-dir registry.yaml.tmp
→ fsync(tmp)
→ rename(tmp, registry.yaml)
→ fsync(parent dir)   ← 文件 fsync 不保证 rename 后目录项的崩溃持久性
→ unlock
```

- **异常处置**：`validate` 失败 → 拒写并原样报错（不部分写入）；`fsync`/`rename` 失败 → 释放锁、删除残留 `.tmp`、报错退出（记录保持旧值）；
- **残留 tmp 清理**：任何命令启动时（持锁后）发现无主 `registry.yaml.tmp`（mtime 早于当前进程启动）即删除；
- **损坏恢复**：`registry.yaml` 解析失败时**不自动重建**——重命名为 `registry.yaml.corrupt-<timestamp>` 留证并报错，由人工决定重建（Registry 是声明面，丢了可重 declare，静默清空会伪造「无人工作」）；
- **仅本地 FS 成立，禁止落 NFS/CIFS**（flock 语义与原子 rename 不保证）。

## A.2 drift 强制随附物豁免清单（正文 §7 的清单面）

仓库纪律要求的强制随附物不参与 scope drift 比对（属流程义务而非执行意图）：

- `docs/notes/` 下的 Agent Note（含本仓库「非平凡变更必须附 Note」要求产生的文件）；
- `.github/workflows/*.yml` 中由 required checks 演进触发的配套改动（属门禁接线，须在 PR 描述中显式提及，豁免仅限 drift 提示、不豁免评审）。

其余一律按正文 §5 比对。

## A.3 P1 启动判据与过渡条款（已满足，历史留档）

**启动判据**（满足其一即启动，v1.1 修订；**三条已全部满足**——工具于 2026-09-07 预置就绪并采用）：

1. **已计划的多 Harness 工作批次启动前**（2026-09-07 用户裁决增补——ADR-0034 的立项背景本就是「即将开展多 Issue 集中修复与新需求开发的多 Harness AI Coding」，工具须**在批次开始前预置就绪**，而非等场景自然发生；原「等撞车」判据把因果倒置）；
2. 连续两周并行 worktree ≥3（数据源 = git worktree 历史/日志统计，与派生视图同源）；
3. 实际发生 ≥2 次跨 Harness 撞车返工。

**过渡条款**（已收口）：`ai_work.py` 就绪并被采用之前，维持 2026-09-04 约定的派生视图用法（`git worktree list` 遍历 + 对 merge-base 取差异）作为现行操作规范——防止「旧规范已废、新工具未启」的空窗。**工具已就绪并被采用**（`repository-workflow.md`：现行操作规范为契约正文协议），派生视图降为 ground truth 交叉验证手段（正文 §5.4）保留。

> 本节的**终态出口**已写明：判据与过渡条款是工具上线前的启动条件，上线后不再构成约束；保留在此仅作历史留档与「为何先建工具」的可追溯性，不参与现行判定。

## A.4 变更历史（v1.1–v1.8，自正文头部迁出）

正文头部只保留近期版本（v1.9+）；更早版本的明细在此留档：

- **v1.8**：§3.5 竞争提案可见性与决策实体唯一性——同一 Requirement 可有多个 Proposal Execution，但一个架构主题同一时刻只能有一个权威 Decision Artifact；决策类 Execution 必须显式 `--issue`、落笔前扫开放 PR 的同编号/同主题 ADR（用户 2026-09-09 裁决，来源 #906 的 ADR-0035 双份事故）；
- **v1.7**：§8 并发上限反转——移除「≈2-3 显式上限」，会话数不设上限，瓶颈校准为集成收尾侧、守对象重锚为在窗 Execution 规模与 reconcile 负载（用户 2026-09-08 裁决，ADR-0034 v1.9）；
- **v1.6**：§1.2 role 缺省归一化——declare 缺省写入 `implementation`（历史空串同义读取不迁移）+ 定义 Role 扩展再开启条件（v1.5 收敛 Revisit 两项闭环，ADR-0034 v1.8）；
- **v1.5**：§1.2 `role` 语义收敛——Role=保留元数据与未来扩展点、默认 `implementation`、Role Runtime 供给降级 deferred（用户 2026-09-08 裁决，ADR-0034 v1.7）；
- **v1.4**：§10 增「实现与契约的先后纪律」——实现不得静默重新定义 Contract 语义（用户 2026-09-08 确认）；
- **v1.3**：§2.1/§3.1/§3.3 增 T9 `resume`——FINISHED→CODING 返工回退（#946）；
- **v1.2**：§1.2 增 `issues` 持久字段、§2.1/§3.4 增 declare 在窗 issue 查重（#978）；
- **v1.1**：§9 启动判据增补「已计划的多 Harness 批次启动前预置就绪」（用户 2026-09-07 裁决）；§1.2 增 `branch` 持久字段；细则一次性自 ADR-0034 §2 迁出（ADR-0034 v1.1）。
