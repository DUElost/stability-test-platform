# 并行操作规范切换：派生视图过渡条款收口（Registry-first）

Status: implemented
Class: process

## Decision

契约 §9 过渡条款的触发条件已双向满足——`tools/dev/ai_work.py`（P1 Registry
MVP）已合入 main 并被真实采用（fix-825/#895、fix-880/#899、fix-881/#912 三单
走完 declare→FINISHED→MERGED reconcile 全周期），且启动判据第 1 条「已计划的
多 Harness 批次启动前预置就绪」随 2026-09-08 B1 批次触发。据此把文档口径从
过渡态翻转为现行态：

- `docs/development/repository-workflow.md`「并行 worktree」节：主操作规范
  从「契约 §9 过渡条款（手写派生视图遍历）」改为「契约正文协议（开工
  `status` 前检 + `declare`、收尾 `finish`/reconcile）」；派生视图 bash 段
  保留但标注为 ground truth 交叉验证手段（effective_scope 并集中 derived 为
  Git 事实，契约 §5.1）；删除已失效的「Registry 尚未实现且启动判据未触发」
  与「判据未触发前不要手工维护任何登记状态」表述。
- `docs/development/ai/harness-adapters.md` L71-75：同族陈旧表述（「派生视图
  实践经契约 §9 过渡条款保留」）同步改为「元文件串行化继续有效、派生视图
  保留为交叉验证手段」。
- 两处 ADR 引用「Accepted v1.0」去掉版本钉扎只留 Accepted——S12 索引门禁
  只约束 ADR 头部/README 主表/DOC-MAP/M7 看板四面，此处不在检查面；去钉扎
  消除后续版本递增的重复漂移源（#867 同类教训）。

语义澄清（本次不改契约正文）：切换是**主操作规范易位**而非派生视图退役——
派生视图保留三个角色：effective_scope 并集内嵌（§5.1 恒成立）、ground truth
交叉验证（§9 尾句）、drift 检测输入（§5.1/§7）。

## Alternatives

- 同步修改 `execution-contract.md` §9 标注过渡条款已失效：放弃——§9 过渡
  条款本身是条件式表述（「就绪并被采用之前维持……」），条件失效后文本仍
  自洽；且该文件在另一 Execution（fix-946-lifecycle-t9-resume）在窗 scope
  内，避免撞文件。
- 同步翻转 AGENTS.md「开始任务时」第 3 条为 Registry 前检口径：放弃——该条
  已含指向 execution-contract.md 的指针且非事实错误，AGENTS.md 受 80 行/8KB
  预算与共享元文件串行约束，留待下次元文件 PR 评估。
- 为本次翻转新建 issue：不需要——切换是 §9 预定义的条件触发机制（非缺陷），
  用户直接批准执行，本 note 承担决策留痕。

## Verification

- `git diff` 核对两文件仅目标段落变更；
- `python scripts/run_gates.py check:quick`（含 S1–S12 治理面与 ai-work gate）
  全绿；
- Registry dogfood：本 Execution 全程经 `ai_work.py` declare/update 登记，
  scope 与实际 diff 一致（drift 零提示）。

## Revisit

- P2 heartbeat 落地使 `last_seen` 升格为可靠 liveness 信号时（契约 §4），
  本节「前检」描述需随之复核；
- AGENTS.md 是否同步 Registry-first 口径，待下次共享元文件串行 PR 一并评估。
