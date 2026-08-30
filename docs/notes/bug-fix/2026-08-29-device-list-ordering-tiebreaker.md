# 设备列表排序补 id 稳定次序键

Status: implemented
Class: bug-fix

## Decision

`GET /api/v1/devices`（`backend/api/routes/devices.py`）此前只按
`Device.last_seen.desc().nullslast()` 排序。`last_seen` 并列时（新登记设备全为
NULL 是常态）**PostgreSQL 不保证组内顺序**，堆序偶然翻转即导致：

- 测试 `test_devices_ordered_by_id` 偶发红（`assert [3, 2, 1] == [1, 2, 3]`，
  PR #536 的 CI 命中过一次，rerun 即绿）；
- 生产侧更实际的问题：分页（skip/limit）在并列组内不稳定，翻页可能重复/漏行。

改为 `order_by(Device.last_seen.desc().nullslast(), Device.id.asc())`——`id` 是
主键，唯一且单调递增，作为 tie-breaker 后整个结果集全序确定。

方向取 `id` 升序：与测试原本断言的 `[1, 2, 3]` 一致，也与「同批次登记的设备按
登记先后呈现」的直觉一致。

## Alternatives

- **只改测试断言为集合比较**：#582 已经这么做过一次（为了解 CI 红灯）。放弃——
  它只消掉了红灯，路由侧的顺序依旧不确定，分页重复/漏行的隐患原样留着；且断言
  退化成集合后，将来真有人改坏排序，测试也拦不住。
- **按 `serial` 或 `created_at` 做 tie-breaker**：`serial` 是字符串（字典序，
  对用户不直观），`created_at` 同批登记可能完全同值（批量导入），仍不唯一。
  `id` 是唯一能保证全序的那一列。
- **加 `nullslast()` 之外的二级业务排序**（如 status）：会改变现有列表的业务语义，
  超出本 issue 范围。

## Verification

- `backend/tests/api/test_devices.py`（SQLite 模式全绿）。注：该用例的断言在 #582
  时已被放宽成集合比较，本次保留该形态——并列组内顺序现在虽然稳定了，但断言依赖
  id 顺序会让「排序语义」和「id 升序」耦合，不是本 issue 想固定的契约。
- 人工核对：造 3 台 `last_seen` 全 NULL 的设备，连续请求 10 次，返回顺序恒为
  id 升序。

## Revisit

- 若将来设备列表改为按业务字段（如 status / project_key）排序，`id` 应继续保留
  为最后一级 tie-breaker。
