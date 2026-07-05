# Linux-first 首迁总计划清单

适用目标：将当前稳定性测试管理平台的控制平面从历史 Windows / WSL 主路径收敛到 Linux-first 生产形态，并保留开发环境与生产环境的彻底隔离。

## A. 基线与边界确认

1. 确认 Git 基线已冻结并作为回退锚点保留：
   - 分支：`baseline/pre-linux-migration`
   - tag：`pre-linux-migration-2026-07-05`
2. 确认后续唯一主线是 `main`，所有 Linux-first 收敛继续在 `main` 推进。
3. 明确本次首迁目标：
   - 生产控制平面：Linux 宿主机部署，不使用根目录 Docker Compose 作为运行入口
   - 生产运行模型：宿主机单实例 backend(systemd) + Nginx + PostgreSQL + Redis
   - 开发环境：独立 Docker Compose 容器，与生产彻底隔离
4. 明确这次不做的事：
   - 不拆 scheduler / SAQ / backend 角色
   - 不上复杂生产容器编排
   - 不重构 host identity 模型
   - 不删除全部 auto-register 兼容

## B. 运行模型收口

5. 把仓库所有主文档统一到 Linux-first 口径：
   - `README.md`
   - `docs/production-minimum-deployment-checklist.md`
   - `docs/preprod-drill-runbook.md`
   - `docs/development/local-development.md`
   - `docs/operations/README.md`
   - `tools/ansible/README.md`
   - `docs/linux-agent-ansible-runbook.md`
6. 明确生产、预发布、开发三套模型：
   - 生产：Linux 宿主机常驻服务（systemd + Nginx）
   - 预发布：尽量贴近生产，同样按宿主机部署演练
   - 开发：Docker Compose 容器隔离环境
7. 把 Windows / WSL 标记为兼容入口，不再作为默认基线。

## C. 高风险口径收口

8. 固定 Agent 默认注册策略：
   - 控制平面先创建或确认目标 Host 记录
   - 生产 / 预发布默认固定 `HOST_ID`
   - `HOST_ID` 必须与后端 `hosts.id` 对齐
   - `AUTO_REGISTER_HOST=true` 仅保留实验 / 兼容用途
   - 生产 / 预发布 Agent 必须配置与后端一致的 `AGENT_SECRET`
9. 审计并限制 Windows-only 脚本链路：
   - 当前扫描 / API 仅接受 `.py` / `.sh`（`script_type=python|shell`）
   - `.bat/.cmd` 会被扫描忽略或 API 拒绝，不应再作为 Linux Agent 推荐路径
   - 保留旧兼容事实说明，但不再作为推荐路径
10. 明确生产指标暴露策略：
   - `STP_METRICS_AUTH_REQUIRED=1`
   - 如有需要再叠加 Nginx IP 白名单
11. 明确生产安全基线：
   - `ENV=production`
   - `AUTH_COOKIE_SECURE=1`
   - `AUTH_COOKIE_SAMESITE=lax|strict`
   - `STP_CSRF_ENABLED=1`
   - `AGENT_SECRET` / `JWT_SECRET_KEY` 禁止占位值

## D. 开发 / 生产彻底隔离

12. 规定生产与开发使用不同 checkout 目录。
13. 规定开发 Compose 不得占用生产默认端口。
14. 固定开发隔离端口策略，例如：
   - backend：`18000`
   - frontend：`15173`
   - postgres：`15432`
   - redis：`16379`
15. 开发环境显式使用 dev 专用路径：
   - `STP_NFS_ROOT`
   - `STP_AEE_NFS_ROOT`
   - `STP_AEE_LOCAL_ROOT`
   - `STP_SCRIPT_ROOT`
16. 禁止开发环境挂载生产 NFS / CIFS / AEE / 日志目录。
17. 开发 Agent 或联调环境不得默认指向生产控制平面。

## E. 控制平面部署标准化

18. 固定 Linux 控制平面部署目录：
   - `/opt/stability-test-platform`
19. 固定后端 systemd 运行方式：
   - 单实例
   - 监听 `127.0.0.1:8000`
   - 由 Nginx 对外代理
20. 固定前端生产构建方式：
   - `VITE_API_BASE_URL=` 为空
   - API / Socket.IO 走同源代理
21. 固定 Nginx 模型：
   - `/api/`
   - `/socket.io/`
   - 可选 legacy `/ws/`
   - 远端 Agent 的 `API_URL` 指向 Nginx 对外入口（如 `https://stp.example.com` 或 `http://<控制平面IP>`），不直接指向 backend 的 `127.0.0.1:8000`
22. 补齐 HTTPS 方案：
   - 证书路径
   - 80 -> 443 跳转
   - 生产同源 Cookie 生效

## F. 数据库与依赖准备

23. 确认 PostgreSQL 可用，并准备备份 / 恢复路径。
24. 确认 Redis 可用，且满足 SAQ 启动要求。
25. 明确数据库迁移策略：
   - 短期：部署时显式执行 `alembic upgrade head`
   - 中期：迁移从 service 启动钩子中独立出来
26. 明确外部依赖落点：
   - `STP_AEE_NFS_ROOT`
   - `STP_AEE_CIFS_ROOT`
   - `STP_DEDUP_SCAN_SCRIPT`
   - `STP_DEDUP_SCAN_PYTHON`
27. 确认 Linux 控制平面可访问相关共享存储和 scan 工具。

## G. 预发布环境落地

28. 搭建 1 套 Linux 预发布控制平面。
29. 生成并填写 `.env.backend`。
30. 安装依赖并执行数据库迁移。
31. 启动 backend systemd。
32. 构建 frontend 并配置 Nginx。
33. 验证基础健康：
   - `GET /`
   - `GET /health`
   - 登录
   - Dashboard
   - Socket.IO
   - Redis / SAQ ready
34. 验证 HTTPS 与生产 Cookie/CSRF guard 一致。
35. 预先创建或确认 canary Agent 对应的 Host 记录，并记录 `hosts.id`。

## H. 预发布主链路验证

36. 接入 1 台 Linux Agent。
37. 手工设置固定 `HOST_ID`，并写入与后端一致的 `AGENT_SECRET`。
38. 将 Agent `API_URL` 指向 Nginx 对外入口，而不是 backend loopback 端口。
39. 验证 Host ONLINE、Device ONLINE/BUSY。
40. 执行 `seed_and_smoke.py` 或等价人工全链路验证。
41. 验证关键链路：
   - `Plan -> PlanRun -> Job`
   - 心跳
   - 认领任务
   - 执行回传
   - 终态聚合
   - 设备锁释放
42. 验证 Job 状态符合当前状态机：
   - `PENDING -> RUNNING -> COMPLETED/FAILED/ABORTED`
   - 心跳丢失 / patrol stall：`RUNNING -> UNKNOWN -> RUNNING/FAILED`
43. 验证 watcher / dedup / merge 依赖可达性。

## I. 灰度切换

44. 先选 1-2 台 Agent 作为 canary。
45. 将 canary Agent 配置切到 Linux 控制平面：
   - `API_URL` 指向 Nginx 对外入口
   - `HOST_ID` 固定且与 `hosts.id` 对齐
   - `AUTO_REGISTER_HOST=false`
   - `AGENT_SECRET` 与后端一致
46. 持续观察：
   - 心跳
   - 设备状态
   - 任务流转
   - 热更新
   - 回收器行为
   - 日志链路
47. 若正常，再分批切换剩余 Agent。
48. 切换期间避免旧 WSL 控制平面与新 Linux 控制平面同时承担调度。

## J. 正式切换

49. 停止旧 Windows / WSL 控制平面入口。
50. 保留 Linux 控制平面为唯一运行面。
51. 观察一段稳定窗口后再开放业务使用。
52. 保留快速回退路径：
   - 旧配置
   - Agent `API_URL` 批量改回
   - Git 基线对照

## K. 迁移后立即优化

53. 把数据库迁移从 `ExecStartPre` 演进为独立部署步骤。
54. 增加控制平面 preflight 脚本，检查：
   - DB
   - Redis
   - secrets
   - TLS
   - NFS / CIFS
   - scan tool
55. 增加控制平面部署脚本或 Ansible 化入口。
56. 把开发 Compose 命名进一步显式化，比如 `docker-compose.dev.yml`。
57. 再做一轮文档归档，降低 Windows / WSL 历史噪音。

## 建议执行顺序

1. 先做 A-B-C-D
2. 再做 E-F
3. 然后做 G-H
4. 再做 I-J
5. 最后做 K

## 通过标准

当下面几项都满足时，可认为首迁完成：

- 生产默认口径已统一为 Linux-first
- 开发与生产已彻底隔离
- 预发布 Linux 控制平面已跑通主链 smoke
- 固定 `HOST_ID` 且配置 `AGENT_SECRET` 的 Agent 灰度通过
- 旧 Windows / WSL 控制平面已退为兼容或历史路径
- 回退锚点仍可用：`baseline/pre-linux-migration` / `pre-linux-migration-2026-07-05`
