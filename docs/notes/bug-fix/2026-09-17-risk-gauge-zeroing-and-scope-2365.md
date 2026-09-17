# `stability_risk_jobs_by_level`：无 job 也要归零，作用域请求不改写全局值（#2365 重开残余）

Status: implemented
Class: bug-fix

## Decision

重开说明把本单收窄到覆盖率指标自身的两处口径问题（活链接线与覆盖率指标本身保留），
本单就只修这两处。

### 1. 写 gauge 移出 `if total_jobs > 0`

四桶的 `.set()` 原先全在那个块里 —— 「job 被清空」时整段跳过，gauge **停在上一轮的非零值**。
后果正好抵消本单的目的：我们要区分「无判定依据」与「判据坏了」，而一个冻结在旧值的
gauge 会把「已经没有数据」也显示成「分布还在」。也违反仓库自定纪律
（`cron_scheduler.py` 里那句「清空后 gauge 必须回到 0」—— #2144 立的）。

### 2. 只有全局口径写 gauge

`stability_risk_jobs_by_level` 只有 `level` 一个标签；带 `project_key` 的请求算的是
**作用域分布**，却写进同一条序列 → 一次项目筛选就把全局值覆盖掉。修法取重开说明里的
第一分支：**作用域调用不写 gauge**（响应仍按作用域返回，两件事分开）。

刻意**不加 `scope` 标签**（第二分支虽也允许）：`project_key` 是用户数据，拿它当标签等于
把基数交给项目登记簿的增长；而覆盖率要观测的对象本来就是全局分布。少一个标签、也少一个
"谁都能往里加基数"的口子。

## Alternatives

- **加 `scope` 标签，让全局与项目各自成序列**：否决，理由见上（基数交给用户数据）。
- **给 gauge 挂 TTL / 由采集端重置**：否决。这是把"调用即写入"的语义问题推给观测基础设施；
  正确性应由写方负责（与 #2144 那轮「保留清理 gauge 必须回零」同源）。
- **顺手把覆盖率做成"有 job 但无信号"的比率指标**：否决，超出收窄后的范围；现在的四桶
  已足够让 `high+medium+low / total` 在查询侧算出来。

## Verification

`backend/tests/api/test_results.py` **7 passed**，其中新增 2 条**对旧实现红**（逐条对应两条验收判据）：

- `test_gauge_returns_to_zero_when_all_jobs_are_gone`：先造 job → 全局调用 → 记录器拿到
  一次非零写入；再清空全部 job → 调用 → 断言 `values == {high:0, medium:0, low:0, unknown:0}`。
  前提断言（"先要有非零写入"）也写明 —— 不然这条用例会在一个"从没写过"的假绿上通过。
- `test_project_scoped_call_does_not_rewrite_global_gauge`：全局 4 个 job、项目内 1 个；
  全局调用后记录器 = `unknown:4`；再以 `project_key` 调用 → 响应桶和为 1（作用域算法没坏）
  而**记录器保持空**（这次调用不得写全局 gauge）。旧实现会在这里把 4 覆盖成 1，正是可区分点。
- 用记录器替身（`monkeypatch` 掉 `results.risk_jobs_by_level`）而不是读 `REGISTRY` 终值：
  沿用同文件 #2365 既有用例的做法，避免用例之间通过全局注册表互相污染。
- 标签集合未变（仍是 `level`），故 `tests/tests/test_prometheus_alerts_contract.py` / 告警与
  仪表板契约不受影响；实际跑的结果见 PR。
- `check:quick`、`ruff`：见 PR。

## Revisit

- 指标名与语义都写着"最近一次 `/results/summary` 计算结果"，本质是**拉取式端点喂推取式
  gauge**：无人访问 `/results/summary` 时它是陈旧的。真要按仪表盘长期观测，出口是把它挪进
  调度任务周期计算（与 `/storage` 那套 node-exporter textfile 的做法同类），或在名字/HELP 上
  把"last scrape of the summary endpoint"的时限写得更硬。本单不动语义，只记风险。
