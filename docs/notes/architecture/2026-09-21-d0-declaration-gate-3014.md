# D0 新族门禁改为「归类声明即放行」（#3014 案 3A-1，选项 A）

Status: implemented
Class: architecture

## Decision

owner 按推荐采 **A**：`check_new_script_family.py`（#3005 落地的 v1.7 门禁）的判据从
「新增顶层族 → 一律红」改为「**三态按声明判定**」：

| 声明 | 结果 | 依据 |
|---|---|---|
| `platform-authored` | 🟢 放行 | 纯 adb/python 自研能力不是 D0 对象（ADR-0033 §5.6 口径） |
| `external-tool` | 🔴 红 | 外部工具在 §5.4 触发前**无合法 in-tree 出口**，须走中心存储 + env 并登记 legacy 例外 |
| 未声明 | 🔴 红 + 给出声明写法 | 沉默不得通过 |

声明写在 **diff 或提交说明**内任一行即可：`ADR-0033 归类：<family> = platform-authored`。
判据实现为三个纯函数（`parse_declarations` / `classify_new_families` / 既有 `find_new_families`），
`--self-test` 各含红绿样例；取不到 diff 时**降级为未声明（fail-closed）**，不放行。

**刻意不做豁免清单**：清单 = 第二个事实源，会与 ADR §5.6 口径长期漂移。声明随 PR 一起被审。

## Alternatives

| 选项 | 否决理由 |
|---|---|
| B 保留"一律禁新族"+ 代码内豁免名单 | 实测门禁里原本**没有**任何名单机制 → 属净新增事实源；且 `clear_recents` 那类改动每次都要改 `tools/dev/` |
| C 一律禁新族、例外走 ADR | 代价已量化：近 7 天 3 次合法入场（`unisoc_probe`、`unisoc_signal_trigger`、`clear_recents`）重放全红，等于每加一个设备能力脚本都要一次 ADR 例外 |
| 要求声明必须写在 ADR-0033 §5.6 文件内 | 把每次新族入场都变成 ADR 编辑 + 版本 bump（#3014 已证明 0033 是多线版本竞赛热点），成本与撞车面都高 |
| 用 `归属域：semantic-ownership <key>` 当声明载体（最初提案） | 该字段是 ADR 头部语义（S15⑦），塞进脚本族会让"表行"变成族登记处，越出索引权责（§0：对内容零裁决权） |

## Verification

`--self-test` 通过（含 4 组新增纯函数样例：后写覆盖 / 空族名不解析 / 三态划分 / 空输入）。

端到端重放（scratch worktree：`32096f1e^` + 该提交的 `clear_recents` 树，父即基线）：

| 用例 | 提交说明 | 结果 |
|---|---|---|
| ① 新族·未声明 | — | `exit=1`，`? …clear_recents/ 未声明归类` |
| ② 新族·声明自研 | `= platform-authored` | `exit=0`，`OK: 已声明为平台自研能力…` |
| ③ 新族·声明外部 | `= external-tool` | `exit=1`，`✗ …已声明 external-tool` |
| ④ 既有族新版本 | `9ff0455e` | `exit=0`（#3005 原语义不变） |
| ⑤ 现网回归 | `--base origin/main --head HEAD` | `exit=0`，无新族 |

`ruff check` 通过。**注**：`ruff format` 会顺手改写作者原有的 `_git()` 与 `_report()` 两处排版，
已手工还原，保证 diff 只含本单必需改动。

临时 worktree（`/tmp/replay`、`/tmp/gatefix`）均已 `git worktree remove`（未用 `--force`）。

## Revisit

| 条件 | 动作 |
|---|---|
| 有人用 `platform-authored` 洗白真外部工具 | 判据客观（厂商二进制 / 第三方版权头 / 大体积资产），review + `grep -rl "Permission is hereby granted" backend/agent/scripts/` 应恒为 0 命中即可发现 |
| §5.4 包存储触发条件成立 | `external-tool` 的红改为"必须带 `tool_manifest.yaml` + 包形态"，届时 `verify_tool_contract.py` 已就位 |
| 声明需要机器可审计的落点（而非提交说明） | 再考虑 §5.6 表内登记，避免现在就把 ADR 变成高频改动漫点 |
