# Phase F-3 Agent 切换验收清单（/agent/jobs 新链路）

适用范围：`legacy-model-migration` 的 Phase F-3（Linux Agent 全量切到 `/api/v1/agent/jobs/*`）。

## 1. 目标

- 所有 Linux Agent 仅访问新端点：
  - `GET /api/v1/agent/jobs/pending`
  - `POST /api/v1/agent/jobs/{job_id}/heartbeat`
  - `POST /api/v1/agent/jobs/{job_id}/complete`
  - `POST /api/v1/agent/jobs/{job_id}/extend_lock`
  - `POST /api/v1/agent/jobs/{job_id}/steps/{step_id}/status`
- 控制平面访问日志中不再出现 `/api/v1/agent/runs/*`

## 2. 部署前检查（每台 Agent 主机）

```bash
sudo systemctl status stability-test-agent --no-pager
sudo grep -E '^(API_URL|HOST_ID)=' /opt/stability-test-agent/.env
```

检查要点：
- `HOST_ID` 必须为非空字符串，且在控制平面全局唯一。
- `API_URL` 指向当前控制平面地址。

## 3. 主机侧滚动发布（每台 Agent）

```bash
cd /opt/stability-test-platform
git pull
sudo systemctl restart stability-test-agent
sudo systemctl status stability-test-agent --no-pager
sudo journalctl -u stability-test-agent -n 200 --no-pager
```

日志应出现 `/api/v1/agent/jobs/` 请求，不应出现 `/api/v1/agent/runs/`。

## 4. 控制平面验证

### 4.1 单机连通性（按 host_id 抽检）

```bash
curl -s "http://127.0.0.1:8000/api/v1/agent/jobs/pending?host_id=<HOST_ID>&limit=1"
```

预期：
- HTTP 200
- 返回结构包含 `{"data": [...], "error": null}`

### 4.2 旧端点零流量验证（至少连续 5 分钟）

Nginx access log（如启用）：
```bash
sudo grep "/api/v1/agent/runs/" /var/log/nginx/access.log | tail -n 20
```

后端日志（systemd）：
```bash
sudo journalctl -u stability-backend --since "5 min ago" --no-pager | grep "/api/v1/agent/runs/"
```

预期：两条命令都无输出。

### 4.3 心跳活性验证（数据库）

```sql
SELECT id, status, last_heartbeat
FROM host
ORDER BY last_heartbeat DESC
LIMIT 20;
```

预期：
- 目标主机 `status=ONLINE`
- `last_heartbeat` 在最近 1 分钟内持续刷新

## 5. 回滚条件与动作

触发条件（任一满足即回滚）：
- Agent 连续 2 分钟无法拉取任务（`jobs/pending` 非 200 或超时）
- 大量 4xx/5xx 出现在 `/api/v1/agent/jobs/*`

回滚动作：
1. 回退 Agent 到上一可用版本并重启服务
2. 暂时恢复旧流量观测窗口（保留后端日志）
3. 记录失败主机列表与错误日志，排障后再滚动发布

## 6. 验收结论模板

```text
Phase F-3 验收时间：
验收人：
总主机数：
已切换主机数：
旧端点流量：0（持续 N 分钟）
异常主机：
结论：通过 / 不通过
```
