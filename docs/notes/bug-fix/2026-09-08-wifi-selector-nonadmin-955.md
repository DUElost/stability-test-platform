# R04-F10 落地：普通用户 WiFi 选择器走去密 available 端点（#955）

Status: implemented
Class: bug-fix

## Decision

Plan 执行允许普通登录用户，但 `PlanExecutePage` 的 WiFi 选择器调用
admin-only 的 `GET /resource-pools`：403 后页面无 `isError` 处理，缺省当
空池——普通用户看不到任何已配置 WiFi，无法选择。完整资源池含 WiFi 密码，
不宜放宽现有管理接口（`ResourcePoolOut.config` 含 `password`）。

修复：

1. **新增 `GET /resource-pools/available`**（`get_current_active_user` 即可）：
   只返回 `is_active` 池，`config` 按白名单剥密（`ssid/band/router_ip/
   mac_filter`）——白名单外新增 config 键默认不返回（保守方向，防未来
   机密字段泄漏）。管理接口保持 admin + 完整 config；
2. **`PlanExecutePage`**：WiFi 查询改用 `available`，加载失败走页面级
   destructive 横幅（与 hosts/scripts/recentRuns 错误同款模式 + 重试），
   不再静默成空池；「尚未配置」空态文案只在列表真实为空时出现；
3. `DispatchCockpit` 零改动：剥密后 `config?.ssid` 可选链天然兼容
   （缺 ssid 时只显示 pool.name）。

## Alternatives

- **放宽现有 list 接口给登录用户**——放弃：config 含 password，任何
  放宽都引入凭据泄漏面；管理面 admin-only 是既有安全边界（#904 同类
  原则）；
- **前端错误态只弹 toast 保留空池**——放弃：空池会诱导用户误判「未配置
  WiFi」；横幅 + 重试与页面既有错误模式一致；
- **available 返回整池+运行时后端过滤**——放弃：剥密在响应边界做才可
  审计；白名单方向保守（漏配=不返回而非漏出）。

## Verification

- **反例实证**：回退 route 保留测试 → available 2 用例失败（404）；修复版
  全绿；
- 后端新增 3 用例（`test_resource_pools.py`）：普通用户可取无密码列表且
  ssid 保留 / inactive 不出现 / admin list 仍含 password 且普通用户 403；
- 前端：`PlanExecutePage.test.tsx` 新增错误横幅用例 + 既有 52 用例 mock
  迁移 `available`——**53 passed**；
- `check:quick`（含 eslint/tsc）与 PR 门禁：见 PR 描述。

## Revisit

- 白名单键（`ssid/band/router_ip/mac_filter`）是当前 ResourcePool config
  的实际键集；未来若新增展示性 config 键需同步加白名单，否则 UI 不显示
  （保守安全方向，可接受）；
- WifiPage 管理面仍走完整 list（admin），两端点职责分界在
  `resource_pools.py` 注释中固化。
