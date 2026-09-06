# ADR-0034 v0.3 多 Harness 执行契约只读审查

- 审查日期：2026-09-06
- 会话标识：`9261bd`
- 审查对象：
  - `docs/adr/ADR-0034-multi-harness-execution-contract.md`（Proposed v0.3）
  - `docs/notes/process/2026-09-06-adr-0034-draft.md`
  - `docs/adr/README.md`
  - Phase -1 Harness 基线、现行并行约定、Harness 适配与仓库集成工作流
- 审查方式：对照已冻结的 Contract v1 条款、Git worktree 实际行为、现有 FIFO
  auto-merge/CI 事实源和治理门禁进行只读交叉核验
- 审查基线：`main` at `55e9a71e`（包含 PR #860、#861）

---

## 一、结论

ADR-0034 v0.3 相比 v0.2 已完成关键方向修正：Registry 移入 Git common dir、固定
`registry.lock`、STALE 不再退出集成窗口、Registry/Git/GitHub/CI 权威分层、Scope
MVP、`test_impact`、单一 Execution Contract 以及复用现有 FIFO auto-merge 均已进入
正文。

但 v0.3 **仍不应转为 Accepted**。当前有 3 项与冻结版 Contract v1 直接冲突的阻断项，
另有 5 项应在同一轮修订中收口。建议发布 v0.3.1，完成本文 B1–B3 与 H1–H5 后再进行
Accepted 评审；在此之前不应启动 P0/P1 实施。

---

## 二、阻断项

### B1. Registry 发现协议没有使用已冻结的绝对 common-dir 形式

ADR §2.2 与 §5 使用：

```text
git rev-parse --git-common-dir
```

冻结版 Contract v1 要求的唯一发现方式是：

```text
git rev-parse --path-format=absolute --git-common-dir
```

本仓库实测：

| cwd | 当前 ADR 命令 | 冻结命令 |
|---|---|---|
| 仓库根 | `.git` | `/home/debian13/stability-test-platform/.git` |
| `backend/agent/` | `../../.git` | `/home/debian13/stability-test-platform/.git` |

当前写法仍依赖调用者 cwd；ADR §2.2 还允许实现选择 common dir 外路径，这与“common-dir
是唯一发现方式”冲突，也重新引入多 Registry 与 NFS/CIFS 落点风险。

**修订要求：**

- 固定使用 `git rev-parse --path-format=absolute --git-common-dir`；
- 明确这是唯一发现方式，不硬编码 `.git`，不提供 common-dir 外替代路径；
- 验收覆盖主 checkout、linked worktree 和深层 cwd，三者必须解析到同一目录。

### B2. 原子写入协议未完整固化

ADR §2.2 只写：

```text
flock registry.lock → same-dir temp + fsync + 原子 rename
```

冻结版 Contract v1 已明确该流程不能留给 P1 实现者自由解释，完整顺序应为：

```text
flock(registry.lock)
→ read registry.yaml
→ validate
→ modify
→ write same-dir registry.yaml.tmp
→ fsync(tmp)
→ rename(tmp, registry.yaml)
→ fsync(parent directory)
→ unlock
```

当前遗漏 `validate`、parent-directory `fsync` 和完整锁覆盖范围。文件 `fsync` 只能保证
文件内容，不能单独保证 rename 后目录项在崩溃后的持久性。

**修订要求：**在 ADR 固化完整硬约束，具体异常类型、残留 temp 清理和损坏恢复再由
`execution-contract.md` 定义。

### B3. 冻结的三维状态模型被压缩成两维，`finish` 无法被记录

冻结版模型：

| 维度 | 状态 |
|---|---|
| execution lifecycle | `ACTIVE / FINISHED / ABANDONED` |
| liveness | `LIVE / STALE` |
| integration | `NO_PR / PR_OPEN / READY / MERGED / CLOSED` |

v0.3 §2.3 只保留：

| 维度 | 状态 |
|---|---|
| liveness | `ACTIVE / STALE` |
| integration | `NO_PR / PR_OPEN / READY / MERGED / ABANDONED` |

这会造成具体不可实现问题：

1. `ACTIVE` 被用作 liveness，重新混入 execution lifecycle；
2. `ABANDONED` 被放进 integration，执行生命周期与 PR 生命周期再次耦合；
3. PR 可以在编码期间已经处于 `PR_OPEN`，此时执行 `finish(PR #N)` 后两个字段都无需
   变化，Registry 无法表达“执行者已停止编码”；
4. `finish` 被写成固定的 `NO_PR → PR_OPEN`，无法覆盖“先开 PR、继续编码、再 finish”
   的正常路径。

**修订要求：**恢复三个正交字段；风险集合由三维状态派生，不能以压缩状态替代：

- overlap/integration risk 继续覆盖尚未 `MERGED/CLOSED` 的声明；
- `STALE` 永远不构成排除条件；
- `finish` 只更新 lifecycle，不自行宣告 GitHub/CI 事实。

---

## 三、高优先级发现

### H1. Scope MVP 未明确拒绝 `..` 与 symlink escape

§2.8 只要求 repo-relative path 和 normalized storage；§5 只验收“非 repo-relative
path 拒绝”。这不足以约束：

- `backend/../outside` 等含 `..` 输入；
- 绝对路径；
- 仓库内 symlink 指向仓库外；
- file/directory 前缀边界与 trailing slash；
- 不存在但计划新增的路径应如何做 containment 校验。

应在 ADR 明列 `no absolute path / no .. / no symlink escape`，并要求 P0 Contract
定义基于路径组件边界的 overlap 谓词。

### H2. Competition mode 仍未进入 ADR

新机制正式取代 2026-09-04 “冲突靠避免”的关键理由之一，是允许在开发者明确选择时
进行受审计的 Competition。当前 ADR 只说 overlap 是 hint、Registry 不上锁，但没有
区分：

- 意外 overlap；
- 开发者批准的 Competition；
- Competition 的声明、结束和候选淘汰语义。

缺少这一点会让新 Registry 看起来仍只是自动化 WIP 公告，取代旧决策的理由不完整。
应至少在 ADR 定义 Competition 为“显式、非默认、仍不形成 ownership/locking”，具体
字段与 CLI 由 P0 Contract 规定。

### H3. `status` 刷新 `last_seen` 会让观察行为改变被观察状态

§2.5 写明 `status/update` 顺带刷新 `last_seen`。`status` 是读取全局 Registry 的观察
命令；若它刷新记录，则任何巡检都可能把失联 Execution 重新显示为活跃，破坏 stale
判断。

应保持：

- `status` 严格只读；
- 只有带明确 execution identity 的 `update/heartbeat` 可以刷新自身 `last_seen`；
- 读取其他 Execution 不得改变其 liveness。

### H4. STALE 的“持久字段”与“派生提示”互相矛盾

§2.3 称每条记录包含 `liveness ∈ {ACTIVE, STALE}`，且超时“标 STALE”；§2.5 又规定
P1 超时“不自动改写任何字段”，只在输出中提示可能陈旧。实现者无法判断 P1 的 STALE
究竟是：

- 持久化枚举值；
- 由 `now - last_seen` 派生的显示状态；
- 只能人工写入的字段。

建议拆为：

- 持久化 `last_seen`；
- P1 `effective_liveness` 仅派生显示，不回写；
- P2 heartbeat 就位后仍由查询时派生，或明确唯一回写者；
- 不论采用哪种形式，都不得影响 integration risk。

### H5. 单一权威源与“五处 canonical”表述冲突

§2.10 正确声明 `execution-contract.md` 是唯一权威源，但 §2.7 P0 又写
“AGENTS.md/CLAUDE.md/.cursor/.codex/docs 五处 canonical”。这既像五份权威副本，
又可能被解析为不存在的 `.codex/docs` 路径。

应改为“一个 canonical Contract + 明确列举的薄入口”，并在 P0 验收中列出准确文件，
禁止以目录名或“五处 canonical”概括。

---

## 四、中等与文档一致性问题

### M1. `READY` 的定义与现有 FIFO 行为不够精确

§2.3 把 READY 定义为“required checks 全绿、进入 FIFO 集成位”。现有实现中，绿灯 PR
可以处于队列非队首，只有队首会启用 auto-merge。需明确 READY 表示：

- checks 通过且 eligible；还是
- 已成为队首；还是
- 已启用 auto-merge。

READY 由 CI/队列事实派生，不能由 Registry 自声明；若主干推进导致 checks 重新运行，
还需定义 `READY → PR_OPEN` 的回退。

### M2. Integration transition 与 reconciliation 入口不完整

当前 CLI 只有 `declare/status/update/finish`，但没有明确：

- `PR_OPEN → READY` 的事实采集者；
- `READY → MERGED` 的 GitHub reconciliation；
- PR closed without merge 如何映射；
- 谁可以写 `ABANDONED/CLOSED`；
- GitHub API 不可用时是否保持旧状态并标记观测时间。

这些可以下沉到 P0 `execution-contract.md`，但 ADR 应将 transition table、reconcile
来源和缓存 provenance 列为 P0 必备目录，防止 P1 先于语义落地。

### M3. G2 尚未解决深层 Claude 对根启动契约的摄取

§3 的 scoped `AGENTS.md` 真身 + `CLAUDE.md` symlink 可以让 Claude 读取 scoped 内容，
但 #857 已证明深层 cwd 下根 `CLAUDE.md` 的 `@AGENTS.md` 不展开。根层形态本次又明确
不动，因此 scoped symlink 本身不能保证 Claude 同时获得根 AGENTS 的总原则与硬不变量。

附录 A 的 Q1 正是根标题阳性对照，当前 Claude 仍会失败。P2 必须明确根契约供给方案，
并将“根 bootstrap + scoped 内容”同时可见作为验收，而不能只写“真身内容全部可见”。

### M4. ADR 索引和起草记录仍有漂移

- `docs/adr/README.md` 的 M7 看板已更新为 v0.3，但“当前 ADR 清单”仍止于 ADR-0033；
- `docs/DOC-MAP.md` 尚无 ADR-0034；
- 起草 Note 第 8 行仍称 Proposed v0.1；
- 起草 Note 第 10 行仍保留 v0.1/v0.2 的“worktree 外 + gitignore”摘要，虽然后文新增
  v0.3 修订记录，但当前状态不够直接；
- `docs/design/2026-08-governance-surface-protection.md` 汇总表仍写 S1–S10，实际已经
  是 S1–S11。

这些不阻断方向裁决，但应在 v0.3.1/P0 文档 PR 一并校正。

### M5. #855 的“合入触发”仍有语义歧义

ADR 已合入 Git，但仍为 Proposed，P0/P1 尚未发生。§5 把“本文合入”写成 #855 主触发，
而相关 Issue 评论又以 Accepted 后启动为实际节奏。建议明确区分：

- Git merge：草案可被引用；
- ADR Accepted：方向生效；
- P0 Contract 完成：行为验证方案可开始实施。

---

## 五、已闭环项

| v0.2 审计问题 | v0.3 结果 |
|---|---|
| Registry 位于主 checkout，跨 worktree 可能分裂 | 已改为 Git common dir，方向正确 |
| 锁数据文件而非固定锁 | 已固定 sibling `registry.lock` |
| 仅 ACTIVE 参与 overlap | 已改为 integration window，liveness 不参与 |
| STALE 自动退出风险集合 | 已明确永不退出 |
| P1 无可靠 heartbeat | 已分期，P1 advisory、P2 wrapper heartbeat |
| Registry 复制 GitHub/CI 事实 | 已建立 Registry/Git/GitHub/CI 权威表 |
| `finish` 可自行宣告 MERGED | 已禁止，MERGED 必须核对 GitHub |
| Scope 支持面过宽 | 已限制 file/directory，拒绝 glob/ownership/locking |
| 缺少 Test Impact | 已加入 `test_impact` 与 coverage-mismatch advisory |
| 缺少单一完整契约源 | 已把 `execution-contract.md` 定为 P0 唯一权威源 |
| 重建 Merge Queue | 已明确复用现有 FIFO auto-merge |
| Worktree/Role 归属混淆 | Worktree 属于 Execution，Role 不构成 ownership |

---

## 六、验证证据

本次只读审查实际执行：

```text
venv/bin/python tools/dev/check_governance_surface.py --check
venv/bin/python tools/dev/check_governance_surface.py --self-test
venv/bin/python scripts/run_gates.py check:quick
git diff --check
git status --short --branch
git rev-parse --git-common-dir
git -C backend/agent rev-parse --git-common-dir
git rev-parse --path-format=absolute --git-common-dir
git -C backend/agent rev-parse --path-format=absolute --git-common-dir
```

结果：

- governance check：S1–S11、S5x 全绿；
- governance self-test：12 条规则红/绿样例全绿；
- `check:quick`：ruff、eslint、tsc、knip、compileall、gov-surface 全绿；
- PR #860 与 #861 的 required checks 全部通过；
- 审查开始前工作树仅包含其他会话生成的未跟踪 review 文件，本报告未修改它们。

上述结果证明当前仓库结构和静态质量门禁健康，但不证明 ADR 的状态语义已经闭合。

---

## 七、建议的 v0.3.1 最小修订集

1. Registry root 固定为 absolute git-common-dir，删除替代落点；
2. 补全原子写入九步协议；
3. 恢复 lifecycle × liveness × integration 三维模型；
4. 明列 Scope 的 absolute/`..`/symlink escape 拒绝规则；
5. 定义 Competition mode 的最小语义；
6. 让 `status` 严格只读，澄清 STALE 派生/持久化模型；
7. 明确 READY、MERGED、CLOSED/ABANDONED 的权威 transition 与 reconciliation；
8. 将“五处 canonical”改为“单一 Contract + 精确薄入口清单”；
9. 修正 G2 根契约摄取验收与索引/版本漂移。

完成上述修订后，可再次进行短周期只读复审；复审通过后再将 ADR-0034 改为 Accepted，
随后执行 P0 Contract 与入口接线，最后进入 P1 Registry MVP。

