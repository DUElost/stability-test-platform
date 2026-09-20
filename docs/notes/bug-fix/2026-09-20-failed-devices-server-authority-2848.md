# 失败设备排行以服务端为单一权威：滤掉零失败组 + 前端不再二次排序/截断（#2848）

Status: implemented
Class: bug-fix

## Decision

**先纠正文里的引用**：#2848 指的文件行号是 `stats.py:347-358`，那是
`/stats/host-failure-rate`（主机失败率）；本单说的是 `/stats/plan-failed-devices`
（`stats.py:377+`）。两条 SQL 形状相似、`HAVING COUNT(*) > 0` 都在，按引用去改会把
**语义不同的另一条**一起改了——所以本单只动 plan 那条，主机失败率那条**故意不动**（见下）。

两处权威问题，一起收到一处：

1. **后端 `HAVING COUNT(*) > 0` 滤的是空组，从不滤零失败**（每组必然 ≥1 行，条件恒真）。
   于是健康窗口里所有 plan 都带着 `failed=0` 返回，前端的空态判据
   （`data.length === 0`）永远走不到，图里是一排零高柱 + 数字 0。
   改成 `HAVING SUM(CASE WHEN status IN ('FAILED','ABORTED') THEN 1 ELSE 0 END) > 0`
   ——**过滤落在度量本身**。
2. **前端又排一次、再切一刀**：`.sort((a,b)=>b.failed-a.failed).slice(0,10)`。
   排序键比服务端少一个 tie-break（`failed DESC, total_jobs DESC`）⇒ 同分次序两边各说一套；
   `slice(0,10)` 更实在——接口 `limit` 允许到 50，调用方要 24 条会被**静默截回 10**，
   属于「改了没生效」最难查的那类。现在前端只做标签截断，顺序与条数以服务端为权威。

纯函数 `buildFailedDeviceRows` 单独成模块（`failedDeviceRows.ts`）：一是
`react-refresh/only-export-components` 要求组件文件只导出组件（第一版同文件导出被判
warning，`--max-warnings 0` 直接红），二是仓内已有同形先例（`deviceTableVirtual.ts`、
`minimapGrid.ts`），纯算术独立后 jsdom 才能直接断言「不重排、不截断」——**图的几何测不了，
但顺序与条数测得了**（`testing.md` §4 的边界）。

## Alternatives

- **在前端 `filter(d => d.failed > 0)` 遮丑**（正文给的方案 A）：否决。数据面仍返回一堆
  零行，`limit` 语义继续错（24 条里 20 条是零 ⇒ 有效柱只有 4 根），且每个未来消费方都得
  重做一遍同一个过滤。
- **只删前端 sort、保留 slice**：否决。`slice(0,10)` 与 `limit` 参数正面冲突，比排序更硬。
- **`/stats/host-failure-rate` 一并加 `failed > 0`**：不做。它排的是**失败率**，零失败主机
  在「谁最不稳」的榜里是榜尾事实、不是噪声；且它还承着 ADR-0038 D-5 的「历史口径不排除退役机」
  决定。同一段 `HAVING` 形状、不同语义——**照形状批量改就是顺手重构**，本单不收。
- **把 limit 挪到前端做分页**：否决。分页是 #83/#496 那条轴，不在这里发明第二套。

## Verification

- 后端：`pytest backend/tests/api/test_stats.py -q` → **21 passed**（改 1 条契约断言 + 加 2 条）
  - `test_ranks_by_failed_devices_desc` 的期望从「bad-plan + good-plan(failed=0) 共 2 行」
    改成「只有 bad-plan」——**这是有意的契约变更**，注释写在断言旁边；
  - `test_healthy_window_returns_no_rows_so_empty_state_shows`：全通过窗口 → `items == []`
    （前端才拿得到空态）；
  - `test_tie_break_is_servers_alone`：同 `failed` 时按 `total_jobs DESC`（服务端唯一权威）
- 前端：`vitest run` → **129 文件 / 1050 passed**（新增 4 条纯函数用例：服务端次序保持、
  24 条不被截回 10、标签截断、空/undefined 走空态）；`tsc --noEmit` 0；
  `eslint src --max-warnings 0` 0；`ruff check` 通过
- **变异自证**（逐条红，还原后全绿）：① 把 `.sort().slice(0,10)` 加回前端 → 「不截断」用例红；
  ② 把 `HAVING` 改回 `COUNT(*) > 0` → 后端 2 条用例红（排名契约 + 空态）
- **自己踩到并当场被测试抓住的一次**：第一版把解释性注释用 `#` 写进 SQL 字符串里，
  PostgreSQL 不认 `#` 注释 → 端点直接语法错误，`test_stats.py` 立刻红。注释挪回 Python 侧，
  并把这件事写进代码注释（下一个人别再往 SQL 里塞 `#`）
- 仓库级：`pytest tests/ -q` 与治理面检查见 PR（本单不动测试基线之外的面）

## Revisit

- 这张图叫「失败**设备**数排行」，SQL 数的是 `job_instance` 行数（同设备多轮会计多次）。
  口径命名与度量是否要一致，属产品判断，本单**没碰**（改它要动 SQL 聚合键与前端文案，
  且会改变历史榜单形状）。要收就单开一单。
- 主机失败率那条若将来也要滤零，先回答「榜尾的零失败主机是不是信息」；不要按形状对齐就改。
