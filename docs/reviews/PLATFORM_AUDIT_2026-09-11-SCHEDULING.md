# 2026-09-11 审查批次排期台账

- **状态**：Living（**终态收敛** 2026-09-13）
- **日期**：2026-09-12 起，2026-09-13 收敛
- **来源批次**：📌 [总表 #1515](https://github.com/DUElost/stability-test-platform/issues/1515) · label `audit-2026-09-11`
- **审查报告**：[`reviews/PLATFORM_AUDIT_2026-09-11.md`](./PLATFORM_AUDIT_2026-09-11.md)（已合入 main）
- **性质**：对批次 13 条发现的**实际处置进度盘点 + 剩余项排期**。所有状态经 `gh api` 实查，非计划推测。

---

## 一、TL;DR

**批次 13 条发现已全部采纳并收口：12 条已关闭，1 条（#1520）转为持续工程台账。当前无在途 PR。**

| 处置阶段 | 数量 | 编号 |
|---------|:---:|------|
| ✅ 已关闭（completed） | **12** | C-01(#1516)、C-02(#1517)、H-01(#1518)、H-02(#1519)、H-04(#1521)、H-05(#1522)、H-07(#1523)、H-07b(#1524)、H-08(#1525)、H-09(#1526)、H-06(#1527) |
| 🏗️ 持续工程台账（open，分期交付） | **1** | H-03（#1520） |
| 📌 总表（长期 open） | 1 | #1515 |

> 注：总数按 issue 单计：13 单中 H-07 与 H-07b 为同一主题拆分，H-06/H-09 各有独立单。

> **续查补充（2026-09-14）**：13 单中 **#1526（H-09）的验收项并未真正闭环** —— 其
> 「`action_template` 表级删除」从未落地，却被同期的 #734/#1754 以 `downgrade()` 里的
> `op.drop_table` 判为「已完成」。已另立 **[#1890](https://github.com/DUElost/stability-test-platform/issues/1890)**。
> 原「12/13 已关闭」的**关单数**不变，但**完成度**须按此修正。

**排期结论**：本批次**已无排期议程**。原「轨道 A 推进在途 PR」4 个 PR（#1589 / #1593 / #1596 / #1616）已于 2026-09-12 全部合入；原「轨道 B 需裁定」的 #1525 已由 owner 裁决落地（PR #1619）；原「轨道 C 暂缓」的 #1520 已启动分期交付（首切片 PR #1663）。

---

## 二、逐条处置状态（实查 · 终态）

### ✅ 已关闭 12 条

| 编号 | 主题 | 关闭时间 | 闭环方式 |
|:----:|------|---------|---------|
| **#1516** | C-01 同步引擎池未配置 | 09-12 09:55 | `completed`（无评论，直接修复） |
| **#1517** | C-02 RunConsole 单例 | 09-12 10:10 | **#1436 / #1114** — ADR-0027 v1.2 登记约束 + `console_run_miss_hint()` 可诊断化；生产拓扑确认为单副本 → **止血交付**；**治本另立 #1737**（owner 路由 / 注册表外置，OPEN） |
| **#1518** | H-01 心跳超时常量多源 | 09-12 13:08 | **PR #1593 已合入**（统一 `HOST_HEARTBEAT_TIMEOUT_SECONDS` 单一来源） |
| **#1519** | H-02 services 反向依赖 | 09-12 16:43 | **PR #1616 已合入**（下沉闭环 + 分层门禁，14 文件 +378/-156） |
| **#1521** | H-04 NFS 原始日志零 TTL | 09-12 13:16 | **PR #1596 已合入**（retention 增加 NFS 轨回收，含删除边界防护） |
| **#1522** | H-05 HddSpill 腾退封顶 | 09-12 09:33 | `completed` |
| **#1523** | H-07 ADR-0031 编号冲突 | 09-12 13:00 | **PR #1589 已合入**（附录改子编号 ADR-0031-A + README 补登） |
| **#1524** | H-07b ADR 状态行格式 | 09-12 10:14 | `completed` |
| **#1525** | H-08 PR 门禁不覆盖重测试 | 09-12 16:55 | **PR #1619 已合入** — owner 裁决「维持现状 + **前移触发规则制度化**」（同类夜间红灯 ≥2 次 → 评估前移）；驳回 Merge Queue（≈10× 全量 CI/天）与全量前移，正式文档见 `repository-workflow.md`「CI 分层」节 + Agent Note |
| **#1526** | H-09 `action_templates` 死代码 | 09-12 10:09 | **PR #1568 已合入**（16 文件，-552 行）；**`action_template` 表级删除未落地 → 另立 [#1890](https://github.com/DUElost/stability-test-platform/issues/1890)**（见 §四·3） |
| **#1527** | H-06 `merge_task` 静默 return | 09-12 09:14 | `completed` |

### 🏗️ 持续工程台账 1 条（open，非「待办」）

| 编号 | 主题 | 当前进展 | 后续 |
|:----:|------|---------|------|
| **#1520** | H-03 God-module 路由 | 首切片已交付：**PR #1663**（manual retry/exit → `services/plan_run_manual.py`，路由薄壳化 + 服务层直测） | 按「垂直切片、分期 PR」原则保留 open 作为**持续工程台账**；下一批候选：`agent_api` complete/recovery 段、`projects.py` 归属/映射线。**#1519 分层门禁已生效**，保证拆分不回流 |

> #1520 的「暂缓至 #1616 落地后重评」条件**已满足**：分层门禁随 PR #1616 合入，owner 已按原建议启动分期交付。

### 🔗 衍生跟踪

| 编号 | 来源 | 说明 |
|:----:|------|------|
| **#1737** | #1517（C-02）| 治本项：RunConsole owner 路由 / 注册表外置。止血已交付，治本独立跟踪（OPEN） |

---

## 三、排期建议（终态 · 原三轨道已清空）

原三轨道均已完成，保留记录以说明收敛路径：

### 轨道 A｜推进既有 → ✅ 已完成

4 个在途 PR 全部合入：**#1589**（H-07，文档）→ **#1593**（H-01，常量统一）→ **#1596**（H-04，NFS 回收，含删除边界复核）→ **#1616**（H-02，分层下沉，最大规模）。

### 轨道 B｜需裁定 → ✅ 已裁决

**#1525（H-08）** 原判「不是开发任务是决策任务」，owner 裁决结果为**选项 A 的强化版**：维持 PR 侧不跑全量，但把「何时该前移」写成**制度化触发规则**（而非个案判断），并留下量化依据（近 30 次 backstop 9 红夜 ≈30%、`backend-test` 类占 5/6）。

### 轨道 C｜暂缓 → ✅ 已启动

**#1520（H-03）** 达到重启条件（#1616 门禁生效），已按垂直切片交付首个 PR #1663，转为持续台账。

---

## 四、本批次暴露的邻域问题（超出原 13 单）

盘点过程中发现两条**不属于原批次、但值得记录**的问题：

1. **`update-branch` 撞 workflow scope 即整 job 红** → **已立单 [#1783](https://github.com/DUElost/stability-test-platform/issues/1783)（P2）→ 已修复闭环**
   - **现象**：`update_branch_tolerant()` 在队首 PR 改动了 `.github/workflows/*` 时，`gh pr update-branch` 被 GitHub 拒绝，落到 `return "$rc"` → 整 job 红：
     `GraphQL: refusing to allow a Personal Access Token to create or update workflow ... without 'workflow' scope (updatePullRequestBranch)`
   - **缺口**：该函数已对 head-sha 竞态 / 真冲突 / PR 已非 open 态三类失败绿退，**唯独漏了 scope 拒绝**（脚本 19-47 行）。
   - **影响**：队列 rebase 全线停摆（一度积压 28 个 PR 呈 `behind`）；且**每个 PR 都带此红叉**，信号失去信息量。
   - **实查**：与 PR 内容无关；队首 #1578 合入后队列恢复 → **条件触发**，非持续故障（近 40 次 run 零 failure），故标 P2 而非 P1。
   - **落地**：采用路径 A（容错分支补齐，止血、零安全影响）→ **PR #1793 已合入**（`scripts/ci/pr-automerge-queue.sh`
     + `tests/test_automerge_queue_alerts.py`）；`#1783` 已于 2026-09-13 09:51 关闭。
     （同题 PR #1803 为重复提交，已关未合。）

2. **`ci/queue-blocked` 告警单高频生成** → **已有单，不重复开 → 已三级闭环**
   - 根因单 **[#1761](https://github.com/DUElost/stability-test-platform/issues/1761)**（告警把「检查进行中」误判为 missing，83 分钟 20 条误报）；
     修复 PR **[#1764](https://github.com/DUElost/stability-test-platform/pull/1764)**（三分类 + 6 例回归测试）。
   - 实查发现时本项**已被认领**，故只登记不另立单。
   - **追加（2026-09-13 晚间实查）**：误报实际有**三类**，逐类闭环：

     | 类别 | 根因 | 修复 |
     |------|------|------|
     | 一类 | `IN_PROGRESS` 被判 missing（只读 `.conclusion`，进行中为空串） | PR #1764 |
     | 二类 | `COMPLETED/NEUTRAL` 被判 failed（CodeQL 聚合 check 子分析未完成时父项为 NEUTRAL） | PR #1796 |
     | 三类 | `MISSING` 的启动窗口误报（CodeQL 作为独立 workflow 晚注册 20–33s） | **PR #1869** |

     根因单 **[#1792](https://github.com/DUElost/stability-test-platform/issues/1792)** 记录了全过程；
     修复确认以「连续观测 20 次（每 90s，约 30 分钟）零新告警」为证（合入前约 4 分钟一条）。
     `#1792` / `#1761` 均已关闭。

3. **`action_template` 表级删除从未落地，却被判为「已完成」** → **已立单 [#1890](https://github.com/DUElost/stability-test-platform/issues/1890)**（P2）
   - **现象**：`#734`（已关）验收项明写「`[ ] 编写 Alembic 迁移 drop 掉无用的 action_template 表`」；
     PR #1754 以「已先行完成——迁移链含 `op.drop_table("action_template")`」复核模式跳过实施并 `Closes #734`。
     同一判据亦出现在 `#1526` 的 Agent Note 中。
   - **实查**：该 `op.drop_table` 位于创建迁移 `f4a5b6c7d8e9` 的 **`downgrade()`**（仅回滚路径）。
     全链 136 个 revision 扫描，**forward 链 0 处删除该表**；最近的 `g7h8i9j0k1l2_align_schema_baseline.py`
     反而**重建**其索引（`CREATE INDEX ... ON action_template`）——若表已 drop，该句在空库 upgrade 时会报错。
   - **影响**：生产库 `action_template` 仍在（仅 ORM 模型被删）；`#1526` 的 `Revisit` 写「需生产只读核对后**另单**」，
     该单**从未建立**（全库 600 单查重确认）。
   - **并行的防线缺口**：`#734` 评论要求修复后**必须**新建 2 项 CI 门禁
     （`tools/dev/check_deprecated_endpoints_usage.py` + 孤立 ORM 模型挂载门禁），实查**均不存在**，关闭时未记录处置。
   - **修复方向**：A 生产只读核对后补 drop 迁移 + 收敛 schema-sync 白名单；B 落地或裁定 descope 该 2 项门禁；
     C 在 `docs/notes/README.md` 复核指引补一条判据——*验证「迁移已删除 X」须确认语句位于 `upgrade()` 而非 `downgrade()`*。

---

## 五、验收清单（终态）

- [x] #1520（H-03）认领 / 转为持续台账 —— **已交付首切片 PR #1663**
- [x] #1525（H-08）owner 决策记录 —— **已裁决并落地 PR #1619 + Agent Note**
- [x] #1589 / #1593 / #1596 / #1616 四个 PR 合入后关闭对应 issue —— **全部完成**
- [x] #1516 / #1517 / #1522 / #1524 / #1526 / #1527 等已关闭项 —— **全部关闭**
- [x] #1526 遗留的 `action_template` **表级删除**是否作为独立后续动作跟踪 —— **已立 [#1890](https://github.com/DUElost/stability-test-platform/issues/1890)**
      （原判「未跟踪」经续查坐实：`#734` 验收项未被满足却被关闭，`downgrade` 语句被误读为 forward 迁移）
- [ ] 总表 #1515 在全部子项收口后关闭（当前保留，因 #1520 分期进行中 + #1737 衍生项 open + #1890 新立）
- [x] 邻域问题立单 —— **#1783**（PAT scope，已修复闭环）、**#1890**（表级删除缺口）已立；
      `ci/queue-blocked` 误报已被 #1761/#1792 认领并三级闭环，不重复开

---

## 六、方法论

- **状态来源**：`gh api repos/.../issues/{n}`、`.../issues/{n}/comments`、`.../actions/runs`、`.../commits/{sha}/check-runs`，全部实查。
- **不臆断**：凡未见 PR 者一律标「尚未认领」，不假设「有人在做」。
- **排期依据**：严重度 × 影响面 × 修复风险 × 依赖关系；**决策类项单列**，不与开发项混排。
- **失败归因**：CI 红灯先查是否与自身改动相关（比对 `check-runs` 的 job 清单与 PR 文件集），避免误判自因。
- **复核判据方向**（2026-09-13 续查新增）：验证「迁移已删除 X」时，**必须确认该语句位于 `upgrade()` 而非
  `downgrade()`** —— `grep drop_table` 会同时命中回滚路径，仅凭命中即判「已完成」会产生假证据（见 #1890）。
- **终态时点**：2026-09-13 16:10 GMT+8（此时 open PR = 0，批次 12/13 关闭）。
- **续查时点**：2026-09-14 06:33 GMT+8（同步至 `d6fe86ed` 后复核：邻域项全部闭环，新增 #1890 一条）。
