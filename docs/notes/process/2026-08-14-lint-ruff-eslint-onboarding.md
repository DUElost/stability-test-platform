# lint: ruff 与 ESLint 接入并清零

Status: implemented
Class: process

## Decision

ruff 与 ESLint 均为阻塞门禁（ESLint `--max-warnings 0`），CI 的
`continue-on-error` 已全部摘除。ruff 于 2026-07 接入、2026-08-04 清零
（#155/#157）；ESLint 于 2026-08-05 清零（#159/#161/#162）。

ruff 暂不开 `UP`(pyupgrade) 族。

## Alternatives

- 接入时直接开 `UP` 族：拒绝。全量 2239 处纯风格改写（`Optional[X]`→
  `X | None` 882、`Dict`→`dict` 633 等）会淹没 F/B 的真实信号，与缺陷无关；
  该债务单独跟踪（#85）。

## Verification

- CI：ci.yml §lint 阻塞；本地 `python -m ruff check backend/ tools/ scripts/`、
  `npm run lint -- --max-warnings 0`（frontend）。
- 统一入口：`python scripts/run_gates.py check:quick`。

## Revisit

处理 #85（UP 收敛）时更新本 note。
