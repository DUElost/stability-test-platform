# God-module：二次下调 god-files 封顶 + projects 入册（#1520）

Status: implemented
Class: bug-fix

## Decision

#1520 主战场切片（catalog / auth / re-export / shape / pairing）已合入
`main`。按棘轮约定「同一批瘦身落地后即下调」：

| 文件 | 实测 | 旧封顶 | 新封顶（×1.05） |
|---|---:|---:|---:|
| `plan_runs.py` | 592 | 745 | **622** |
| `agent_api.py` | 391 | 541 | **411** |
| `projects.py` | 360 | （未入册） | **378** |
| `agent/main.py` | 1622 | 1704 | 1704（持平） |

`projects.py` 是 #1520 三主战场之一，此前漏册——补上以防再胖回。

## Alternatives

- **只下调不入册 projects**：弃——与 issue 证据表三文件不对齐；
- **把封顶钉死为实测不加 5% 缓冲**：弃——与既有棘轮公式不一致，易与旁路
  注释/形状小改打架。

## Verification

- `python tools/dev/check_god_files_ceiling.py --self-test`
- `python tools/dev/check_god_files_ceiling.py`（四文件均绿）
- `check:quick`

## Revisit

- 路由侧已基本薄壳；残余是 docstring / HTTPException 映射，再切收益低；
- 建议 #1520 在本棘轮合入后评估关闭（ledger 举证行数与 service 边界）；
- Issue #1520 本 PR 仍 `Refs`，不自行 close。
