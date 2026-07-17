# 运维与部署文档索引

---

## 1. 上线前必读

| 文档 | 用途 |
|------|------|
| [`production-minimum-deployment-checklist.md`](../production-minimum-deployment-checklist.md) | 生产最小部署、env、HTTPS、冒烟 |
| [`preprod-drill-runbook.md`](../preprod-drill-runbook.md) | 预发布逐条验收 |
| [`acceptance/00-platform-smoke.md`](../acceptance/00-platform-smoke.md) | 验收 ID 与测试映射 |

---

## 2. Agent 部署

| 文档 | 用途 |
|------|------|
| [`backend/agent/DEPLOY.md`](../../backend/agent/DEPLOY.md) | 安装、目录、systemd、热更新 |
| [`agent-version-and-hot-update.md`](./agent-version-and-hot-update.md) | 协议版本门禁、code revision、滚动升级顺序 |
| [`linux-agent-ansible-runbook.md`](../linux-agent-ansible-runbook.md) | Ansible 批量 |
| [`wsl-linux-agent-setup.md`](../wsl-linux-agent-setup.md) | WSL 联调 |
| [`tools/ansible/README.md`](../../tools/ansible/README.md) | Playbook 说明 |

---

## 3. 控制平面部署模板

```
deploy/
├── control-plane/
│   ├── env/.env.backend.example
│   ├── nginx/stability-platform.conf
│   ├── nginx/stability-platform-https.conf
│   └── systemd/stability-backend.service
├── postgres/docker-compose.yml
├── nginx/frontend-docker.conf
└── prometheus/
    ├── alerts-stability-platform.yml
    └── alertmanager.yml
```

生产 / 预发布控制平面使用 **Linux 宿主机 systemd + Nginx** 部署；根目录 `docker-compose.yml`、`Dockerfile.*` 仅用于 **开发隔离 / CI / 容器化构建**，不是生产控制平面主入口。

---

## 4. 网络与连通

| 文档 | 用途 |
|------|------|
| [`wsl-linux-agent-setup.md`](../wsl-linux-agent-setup.md) | WSL Agent、ADB 与连通配置 |
| [`archive/host-connectivity-verification.md`](../archive/host-connectivity-verification.md) | 2026-01 SSH、挂载验证历史记录 |

---

## 5. 备份与脚本

| 路径 | 用途 |
|------|------|
| `scripts/pg_backup.sh` | PostgreSQL 备份 |
| `scripts/pg_restore_test.sh` | 恢复演练 |

---

## 6. 可观测性

- 指标：`GET /metrics`（生产建议保持 `STP_METRICS_AUTH_REQUIRED=1`，必要时叠加 Nginx IP 白名单）  
- Grafana：`docs/grafana/stability-platform-dashboard.json`  
- 告警草案：`deploy/prometheus/alerts-stability-platform.yml`（ADR-0011 待挂载）

---

## 7. 方案 C 运维补充

Agent 新增 env（部署时写入 `/opt/stability-test-agent/.env`）：

| 变量 | 说明 |
|------|------|
| `STP_AEE_LOCAL_ROOT` | HDD AEE 根 |
| `STP_AEE_CIFS_ROOT` | 15.4 CIFS 挂载 |

设计详述：[`design/2026-plan-c-storage-and-access.md`](../design/2026-plan-c-storage-and-access.md)

---

## 8. 数据库迁移

```bash
cd backend && python -m alembic upgrade head
```

生产发布前后必须执行；见 ADR-0008。

执行协议硬化 revision（`c8d9e0f1a2b3` 等）前先跑：

```bash
python -m backend.scripts.migration.preflight_execution_protocol
```

契约说明：[`../design/07-execution-protocol.md`](../design/07-execution-protocol.md)。
