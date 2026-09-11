# WiFi loads 公网 config + 编辑拉详情（#954）

Status: implemented
Class: bug-fix

## Decision

1. `/resource-pools/loads` 响应增加 `config`（仅 `_PUBLIC_CONFIG_KEYS`：
   ssid/band/router_ip/mac_filter，不含 password）。
2. `ResourcePoolLoad` 前端类型不再错误 `extends ResourcePool`。
3. `WifiPage.startEdit` 调用 `GET /resource-pools/{id}` 拉完整凭据再填表。

涉及：`resource_pools.py`、`resource_pool.py`、`WifiPage.tsx`、`types.ts`；
测试见 `test_resource_pools.py::test_loads_includes_public_config_without_password`。

## Alternatives

- loads 返回完整 config：列表泄漏 password，违背 #955 剥密方向。
- 仅改类型不补 loads config：SSID 仍空白。

## Verification

- `pytest backend/tests/api/test_resource_pools.py::test_loads_includes_public_config_without_password -q`

## Revisit

若 loads 需更多展示字段，扩 `PUBLIC_CONFIG_KEYS` 并同步 available 端点。
