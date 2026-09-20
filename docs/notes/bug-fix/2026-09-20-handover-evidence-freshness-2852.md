# handover 证据：候选槽「最新运行赢、同运行取最坏」（#2852，修正 #2718 语义）

Status: implemented
Class: bug-fix

- 日期：2026-09-20
- 关联：`#2852`（本单）、`#2718`（引入累积视图的那一单——本单修正其语义）、
  `#2404`/`#2706`（候选槽机制）、`docs/notes/bug-fix/2026-09-18-install-evidence-per-release-2718.md`

## Decision

`install-state.json` 的证据桶条目由 `{check_id: status}` 改为 `{check_id: {status, run}}`
（`install.py` 写侧盖运行序号），`handover._match` 的命中规则随之收严（`handover.py` 读侧）：

1. **候选组内只认最近一次发出该槽的运行**（组内成员取 `run` 最大者）——互斥路径的旧值就此
   退役，两个方向都不再误判；
2. **同一次运行内多成员在场时取最坏**（`FAIL > BLOCKED > PASS`）——一个 PASS 永不遮蔽兄弟 FAIL；
3. 都不存在才算缺失（文案仍给 `A or B`）。

### #2718 的语义哪里没兜住

#2718 的承诺是「历史不得掩盖刚发生的失败」，但实现只做了 **同 ID** 的覆盖
（`bucket[check_id] = ...`）+ 读侧「按候选顺序取第一个存在」——**互斥的两个 ID 之间没有任何
新鲜度关系**，于是两个方向都坏（#2852 实测）：

| 方向 | 序列 | 修前结果 |
|---|---|---|
| a（假 PASS，最危险） | run 1 `install.s3.db`=PASS 入桶；run 2 迁移失败只发 `install.s3.migrate`=FAIL | 命中桶里旧 PASS ⇒ **站点装坏而 handover 判 PASS 出文件** |
| b（假 FAIL） | run 1 数据库不可达 `install.s3.db`=FAIL 入桶；run 2 走迁移 `install.s3.migrate`=PASS | 命中旧 FAIL ⇒ handover 判 failed 且**拒写文件** |

**为什么不是「取全部在场候选的最坏」就够**（issue 建议里的前半条）：那只修了方向 a ✗——
方向 b 里旧 FAIL 仍是「最坏」，新的 PASS 照样被掩盖。真正的判据是**新鲜度**（run 序号），
最坏只是同一新鲜度内的并列规则 ✓ 故两条一起才闭合。

**兼容**：旧格式（桶值是纯字符串、无 run）按 `run=0` 读入 ✓；`stages`（最近一次运行）用
state 的 `runs` 当序号 ⇒ 恒比桶里旧条目新 ✓。既有 186 条用例中只有 2 条断言旧形状，已更新。

## Alternatives

- **只修 `_match`（取全部在场候选的最坏）**：方向 b 仍误判（见上）。否。
- **只修写侧（每次运行清空桶里同组旧 id）**：写侧不知道「哪些 ID 互为候选」——那组关系在
  `handover.ACCEPTANCE_ITEMS` 里，让 `install.py` 去 import 它会反向耦合（安装链依赖验收映射）。
  把 run 序号写进证据、由读侧套用组关系，职责更顺。否。
- **彻底不累积（回到只看最近一次运行）**：那正是 #2718 要修的「plain 重跑抹掉 S5 证据」。否。
- **给候选组也存「组 id」**：要在证据里再引入一层分组键，且分组会随验收表变动漂移；
  「同组 = 同一槽位的候选」在读侧天然可知。否。

## Verification

- `python -m pytest tests/test_site_handover.py tests/test_site_install.py tests/test_site_agents.py -q`
  → **186 passed**（新增 3 条：#2852 方向 a / 方向 b / 同运行取最坏；更新 2 条断言旧桶形状的用例）；
- **3 条定向变异逐条回退即红**：退回「按候选顺序取第一个存在」→ **2 failed**（两个方向都红）；
  忽略运行序号（全部在场候选取最坏）→ **1 failed**（方向 b，正是「只做最坏不够」的实证）；
  同运行内不取最坏 → **1 failed**；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**；
- **一条自纠**：改完第一版后 17 条 handover 用例集体红——根因是我在 `_match` 里对**已归一化**
  的 `(status, run)` 又调了一次 `_status_run`（把元组当字符串）。全红这个信号（连无关用例一起红）
  正是「结构性改坏了」而非「断言该更新」，据此定位。

## Revisit

- **桶值的形状变了**（`status` 字符串 → `{status, run}`）：`installation.md` /
  `site-handover-and-navigation.md` 只写了「按发布物累积」的语义面，未描述条目内部形状，故无需改；
  但若将来有人以脚本直接读该文件，须按新形状解析（旧文件仍可读 ✓ 读侧兼容）。
- **`run` 的语义 = 安装记录的 `runs` 计数**（不是时间戳）：同一 state-dir 内单调递增 ✓；
  若将来支持「并发安装到同一 state-dir」，序号不再是全序（`install` 有 flock ✓ 当前安全）——
  与 #2718 的写侧竞态前提同源。
- **`_verify_index` 统一为 `(status, 0)`**：verify 报告没有运行序号，与 stages 索引同形只为了让
  `_match` 只认一种形状 ✓；若将来 verify 证据也需要新鲜度（例如多次 verify 的报告并存），
  要给它补序号——那属另一个决策面。
