# 设备批量归入型号范围与无型号阻断（#951）

Status: implemented
Class: bug-fix

## Decision

1. **后端**：`bulk_assign_project` 对无 `model` 的设备返回 422（含 id/serial），
   不再在响应里假填 `project_key`。
2. **前端**：`AssignProjectDialog` 展示将归入的型号列表，无型号设备列明并禁用确认；
   文案改为型号级语义。

涉及：`devices.py`、`AssignProjectDialog.tsx`、`DevicesPage.tsx`；
测试见 `test_project_routes.py`。

## Alternatives

- 恢复逐设备 `project_id` 列：与 ADR-0029 M3 冲突。
- 仅改文案不拦无型号：仍假成功。

## Verification

- `pytest backend/tests/api/test_project_routes.py::TestBulkAssignProject::test_device_without_model_422_no_fake_success -q`
- `cd frontend && npm run test -- --run src/pages/devices/DevicesPage.test.tsx`（若有归入用例）

## Revisit

型号级预览若需展示「将影响的全库同型号台数」，可加只读统计 API。
