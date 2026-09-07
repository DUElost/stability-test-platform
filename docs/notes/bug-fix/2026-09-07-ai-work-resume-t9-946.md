# ai_work resume：lifecycle 补 T9 返工回退（#946）

Status: implemented
Class: bug-fix

## Decision

修复「`finish` 后返工无干净出口」（#946）：lifecycle 原是单向阀门（`CODING` 仅 T1 declare
写入），按契约语义正常操作（开 PR 即 `finish`）的 Execution 被评审意见打回时，三条出路全坏——
带 FINISHED 谎报继续干、同 id 重 declare 被拒、换 id 制造自指 overlap。采纳 issue 倾向的
**方案 1：补 T9 `FINISHED→CODING`**，与 T8（PR reopen 由 GitHub 事实回退）对称：

1. **`resume --id` 子命令**：守卫 = `can_resume(lifecycle, integration)` 纯函数——仅
   `FINISHED` 且 integration ≠ `MERGED` 放行；`CODING`（无需）、`ABANDONED`（恢复=重新
   declare，ABANDONED 仅显式人工动作的语义不破）、`MERGED`（风险已真实关闭，走 T1）三类
   拒绝并附理由。`integration` 不变（T9 表行语义），随下一轮 `update` 向 GitHub reconcile；
2. **契约升 Living v1.3**：§3.3 transition table 增 T9 行、§3.1 FINISHED 标注非终态、
   §2.1 工具清单同步。保持「审计连续性」：返工前后是同一条记录，不再制造 abandon+重
   declare 的窗口空档与 overlap 假阳性；
3. **本批次即用**：B1-A 轨道自身就是首个触发场景（issue 预判「当前批次的下一个 Execution
   大概率命中」命中）。

## Alternatives

- **方案 2（恢复 = 新 Execution：abandon 旧记录 + 重 declare）**——放弃（issue 同判）：
  丢审计连续性；T4 对开放 PR 的 abandon 会警告留窗，换新 id 则新旧两条在窗记录 scope
  重合产生自指 overlap；
- **方案 3（finish 语义改为 PR 终态后统一收口）**——放弃：与 T2 现有定义冲突，改动面
  最大，且削弱「finish=停止编码」这个最自然的登记时点；
- **`update --resume` 形态**——放弃：update 是 T5「lifecycle 不变」的心跳/reconcile 通道，
  混入状态迁移会让 transition table 的触发列失真；独立子命令与 T1-T9 的「触发=命令」
  结构一致；
- **允许 ABANDONED/MERGED 也能 resume**——放弃：前者破坏「ABANDONED 仅显式人工动作」
  的僵尸出口唯一性；后者窗口已真实关闭，恢复记录只会伪造在窗风险。

## Verification

- `python3 tools/dev/ai_work.py --self-test` 绿：新增 `can_resume` 真值表断言（四 integration
  放行/三类拒绝附理由）；
- /tmp 沙盒 E2E：issue 原样最小复现（declare→update --pr→finish→返工）经 `resume` 回到
  CODING ✓；重复 resume / MERGED / ABANDONED 三向拒绝且理由正确 ✓；
- `check:quick` 7 门禁全绿；
- **registry 格式零变更**（仅 lifecycle 字段值域内迁移）——无 #978 那样的混合版本隔离
  风险；旧代码读 resume 后的记录行为不变（CODING 本就是合法值）。

## Revisit

- P2 Adapter（heartbeat wrapper）就绪后，评审反馈驱动的 resume 可由 wrapper 建议（仅
  提示，不自动——状态迁移保持执行侧自声明）；
- 若「FINISHED 期间 drift/coverage advisory 停摆」成为实际盲区（FINISHED 记录仍 in-risk，
  advisory 不停，此项预计不触发），再评估 resume 是否需要顺带刷新 derived；
- 同文件前一单 #978 的混合版本隔离风险仍按其 Agent Note Revisit 观察。
