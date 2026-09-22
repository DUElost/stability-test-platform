# 设备页批量开关滑动留痕

Status: implemented
Class: feature

## Decision

在 `/devices` 底部悬浮栏为 admin 增加「开启滑动留痕」「关闭滑动留痕」两按钮。

1. **设置项**：同时写 `show_touches` + `pointer_location`（`1`/`0`），对应开发者选项里的触摸点与指针轨迹。
2. **通路**：`POST /api/v1/devices/bulk-swipe-trail` → 按 `host_id` 分组 → Agent control `set_device_swipe_trail`；Agent **硬编码**两条 `settings put`，API 不接受任意 shell。
3. **结果口径**：无 host / host 非 ONLINE → `skipped`；Agent 未连 / RPC / 单台 adb 失败 → `failed`；toast 汇总「成功 N · 失败 M · 跳过 K」。

## Alternatives

- **开放任意 ADB 输入框**：排障灵活，但扩大攻击面与误操作面；否决。
- **只发 `show_touches`**：部分 ROM「滑动留痕」只绑这一项，但指针轨迹常一起需要；已确认两者同开同关。
- **走脚本 Job / Plan**：过重，且会占设备租约；本需求是运维瞬时配置。

## Verification

- `.venv/bin/python -m pytest backend/agent/tests/test_control_handler_736.py backend/tests/api/test_devices_bulk_swipe_trail.py -q` → 15 passed
- `npm --prefix frontend run test -- --run DeviceBulkActionBar DevicesPage` → 13 passed
- ruff / tsc / compileall on touched paths → green
- `check:quick`：本机 `schema-at-head` 因业务库 alembic 落后于 code head 红灯（与本变无关、禁止对生产库 upgrade）；其余静态门禁按文件跑通

## Revisit

- 合入后须 **Agent 热更新** 才有 control 分支；仅部署控制面无效。
- 大批量（数百台）可后续加进度面板；当前 toast 汇总即可。
- 若某 OEM 滑动留痕键不是这两项，再按机型扩展白名单，仍禁止自由 shell。
