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
│   └── systemd/stability-backend.service
├── postgres/docker-compose.yml
├── nginx/frontend-docker.conf
└── prometheus/
    ├── alerts-stability-platform.yml
    └── alertmanager.yml
```

根目录 `docker-compose.yml`、`Dockerfile.*` 用于 CI/容器化构建。

---

## 4. 网络与连通

| 文档 | 用途 |
|------|------|
| [`host-connectivity-verification.md`](../host-connectivity-verification.md) | SSH、挂载验证 |

---

## 5. 备份与脚本

| 路径 | 用途 |
|------|------|
| `scripts/pg_backup.sh` | PostgreSQL 备份 |
| `scripts/pg_restore_test.sh` | 恢复演练 |

---

## 6. 可观测性

- 指标：`GET /metrics`（生产建议 `STP_METRICS_AUTH_REQUIRED=1`）  
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
