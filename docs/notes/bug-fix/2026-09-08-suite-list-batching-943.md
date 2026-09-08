# 套件列表批量化：消除 1+2N 查询（#943，R03-F11）

Status: implemented
Class: bug-fix

## Decision

`list_suites` 对每套件调 `_suite_out` → `_case_rows`（查询 1）+
`_current_fingerprint` → `suite_case_rows`（查询 2），N 个套件 1+2N 次查询且
用例 JSON 重复读取（#943）。

修复：新增 `_suite_outs` 批量路径——全部套件的用例行**一次 IN 查询**取回
（`ORDER BY suite_id, ordinal, id` 与单套件口径一致），指纹用纯函数
`content_fingerprint` 本地复算；`_suite_out` 增加可选 `cases`/`current` 预取
参数，detail/单套件路径缺省行为不变。行形状与
`suite_binding.suite_case_rows` 逐键一致（fingerprint 输入契约，含
`exec_descs or []` 归一）。

**未加分页**（issue 备选路径之一）：套件是管理员维护的小表，全量返回是前端
依赖的既有 API 契约；批量化已消除查询数随 N 的增长（验收第一 clauses 直接
满足）。若未来套件规模上量，再加 `limit/offset` 属增量改动。

## Alternatives

- **selectinload(TestSuite.cases) 走 ORM 关系**——放弃：`suite_case_rows`
  的 docstring 明确记录了为什么显式查询——`expire_on_commit=False` 会话下
  identity map 会把过期集合喂给指纹计算，「库改了没导出」漏检（#402 家族
  教训）；批量 IN 查询保持同一语义；
- **加分页（limit/offset）**——见上，属 API 形状变更，非本缺陷必需；
- **在 `_current_fingerprint` 里做缓存**——放弃：缓存失效语义复杂且仍逐
  套件查询；批量化从根上消除。

## Verification

- `test_mtbf_suite_routes.py` **34 passed**（新增 2 用例：1 套件 vs 3 套件
  的 `test_case` 表查询数相等且 ≤2（旧实现 1+2N=7）、列表 case_count/
  enabled_case_count/export_stale 与批量前判定一致）；
- `check:quick` 7 门禁全绿。

## Revisit

- 列表若未来需要规模上限，`limit/offset` 增量加入（docstring 已留语义说明）；
- `_current_fingerprint` 在 detail 路径仍逐套件查询——单套件场景无增长问题，
  不动。
