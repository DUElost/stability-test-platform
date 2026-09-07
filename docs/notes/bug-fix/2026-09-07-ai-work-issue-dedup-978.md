# ai_work declare 在窗 issue 查重（#978）

Status: implemented
Class: bug-fix

## Decision

修复「同一 issue 被不同 requirement 名双领，declare 时零告警」（#978）：T1 既有防线只拒
**同 requirement 名**在窗重复（`ai_work.py` cmd_declare），`fix-900-a` 与 `fix-900-b` 互不
感知；scope overlap 仅 hint 且 scope 不相交时连提示都没有。多 Harness 以审查台账
（#891/#910/#945，30 open）为需求池集中领单的场景下，双领要拖到 PR 评审/合入期才暴露。

三层修复：

1. **`--issue N`（可重复）持久字段**：写入 registry 记录（契约 §1.2 v1.2 增行）。受限
   YAML codec 的列表形态从 scope 硬编码泛化为 `LIST_FIELDS = (scope, issues)`；`issues`
   仅在有声明时落盘——legacy 记录（无该字段）的 dump 输出逐字节不变；
2. **declare 在窗查重**：issue 集 = 显式 `--issue` ∪ requirement/branch slug 启发式提取
   （`issue|fix` + 分隔符 + 1-6 位数字；日期串/无标记数字/标记粘连/≥7 位不误报）。与任何
   在窗记录（§3.2 risk 真值表）的 issue 集相交 → **exit 2 拒绝**并列出冲突记录；
   `--force` 供人工确认转手后显式覆盖（`[WARN]` 留痕）。查重是工作项去重、非文件上锁，
   与 §2.3 visibility-only 边界及同名录拒绝同一精神；契约新增 §3.4 成文；
3. **可见性**：`status` 的 `_report` 展示 `issues=`；declare 无任何 issue 输入时输出
   `[hint]` 建议 `--issue N`（slug 兜底失效时查重无输入的诚实提示）。

## Alternatives

- **查重命中仅 WARN 不拒绝**——放弃：与既有同名录拒绝（exit 2）不一致；双领的真实代价
  （双 PR 评审返工 + 后合者 rebase 冲突）值得一道默认硬闸，`--force` 已留逃生阀；
- **slug 提取放宽为任意数字**——放弃：`drift-sync-2026-09-07` 类日期 slug 会撞 2026/09/07
  假阳性；启发式宁可漏报（有 `--issue` 主通道兜底）不可误拦；
- **codec 对未知字段容错（读时忽略）**——放弃：违反 #880 确立的 fail-fast 设计（超集语法
  即损坏信号）。混合版本窗口的操作风险见 Revisit；
- **issue 查重放进 drift gate（advisory）**——放弃：advisory 在撞车预防上已被证明不够
  （overlap hint 存在数月仍有双领风险），declare 时点是唯一能在成本前置的时刻拦截的位置。

## Verification

- `python3 tools/dev/ai_work.py --self-test` 绿：新增 #978 红绿断言 19 项（提取启发式
  正/反例、查重纯函数含 MERGED 出窗与 skip 自身、codec issues 往返 + legacy 输出不变、
  validate 拒绝非数字、非 LIST_FIELDS 列表形态拒绝）；
- /tmp 沙盒 E2E 七场景：slug 兜底提取落盘 ✓ / 不同名同 issue 拒绝（exit 2）✓ / `--force`
  覆盖留痕 ✓ / 显式 `--issue` 列出全部在窗冲突 ✓ / 无 issue 输入 hint 放行 ✓ /
  MERGED 出窗不拦 ✓ / `issues` 字段 YAML 落盘正确 ✓；
- `check:quick` 7 门禁全绿（ruff/eslint/tsc/knip/compileall/gov-surface/ai-work）；
  compileall 的 `jira_issue_parser.py` SyntaxWarning 为存量非本单引入；
- 真实 registry dogfood：declare `--issue 978` 成功写入并回读（后因混合版本窗口主动移除
  自身记录，见 Revisit）。

## Revisit

- **混合版本窗口**：新代码写入的 `  issues:` 字段会让**旧版解析器** fail-fast 隔离
  registry（本次实测发生一次，已按 §2.2 恢复流程重建 legacy 兼容内容并双版本验证）。
  本 PR 合入后，任何仍持旧代码的 worktree 跑 `ai_work` 写/读均可能触发隔离——恢复路径
  同本次（剔除新字段重建）。若复发 ≥2 次再考虑 codec 前向容错或 registry 版本号字段；
- `update` 不支持改 `issues`（declare-only）：领单时漏带 `--issue` 且 slug 无号的单据
  无法补登记——出现真实需求再加；
- slug 提取启发式不认识 `issue946`（无分隔符）形态：现有 branch 命名 `docs/review-*`
  不受影响，`--issue` 显式通道不受限。
