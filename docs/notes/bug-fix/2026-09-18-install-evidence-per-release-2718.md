# 站点安装证据按发布物累积：handover 不再只看最近一次运行（#2718，方案 A）

Status: implemented
Class: bug-fix

- 日期：2026-09-18
- 关联：`#2718`（本单，owner 裁决 **A**）、`#2706`（MS-04 候选集，本议题的原始记录）、
  `#2404`（MS-01 候选集）、ADR-0044（安装终态落审计）、
  `docs/notes/feature/2026-09-15-multi-site-navigation-and-handover.md`（原「只读最近一次」的取舍）

## Decision

`install-state.json` 新增**按发布物累积的证据视图** `evidence[<release>] = {check_id: status}`，
`handover` 的 MS 项改为读它（`tools/site_config/install.py` 写、`tools/site_config/handover.py` 读）。

**为什么**：`stages` 只记**最近一次运行**，而不同运行形态发出的证据 ID 不同——
plain `install.sh --yes`（S0–S4，**文档化的升级路径**）不产出 `install.s5.*`，于是它会把
上一次 `--through-agents` 留下的 S5 证据抹掉，MS-01 随即假 BLOCKED（238 现场；两条 S3 互斥
ID 造成的那两次已由 #2404/#2706 的候选集修掉，这一层是根因）。

### 语义（三条，都有独立用例钉住）

| 规则 | 为什么 |
|---|---|
| **同 ID 以最新状态覆盖** | 历史不得掩盖刚发生的失败：上一轮 S5 PASS、这一轮 S5 FAIL ⇒ 取 FAIL |
| **本次没发的 ID 保留**（同发布物内） | 这正是要补的那一半：S5 证据活过 plain 重跑 |
| **跨发布物不继承** | 旧发布物的证据不得满足本次验收——只读 `state["release"]` 对应的桶 |

实现要点：

- **合并发生在写侧**（`_accumulated_evidence`）：读回既有 `evidence` → 用本次运行的
  `stages` 覆盖同 ID → 落盘。故 handover 侧只需「先铺 `evidence[release]`、再用最近一次
  `stages` 覆盖」，两个消费面共用同一套规则（没有各自的推导）。
- **旧格式折入**：升级本工具的那一次运行不会丢历史——上一份状态若只有 `stages`（无
  `evidence`），把它折进**它自己的发布物**桶。
- **保留窗口**：`EVIDENCE_RELEASES_KEPT = 5`（handover 只读当前发布物，旧桶纯备查），
  避免文件随升级次数无限增长。
- 旧格式状态（只有 `stages`）在读侧**行为不变** ✓（既有 93 条用例全绿）。

## Alternatives

- **B（文档口径：升级必须带 `--through-agents`）**：把 26 分钟的全量跑写进每条升级路径，
  且 plain 运行后证据仍会退化。owner 裁决时未选。
- **C（plain 运行也补 S5 证据：S5 只做只读核对）**：能补新证据，但不解决「**任何**运行形态
  都会重写状态、抹掉别的形态发过的证据」这一层根因；本单的方案对**将来新增的任何 ID**
  同样成立。未选为本次方向（可作后续增量，见 Revisit）。
- **handover 直接回放 `audit_logs`**（ADR-0044 面）：审计里有全部 `install_agent*` 记录，
  但不带 stage 级 `check_id`，映射要另造一层；且 handover 会因此依赖库连接（当前是纯文件）。
  记入 Revisit。
- **把 `stages` 改成历史列表（append-only）**：读侧要自己实现「取哪个运行」的规则，
  且 `verify`/`install` 的既有消费者都要跟着改；改成一个**已合并的映射**让两侧都只读不推导。
  否。
- **无上限累积所有发布物**：文件虽小，但没有任何消费者读旧桶；留窗口更诚实（避免"留着但没人用"
  的沉淀）。否。

## Verification

- `python -m pytest tests/test_site_handover.py tests/test_site_install.py tests/test_site_agents.py -q`
  → **183 passed**（新增 5 条：handover 侧 3 条 + install 侧 2 条）；根套件 `pytest tests/ -q` → **1542 passed**（1m57s）；
- **5 条定向变异逐条回退即红**（每条只动一处、跑完还原）：

  | 变异 | 期望红 | 实测 |
  |---|---|---|
  | handover 忽略 `evidence`（退回只看最近一次） | 累积证据用例 | **1 failed** |
  | 跨发布物也读（去掉 release 分桶） | 跨发布物不继承用例 | **1 failed** |
  | 历史掩盖最新（`setdefault` 代替覆盖） | 最新优先用例 | **1 failed** |
  | 去掉保留窗口上限 | 有界用例 | **1 failed** |
  | install 不折入旧格式 | 折入用例 | **1 failed** |

- `python scripts/run_gates.py check:quick` → **12 gates 绿**；
- **一条自纠**：第 3 条变异最初**没红**——我的用例把整个 `stages` 替换掉，导致其它槽位一并缺失、
  MS-01 本来就判 BLOCKED，量到的不是「同 ID 谁赢」。改成**追加**一条 S5 条目后才拿到预期的红。
  （教训与 #703① 那次同源：**变异的锚点与用例的其余部分都必须有判别力**。）

## Revisit

- **C 方案（S5 只读核对）仍可作增量**：本单让证据活过重跑，但**从未跑过 `--through-agents`
  的站点**依然没有 S5 证据（只能靠 B 的 26 分钟全量或 C 的只读核对补）。若现场出现这类站点，
  按 issue 的 C 提案增补「不重装只核对」模式即可——与本单的累积视图正交。
- **保留窗口 5 的取值依据**：handover 只读当前发布物，5 是「够人回看最近几次升级」的经验值；
  若将来要按发布物做**历史对账**（例如"这个站点在 rel-A 时是什么状态"），应改为按需查询而不是
  调大常量。
- **写侧合并的竞态**：`install` 有 flock（0600 原子写）串行化，故读-改-写是安全的；若将来
  出现**并发安装**（同 state-dir 两个进程），需要复核这条前提。
- **`evidence` 与 `runs` 的关系未做**：累积视图不记录「每个 ID 是第几次运行发的」；若将来要
  回答「S5 证据是哪一轮留下的」，需要把桶升级成 `{check_id: {status, run}}`（体积仍可控）。
