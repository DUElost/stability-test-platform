# 2026-08-29 审查收口后部署 Runbook

> 适用窗口：#404 套件绑定、#514 双轨收口、#527 日志观测层、ADR-0031 AI 助手、
> `k8l9` host.id 对齐、flash v1.3.5/v1.3.6 等已合 main 后的**生产/预发布**一次升级。
> 只读核查脚本：`tools/dev/check-deploy-readiness.py`。

---

## 0. 变量（控制面主机上先 export）

```bash
export CONTROL_DIR="/home/debian13/stability-test-platform"   # 按实际路径改
export VENV="$CONTROL_DIR/venv/bin/python"
cd "$CONTROL_DIR"

# 当前仓库 alembic head（部署前在开发机确认，部署时复核）
export EXPECT_HEAD="$(cd backend && ../venv/bin/python -m alembic heads | awk '{print $1}')"
echo "expect alembic head: $EXPECT_HEAD"
```

凭据（不 echo 明文）：

```bash
set -a && source "$CONTROL_DIR/.env.backend" && set +a
export TOKEN=$(curl -s -H "X-Agent-Secret: $AGENT_SECRET" \
  -F "username=$STP_ADMIN_USER" -F "password=$STP_ADMIN_PASSWORD" \
  http://127.0.0.1:8000/api/v1/auth/token | jq -r .access_token)
export AUTH="Authorization: Bearer $TOKEN"
```

> `/auth/token` 回的是 OAuth2 扁平体 `{access_token, refresh_token, token_type}`，
> **不套 `ApiResponse` 信封**——取 `.access_token` 而非 `.data.access_token`。
> 用户名/口令是两个独立 `-F`：合成一个 `-F "username=a&password=b"` 会被当作
> 单个表单字段，登录必失败。

---

## 1. 控制面（顺序固定）

### 1.1 拉代码

```bash
cd "$CONTROL_DIR"
git fetch origin main
git log -1 --oneline origin/main          # 确认目标 commit
git pull --ff-only origin main            # 或已合并的 release 分支
```

### 1.2 依赖（requirements 有变时）

```bash
source venv/bin/activate
pip install -r backend/requirements.txt -q
```

### 1.3 数据库迁移 + 只读核查

```bash
cd "$CONTROL_DIR/backend"
../venv/bin/python -m alembic upgrade head
../venv/bin/python -m alembic current       # 应 == $EXPECT_HEAD

cd "$CONTROL_DIR"
./venv/bin/python tools/dev/check-deploy-readiness.py --expect-revision "$EXPECT_HEAD"
# 退出码须为 0；若报 mtbf-unbound → 见 §4 补绑后再派发
```

### 1.4 重启 backend + 脚本 catalog

```bash
sudo systemctl restart stability-backend
sleep 3
curl -sf http://127.0.0.1:8000/health | jq .

curl -s -H "$AUTH" -X POST http://127.0.0.1:8000/api/v1/scripts/scan | jq '.data | {created, skipped, conflicts, deactivated}'
# conflicts 非空 → 须新建脚本版本，禁止原地改已发布目录
```

### 1.5 前端（有 frontend 变更时）

nginx root 是仓库内 `frontend/dist-prod`（非 `dist`），部署 = 干净 worktree 构建 +
同盘双 rename 原子切换。完整步骤与坑见
[`.claude/skills/control-plane-deploy/SKILL.md`](../../.claude/skills/control-plane-deploy/SKILL.md) §1.5，
勿在生产目录里 `npm run build`（构建期 `dist-prod` 会处于半成品态）。

### 1.6 Preflight（可选但推荐）

```bash
cd "$CONTROL_DIR"
./venv/bin/python backend/scripts/preflight_control_plane.py \
  --backend http://127.0.0.1:8000 \
  --env-file "$CONTROL_DIR/.env.backend" \
  --origin "http://<你的前端 Origin>"
```

---

## 2. Agent fleet（20 台）

**必须重启**：#514 OperationScheduler fail-fast、claim cap 5、step-trace drain 均在 Agent 侧。

```bash
# 单台
ssh android@<ip> 'sudo systemctl restart stability-test-agent && sudo systemctl is-active stability-test-agent'

# 批量（hosts.ini 在 AGENTS.md §Production access）
ansible android -i /home/debian13/hosts.ini -m systemd -a 'name=stability-test-agent state=restarted' -b
```

每台抽样核对（`k8l9` 迁移后）：

```bash
# 控制面
curl -s -H "$AUTH" http://127.0.0.1:8000/api/v1/hosts | jq '.data[] | {id, ip_address, status}'

# Agent 上
grep ^HOST_ID= /opt/stability-test-agent/.env
# 须与 hosts API 返回的 id 一致
```

可选清理（非阻塞）：

```bash
grep STP_MTBF_EXPECTED_TESTPOINT_COUNT /opt/stability-test-agent/.env || true
# PR-D 已退役 hot-update 下发；绑定 Run 以 plan.suite_id 注入为准
```

---

## 3. 冒烟（§5 主链路）

```bash
cd "$CONTROL_DIR"
export STP_ADMIN_PASSWORD='...'   # 勿写入 shell history 持久文件

./venv/bin/python backend/scripts/seed_and_smoke.py \
  --backend http://127.0.0.1:8000 \
  --target-host-id <hosts.id> \
  --device-id <device.id> \
  --no-hot-update \
  --timeout 600
```

MTBF 专项（若在跑）：[`acceptance/2026-08-suite-binding-mtbf-signoff.md`](../acceptance/2026-08-suite-binding-mtbf-signoff.md) R1–R4 抽检。

Honor 刷机专项：[`honor-flash-runbook.md`](./honor-flash-runbook.md)（v1.3.6 per-model `latest.json`）。

---

## 4. 常见问题

| 现象 | 处理 |
|------|------|
| `SUITE_BINDING_REQUIRED` 派发被拒 | `check-deploy-readiness.py` 列出的 Plan 在编辑器绑定 `suite_name` |
| `script_verify_failed` / conflicts | 磁盘脚本与 DB sha 不一致 → 新建版本或 admin `force_rebaseline`（有在途 Run 时 409） |
| Agent 心跳正常、claim 为 0 | `HOST_ID` 与 DB `hosts.id` 不一致 |
| Job 立刻 `operation_scheduler_required` | Agent 未重启到 #514 后代码 |
| AI 助手「未配置」 | 设 `AI_ASSISTANT_FERNET_KEY` + 管理面写入 LLM 三元组 |

---

## 5. 回滚策略（最小）

1. `git checkout <上一稳定 commit>` + backend 重启（**不**自动 downgrade DB——Alembic downgrade 须单独评估）
2. Agent：按 [`2026-08-27-agent-rollback-readiness-audit.md`](./2026-08-27-agent-rollback-readiness-audit.md) 上一 code revision hot-update
3. 若 migration 已执行且不可回退：forward-fix，禁止在生产试 `alembic downgrade`

---

## 6. 完成勾选

- [ ] alembic current == `$EXPECT_HEAD`
- [ ] `check-deploy-readiness.py` 退出 0
- [ ] backend / nginx active
- [ ] scripts/scan 无未处理 conflicts
- [ ] 20 台 Agent restarted + HOST_ID 抽样 OK
- [ ] seed_and_smoke 或等价主链路通过
- [ ] （可选）MTBF / flash 专项抽检
