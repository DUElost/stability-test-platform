# ADR 索引：0045 落地口径 + M7 看板补全（#2989）

Status: implemented
Class: bug-fix

## Decision

1. 主表 ADR-0045 备注「未落地」→「**已落地**（`adf9c24a`）」——与代码/前端词表一致。
2. 「Proposed 里程碑看板」改名为「里程碑看板（由主表派生）」，M7 行补上
   0036/0038–0042/0044–0050，并**显式列出**三条真正 `Proposed`（0039/0046/0047）。
3. 维护约定补一句：主表写 M7 必须同批进看板。
4. 静态守卫 `tests/test_adr_index_status_2989.py` 防 0045 再标未落地、防看板漏 M7。

## Alternatives

- **看板只列 Proposed 三条**：标题字面更准，但会丢掉「M7 进行中还有哪些 Accepted」
  的导航价值；改为派生视图 + 未决前置更贴读者。

## Verification

- `python -m pytest tests/test_adr_index_status_2989.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

新增 M7 ADR 时同步改主表与看板；守卫会抓漏编号。
