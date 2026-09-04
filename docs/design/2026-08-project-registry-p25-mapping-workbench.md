# ADR-0029 P2.5：登记簿 = Fleet 事实 + 人工项目 + 精确映射

> ⚠️ 2026-08-31 起已被 ADR-0029 v2.5 派生归属重设计取代（见修订行）：映射实体
> `project_device_rule` → 更名 `project_model`，`device.project_id` 列已删除，
> 归属按活跃 `project_model` 成员行派生；本页保留为 v2.4 工作台记录，API 与
> 口径以 v2.5 及代码为准。

- 日期：2026-08-20
- 状态：**历史记录**（v2.4；已由 ADR-0029 v2.5 取代）
- 上游：[ADR-0029](../adr/ADR-0029-project-taxonomy-and-param-layering.md) v2.4

## 0. 结论

1. `/projects` 是登记簿，不是从 ADB 推断出的客户/系列目录。
2. Fleet 表只展示心跳可读的 `device.model` / `platform`。
3. 下方卡片只列出 `source=USER` 的人工项目。
4. 型号→项目必须勾选后 `match_models` 精确映射；SEED / LEGACY / NULL 不算冲突。其他 USER 项目冲突时 apply 返回 409，须 `reassign_conflicts`。
5. `HONOR-MLD` 等六个 P1 key 留在库里只为外键，不出现在工作台。

## API

```
GET  /api/v1/projects                 # 默认 source=user
POST /api/v1/projects                 # admin 新建 USER
GET  /api/v1/projects/inventory/models
GET  /api/v1/projects/inventory/summary
POST /api/v1/projects/{key}/map/preview
POST /api/v1/projects/{key}/map/apply
```

inventory 行：

```json
{
  "model": "MLD_LX2",
  "device_count": 260,
  "platforms": ["MTK"],
  "mapped_project_keys": ["HONOR-CAMERA"],
  "unassigned_device_count": 12
}
```

`mapped_project_keys` 只含 USER 项目（`match_models` 或 `device.project_id`）。

静态路径 `/inventory/*` 必须注册在 `/{project_key}` 之前。

## 非目标

不复活 D5 派发门禁；不用前缀/正则自动建项目或自动映射。

## 关联

- Agent Note：[2026-08-20-project-registry-user-mapping.md](../notes/feature/2026-08-20-project-registry-user-mapping.md)
