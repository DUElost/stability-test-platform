# God-module 三主战场完结举证（#1520）

Status: implemented
Class: bug-fix

## Decision

#1520 开单时三文件分别为 **3328 / 3239 / 997**（`projects` 引 0 service）。
按「垂直切片、不整体重写」拆完后，`origin/main` 实测：

| 文件 | 开单 | 完结 | 封顶（棘轮） | service 面 |
|---|---:|---:|---:|---|
| `plan_runs.py` | 3328 | **482** | 507 | `plan_run_*` ×19 |
| `agent_api.py` | 3239 | **391** | 411 | `agent_*` ×19 |
| `projects.py` | 997 | **360** | 378 | `project_*` ×5 |

失败场景对照：

1. **测试不可达** → 业务在 service，路由薄壳；多切片附 service 直测；
2. **私有函数外泄** → re-export 已删，测试改从 service 导入；
3. **git churn** → 单文件从三千行级降到五百行内；
4. **projects 0 service** → registry / mapping / inventory / catalog 齐全。

防回流：`tools/dev/check_god_files_ceiling.py` 已纳入三路由 +
`agent/main.py`，超限即红。

本 Note 随关闭 #1520 合入；不宣称「路由层从此无债」，只关闭本单
开单范围内的 H 级结构债。

## Alternatives

- **继续啃 docstring / HTTP 映射**：弃——边际收益低于其它 OPEN 单；
- **扩大到 `plans.py` / `hosts.py`**：弃——超出本单证据表，另开单。

## Verification

- `wc -l` 与 `check_god_files_ceiling` 四文件全绿（完结当日 main）；
- 在飞 #1520 切片 PR 均已 MERGED（含 #2681 抛光与三次棘轮）。

## Revisit

- 若棘轮再次被顶满，按「同批下调」开新单，不必重开 #1520；
- 更大路由（`plans` / `hosts` / `scripts`）若要同类治理，新开 tech-debt。
