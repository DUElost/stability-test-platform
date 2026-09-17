# 历史 run 兜底改查 DLE 台账（#2188 D 步·单4 / #2476）

Status: implemented
Class: bug-fix

## Decision

`_resolve_center_event_path` 的历史 run 兜底从**跨 run 目录扫描**改为 **DLE 台账查询**：

| | 旧 | 新 |
|---|---|---|
| 机制 | 对中心盘 `devices/*` 逐 run 扫目录树 | 查 `device_log_event.remote_path` 字符串判形态 + `isdir` 校验 |
| 候选顺序 | `sorted(hits)[0]` = **字典序最小（最旧）** | `plan_run_id DESC NULLS LAST, id DESC` = **最新**，`LIMIT 50` 有界 |
| 越界候选 | 不适用 | 只接受落在 `center_root` 下的路径（台账里可能残留别的站点根） |
| 查库失败 | — | 记日志、返回 None → 调用方保留原映射（尽力而为，不抛） |

**与 issue 描述的一处偏差（实测修正）**：issue 给的形态是
`remote_path LIKE '%/devices/{event_dir}/%'`，但**生产实探**表明 `remote_path` 就是事件
**目录**的中心路径且**无尾斜杠**：

```
/mnt/stp-aee/devices/349/2026_0902_054039_144_db.02.JE
/mnt/stp-aee/devices/379/ffedc853-…/2026_0820_150812_226_db.00.ANR   ← 中间可带 uuid 段
```

按 issue 的写法（`…{event_dir}/%`）**一条都匹配不到**。落地模式取
`%/devices/%/{event_dir}`（允许中间的 run 段与 uuid 段，且不误匹配「目录名恰好同名的文件」），
并对 `_`/`%` 做 LIKE 转义（事件目录名形如 `2026_0902_…`，`_` 是单字符通配，不转义会放宽匹配）。

**顺序换成「最新」是有意的**：旧实现取字典序最小 = 最旧 run 的副本，而 retention 已经按
run 清理过——**最新副本最可能还在盘上**（本次改动的主诉求正是「映射到存在的副本」）。

## Alternatives

- **A. 保留 glob、只加缓存/剪枝**：否决。目录扫描的成本随 run 数增长，且与「盘上有哪些
  run」强耦合；台账查询是 O(候选) 且有 `LIMIT` 上界。
- **B. 只查本 run + 该 host 的 job（不做跨 run 兜底）**：否决。兜底要解决的正是「本 run
  未上送」（#2369 验收场景），删掉它会把可达性问题退回人工。
- **C. 用 `nfs_path`/artifact 表查询代替 DLE**：否决。DLE 是事件产物的权威台账
  （ADR-0028），`remote_path` 就是中心路径，语义最贴近。
- **D. 按 id 倒序（而非 run 倒序）**：否决。id 是随机 UUID，排序无意义；按 run 倒序才
  表达「最新一次的副本」。`NULLS LAST` 是必需的：PG 的 `DESC` 默认把 NULL 排最前，
  会把 LIMIT 名额吃光。

## Verification

- **测试**（`backend/tests/services/test_dedup_scan_merge.py::TestResolveCenterEventPath`）：
  - 新增/改写：DLE 兜底命中历史 run、**取最新** run、越界（别的站点根）候选被忽略、
    台账有行但副本已不在盘上 → 回落原映射、空台账 → 回落原映射、集成用例
    （`_rewrite_merge_report_paths_to_center` 端到端）、**契约测试 ③**
    （源码不含 `devices/*` glob 且含 `_center_event_dir_from_dle`）。
  - 全文件 `pytest` → **56 passed**。
- **红绿差分**：`git checkout` 回基线实现后跑本类 → **2 failed**
  （`test_prefers_newest_run`：基线取到最旧副本；`test_source_has_no_cross_run_glob`：
  基线仍有跨 run glob）。其余用例在基线上也通过——它们钉的是**行为不变**（兜底仍能找到
  副本、找不到仍回落），机制判据由上面两条 + 契约测试负责。
- **只读实证**：`remote_path` 形态与 `LIKE '%/devices/%/%'` 命中数（3,561 行）来自
  生产库只读探针（DSN 仅经 `.env.backend`，全程 SELECT）。
- `ruff check`、`check:quick`（10 gates）通过；上游 `dedup_scan`/`dedup_extract` 相关
  用例随全文件一并跑绿。

## Revisit

- **`remote_path` 上的 LIKE 无索引**：`%…%` 形态用不上 B-tree，当前 DLE 量级（数千行）
  是顺序扫描也很快；若事件量级增长到十万+，需要评估（窄化前缀条件 / 表达式索引 /
  改为按 `event_dir` 精确列查询）。
- **候选顺序语义**：本单把「最旧」改成「最新」。若有场景依赖「取最早那份副本」（例如
  审计要追溯首次落盘），需要单独的查询入口——现有调用点（merge 报告路径重写）只关心
  「盘上存在」。
- **单2/单3 的 `_meta` 分片**（#2474/#2475）：本单未触及；它们与单1（#2473）有顺序约束，
  且 #2473 与在窗的 `fix-2444`（retention 同文件）冲突，落地时需先看那条是否已合入。
