# 桌面浏览器巡检环境

Status: implemented
Class: testing

## Decision

为桌面端视觉巡检提供固定入口和可重置的设备、主机数据。使用独立 Compose project，
复用开发 Compose、前后端 Dockerfile、空库迁移入口与假 Agent 心跳。应用源码来自指定
Git 提交的归档。前端与 API、Socket.IO 使用同源代理。预览端口仅绑定回环，
PostgreSQL、Redis、控制面留在测试容器网络中。测试账号由本工具生成。

```bash
./scripts/project_python.sh -m tools.dev.ui_review_env up --sha <commit>
./scripts/project_python.sh -m tools.dev.ui_review_env status
./scripts/project_python.sh -m tools.dev.ui_review_env reset
./scripts/project_python.sh -m tools.dev.ui_review_env down
```

从 [预览入口](http://stp-ui-review.localhost:15179/ui-review/) 查看 Git SHA、夹具版本、
账号角色和最近一次启动或重置的验收快照。页面提供实时后端健康检查链接。
`status` 同时检查快照和实时后端健康状态。测试口令存于启动命令返回的本地私有文件。
异机使用桌面浏览器时，将该主机的 15179 端口转发到桌面侧同端口。
Docker Compose 需支持覆盖标签 `!reset` 和 `!override`，本机使用 2.26.1。
`reset` 重建此测试库，恢复两台主机、60 台设备和两个角色，沿用测试账号口令并让旧会话失效。
`down` 删除该预览的容器和数据卷。

## Alternatives

沿用历史缺陷的 HTTP 替身，可快速比较几何命中。设备标签的持久化、真实登录和角色控制
需要通过控制面与隔离库验证。使用 Compose 覆盖文件保留同源代理和业务入口。

## Verification

在 `59ff23e048d8` 上使用 Chromium 1366 × 900 验证两种登录角色、设备分页、
2 台主机、60 台设备（58 在线、2 离线）及 Socket.IO WebSocket 连接。
每台主机上报 29 台在线设备和 30 台 USB 设备。设备心跳用摄氏度上报温度。
管理员通过设备页写入标签，API 返回 200。普通用户可读，写入 API 返回 403。
重置后恢复同一 SHA 的夹具数据，两种旧会话返回 401，测试标签消失。
本地 `check:quick` 通过，其中 schema 探针因未配置宿主机数据库连接而跳过。
在预览容器独立比对，测试库的迁移版本和镜像的 Alembic head 均为 `b8g5c0a1b2c3`。
前台桌面内置浏览器成功打开入口，显示预期 SHA 和验收快照。原 Luna 会话首次定时验收
已触发，浏览器清单初始化超时。单次复测直接创建内置浏览器标签页，成功打开入口并截图，
核对了 SHA 和设备、主机数量。定时复测覆盖到入口访问。

## Revisit

桌面端负责探索与取证。下一阶段在同一 Luna 会话中验证定时登录、页面探索和证据留存，
随后扩展 PlanRun、运行日志和报告夹具。
