# ADR-0029 P2.5 设计：项目编组工作台（设备事实 + 人工映射）

- 日期：2026-08-20
- 状态：**P2.5a 实施中**（Fleet 只读；映射列仅人工填写）
- 上游：[ADR-0029](../adr/ADR-0029-project-taxonomy-and-param-layering.md)

## 0. 结论摘要

1. **问题**：`/projects` 把 P1 脚本灌入的 `HONOR-MLD` / `ZTE-Z258` 当成「已映射项目」。这些 key 只是当时方便查看设备归属的回填标签，**不能代表客户、项目或机型**。
2. **方向**：Fleet 事实层只展示 ADB/心跳可读的 `device.model` / `platform`；**已映射项目必须人工填写**。回填标签降为非正式编组，单独列示。
3. **P2.5a**：inventory API + 工作台事实表。`mapped_project_keys` 恒为 `[]`，UI 显示「待手动填写」。`backfill_project_keys` 展示 HONOR-MLD 等，并标明非正式。
4. **非目标**：不复活 D5 派发门禁；v1 不用前缀/正则自动推断。

## 4. API（P2.5a）

```
GET /api/v1/projects/inventory/models
```

```json
{
  "model": "MLD_LX2",
  "device_count": 260,
  "platforms": ["MTK"],
  "backfill_project_keys": ["HONOR-MLD"],
  "mapped_project_keys": [],
  "legacy_device_count": 0,
  "null_device_count": 0
}
```

- `backfill_project_keys`：当前 `device.project_id`（P1 回填）。非正式。
- `mapped_project_keys`：人工映射；P2.5a 无规则表，恒 `[]`。
- 静态路径 `/inventory/*` 必须注册在 `/{project_key}` 之前。

```
GET /api/v1/projects/inventory/summary
GET /api/v1/projects/{project_key}/models
```

后者是「当前挂在此回填标签下的型号」，不是「该项目覆盖哪些型号」。

## 关联

- P2.5a Agent Note：[2026-08-20-project-registry-p25a-inventory-workbench.md](../notes/feature/2026-08-20-project-registry-p25a-inventory-workbench.md)
