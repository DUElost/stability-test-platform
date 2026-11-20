# #205 返修：file-server 响应 schema 丢字段

Status: implemented
Class: bug-fix

关联：[#205](https://github.com/DUElost/stability-test-platform/issues/205)、
合入 #4084（服务层与前端已齐）、生产验收评论
[7044843617](https://github.com/DUElost/stability-test-platform/issues/205#issuecomment-7044843617)。

## Decision

生产 `GET /api/v1/stats/file-server` 经 `FileServerOverview.model_validate` 序列化时，
服务层已生成的 `control_plane.nfs`、`monitoring.unmonitored_reason`、
`nfs.scope` / `export_path` / `applicable` 被响应模型剥离。根因是
`backend/api/schemas/file_server.py` 未同步 #4084 新增字段，不是服务或前端回归。

本单只补齐 Pydantic 响应模型并加经真实路由的序列化回归：

- `NodeMonitoring.unmonitored_reason: Optional[str] = None`
- `FileServerNfs.service_ready` → `Optional[bool]`；新增 `export_path` / `scope` /
  `applicable`
- `ControlPlanePanel.nfs: Optional[FileServerNfs] = None`
- API 测试：分源 CIFS + 无 `STP_STORAGE_NODE_JOB`，走真实
  `collect_file_server_overview` + `GET /api/v1/stats/file-server`，断言上述字段保留；
  旧 payload（无新字段）仍由既有用例覆盖兼容。

合入后仅需重部署后端；无需改前端与 DB 迁移。

## Alternatives

- **在路由层 `model_dump` 前手工 merge 额外字段**：绕过 schema 权威，前端契约仍失明——否决。
- **整页改 `extra='allow'`**：掩盖其他漏同步，失去契约守卫——否决。
- **重改服务/前端**：生产验收已确认服务层与 UI 类型齐备，问题仅在响应模型——超出本单。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/tests/api/test_stats.py backend/tests/services/test_file_server_monitor.py -q`
  → **49 passed**
- `./scripts/project_python.sh scripts/run_pytest.py tests/test_api_response_shape_contract.py -q`
  → **15 passed**
- `./scripts/project_python.sh scripts/run_gates.py check:quick`
  → **16 gates OK**

## Revisit

- 生产重部署后端后，复验 `GET /api/v1/stats/file-server` 是否含 `control_plane.nfs`
  与 `storage_server.monitoring.unmonitored_reason`，以及 `/storage` 中心存储页是否显示
  「外部共享存储」而非红色「Prometheus 不可用」。
- 若后续再扩 file-server 面板字段，优先让 API 序列化测试走真实 collect，避免假字典
  再次绕过 schema。
