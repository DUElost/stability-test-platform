# SSH 自动部署 - 任务列表

## 任务 ID: ssh-auto-deploy

## 任务列表

### Phase 1: SSH MCP 配置

#### T001: 配置 SSH MCP 服务器
- [x] **T001-1**: 安装 `@laomeifun/mcp-ssh` 包
- [x] **T001-2**: 在 Claude.json 中配置 MCP 服务器
- [x] **T001-3**: 配置 SSH config 主机连接信息
- [ ] **T001-4**: 测试 MCP SSH 连接

### Phase 2: 后端实现

#### T002: 创建部署状态模型
- [x] **T002-1**: 在 `backend/models/schemas.py` 添加 `Deployment` 类
- [x] **T002-2**: 添加状态枚举: PENDING, RUNNING, SUCCESS, FAILED
- [x] **T002-3**: 在 `backend/api/schemas.py` 添加 DeploymentOut 模型
- [ ] **T002-4**: 执行数据库迁移（如需要）

#### T003: 创建部署 API 端点
- [x] **T003-1**: 在 `backend/api/routes/` 创建 `deploy.py`
- [x] **T003-2**: 实现 `POST /api/v1/hosts/{host_id}/deploy` 端点
- [x] **T003-3**: 实现部署流程（连接→同步→安装→启动）
- [ ] **T003-4**: 实现 `GET /api/v1/hosts/{host_id}/deploy/status` 端点
- [x] **T003-5**: 实现 `GET /api/v1/hosts/{host_id}/deploy/history` 端点
- [x] **T003-6**: 在 `backend/api/routes/__init__.py` 注册路由

#### T004: 添加部署日志功能
- [x] **T004-1**: 在部署过程中记录详细日志
- [ ] **T004-2**: 支持流式日志输出
- [x] **T004-3**: 错误信息捕获和存储

### Phase 3: 前端实现

#### T005: 添加前端部署组件
- [x] **T005-1**: 在 `HostResourceCard.tsx` 集成部署按钮
- [x] **T005-2**: 实现部署触发按钮
- [x] **T005-3**: 实现部署状态显示
- [ ] **T005-4**: 实现部署历史列表

#### T006: 集成部署功能到主机页面
- [x] **T006-1**: 在 `HostsPage.tsx` 添加部署按钮
- [ ] **T006-2**: 在 `HostDetailPage.tsx` 添加部署功能
- [ ] **T006-3**: 添加部署日志查看对话框

### Phase 4: 测试与文档

#### T007: 测试
- [ ] **T007-1**: 集成测试部署 API
- [ ] **T007-2**: 手动测试完整部署流程

#### T008: 文档
- [ ] **T008-1**: 更新 API 文档
- [ ] **T008-2**: 更新用户文档

---

## 实现顺序

1. T001 (SSH MCP 配置)
2. T002 (数据模型)
3. T003-T004 (API 端点与日志)
4. T005-T006 (前端)
5. T007-T008 (测试与文档)

---

*Created: 2026-02-13*
