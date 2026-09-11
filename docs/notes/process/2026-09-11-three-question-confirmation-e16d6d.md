# 三问确认独立审查落地（会话 e16d6d）

Status: implemented
Class: process

## Decision

新增
`docs/reviews/REVIEW_COVERAGE_AND_FIX_EFFECTIVENESS_2026-09-11_e16d6d.md`，把本会话对
R01–R15 覆盖边界、已关闭 Issue 修复质量和 ADR-0034 多 Harness 修复模式的独立确认
结果落为审查输入。

本稿不复述同题他源（`_4e188e`、`_5e3831`、`_705379`、`_85d793`、`_f61411`），而是把
会话预算投向**证伪**：独立复核 #901、#1123 两个反例在当前基线是否仍成立，并以
`git diff HEAD..origin/main` 确认远端 72 个增量提交未触及相关文件。在此基础上新增
四项他源未记录的事实——§6 跨区收口在 GitHub 上无任何承载体；两个反例为同一根因
（分布式/异步不变量未下沉到可原子判定层）的二次复发；#1123 的 docstring 已把不安全
交错描述为安全；branch protection 的 `require_code_owner_reviews` 与
`require_last_push_approval` 均为 false。

同一会话追加 Q4（止血、治标不治本与组合治理），报告标题与 §0 结论表相应扩为四问。
Q4 的核心判定是：止血的真实形态不是被标注的过渡代码（生产代码中此类标记几乎为零），
而是**对同型缺陷的逐条 path-level 修复**。识别 3 个需组合治理的簇——A 成功语义不统一
（~26 个同型 Issue，需新 ADR）、B 进程内状态冒充跨进程权威（5 个，走 ADR-0027 修订）、
C 脚本冻结版本 × 静默吞咽 × 退役门禁崩溃（自我加强的腐化闭环，需裁决既有可行性研究）。
同时明确 capacity/deferred 族有触发判据，属正确取舍，不纳入治理包。

报告不修改 ADR、工作流、代码或 GitHub 状态，后续由多 Harness 综合审查裁决。

## Alternatives

- 重做全量抽样统计：放弃。他源已有 17 项分层抽样，重复抽样的边际信息低于对既有
  反例做独立证伪；且无稳定抽样框时新百分比同样不可用。
- 覆盖或合并进 `_synthesis`：放弃。总纲 §4.1 明确要求新会话使用新文件名、不以
  `_synthesis` 单文件覆盖他源。
- 直接重开 #901/#1123 或提交修复：放弃。本轮授权为只读确认；代码修复是独立
  Requirement，需按 execution-contract 领单并开独立 worktree。
- 新建 ADR：放弃。本轮是事实确认与流程评估，不含方向级架构裁决。结束条件分层与
  高风险 PR 独立复核若要落地，应作为 ADR-0034 修订单独提出。
- （Q4）为簇 B 新建 ADR：放弃。ADR-0027 正是做出「可横向扩展 / 无需 sticky」声明的
  文档，#1121 已证明该声明超出验证范围；新建会造成同一语义两份权威，应做修订。
- （Q4）为簇 C 新建 ADR：放弃。ADR-0020/0021/0029 已覆盖不可变与对齐语义，
  `SCRIPT_VERSION_BLOAT_ENDGAME_FEASIBILITY_2026-09-10.md` 已完成可行性研究，
  缺的是一次「做或不做」的裁决而非新概念。
- （Q4）把 capacity/deferred 族一并计入技术债治理：放弃。#974/#496/#105/#106/#83/
  #76/#77/#205/#169 均有明确触发判据，属主动取舍；混入会稀释注意力预算。
- （Q4）为簇 A 继续开独立 bug 单：放弃。#1027 关闭后 #812/#809/#816 为完全同型的
  新实例，修复速度与产生速度同阶，净复利为零。

## Verification

- 文件名使用当前 Cursor 会话 `0e8c8bb7-aac3-4092-b1fd-6dcd2fe16d6d`
  去连字符后末六位 `e16d6d`；
- `git rev-parse HEAD origin/main` → `f9a21b0f…` / `a536829e…`（本地落后 72 提交）；
- `git diff HEAD..origin/main --stat -- backend/api/routes/auth.py
  backend/services/token_blacklist.py backend/tasks/saq_tasks.py` → 空输出，
  两个反例结论对远端同样成立；
- 源码静态核验：`auth.py:435` 丢弃 `revoke()` 返回值；
  `token_blacklist.py:51-61` 已提供 `RETURNING` 原子判据；
  `saq_tasks.py:123-126` 在协程 `finally` 释放 guard；
- `gh api repos/DUElost/stability-test-platform/branches/main/protection`：
  strict=true、6 required checks、required_approving_review_count=0；
- `gh issue list --search "收口 in:title"`：无 §6 执行链/日志链收口承载体；
- `gh issue view 1246` 仍 OPEN；`gh pr view 1205` 已于 2026-09-10 合入。

Q4 补充核验：

- 全仓检索过渡/止血标记，`backend/**/*.py` 命中逐条判读，确认生产代码中无未兑现的
  过渡标注（命中多为 dpkg 过渡包、SUW 过渡态等无关语义）；
- AST 精确统计 `except` 体恰为 `pass`：478 处，按冻结脚本 371 / Agent 非脚本 68 /
  控制面 39 分桶；口径与 #739 的「431 处裸捕获或 pass」不同，**不可相减**，
  已在报告 §5.4 显式标注；
- `git ls-files 'backend/agent/scripts/*.py'`：32 类 / 116 版本目录 / 211 文件 /
  71,730 行，对比 #735 立项记录（100/180/59,406）为同口径可比增长 +20.7%；
  统计排除工作区未跟踪的 gpu_setup v1.0.8/v1.0.9 等新版本目录；
- `gh issue list --search "假成功 OR 仍返回成功 OR 报成功 in:title"` 命中约 26 项，
  报告中逐条列出 closed/open 归属；
- `ls docs/adr/`：ADR-0036 已存在，最高编号为 0036。

未执行：pytest、Vitest、全量 CI、迁移、Docker build、生产/真机操作、GitHub 写操作。
`scripts/run_gates.py check:quick` **pending**（本次仅新增两个 Markdown 文件，
尚未运行）。命令成功不等于验证通过，范围限制已在报告 §1.2 明示。

## Revisit

多 Harness 综合审查时逐项裁决为认可、反证或条件成立。若 #901、#1123 被后续代码
修复，须绑定新 SHA 与并发/取消交错回归证据更新综合结论，不原地改写本快照。
若总纲 §6 跨区收口在 2026-09-25 前仍无 Issue/Epic 承载体，应升级为流程缺陷而非
进度滞后。2026-10-15 #1035 经济性裁决后重新评估 ADR-0034 的操作成本与收益。

Q4 的可判定复议判据：

- 簇 C：以 `git ls-files 'backend/agent/scripts/*.py'` 的版本目录数与行数为基线。
  若 2026-10-15 前退役工具仍崩溃、或版本目录数继续净增长，则「逐条修复为负复利」
  的判定成立并应升格处置；若退役闭环恢复且零引用版本清零，则本判定作废。
- 簇 A：以「假成功」同型 Issue 的新增速率为指标。若设计裁决落地后 30 天内仍出现
  新的同型实例，说明契约未覆盖实际产生源，需重新界定范围。
- 簇 B：ADR-0027 修订落地后，以「`workers>1` 启动断言存在且覆盖清单完整」为
  兑现判据；生产改为多进程部署前该判据必须为真。
