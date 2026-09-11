# check_host_duplicates 按 ip 分组键取错列（#834）

Status: implemented
Class: bug-fix

## Decision

`_find_duplicates` 在 SELECT `(id, name, ip, …)` 上按列取分组键：ip → `row[2]`，
name → `row[1]`。抽出 `_group_key` 并单测，避免再把 `id` 当成 ip 键导致每行独组。

## Alternatives

- **改 SELECT 只查分组列**：仍需明细展示 id/name/ip，弃用。
- **用 Row Mapping / 按名取列**：更稳但改动面大；本脚本仅此两列，索引足够。

## Verification

```bash
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/tests/test_check_host_duplicates.py -q
```

## Revisit

无。若后续增加其它唯一列核查，扩展 `_group_key` 映射即可。
