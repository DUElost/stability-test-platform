# 2026-08-29 审查收口后部署 Runbook

> 适用窗口：#404 套件绑定、#514 双轨收口、#527 日志观测层、ADR-0031 AI 助手、
> `k8l9` host.id 对齐、flash v1.3.5/v1.3.6 等已合 main 后的**生产/预发布**一次升级。
> **自 2026-09-01 起**：若目标 tip 含 ADR-0029 v2.5 D10、ADR-0030 P2 #429、G15 脚本、
> OpenRouter UI 前端补丁，须额外执行 **§7 增量附录**。
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
git checkout main                         # 部署源必须是 main（并发会话可能已把工作树切走）
git fetch origin main
git log -1 --oneline origin/main          # 确认目标 commit
git pull --ff-only origin main            # 或已合并的 release 分支
./tools/dev/check-deploy-source.sh        # 部署源守卫：HEAD==main 且无未提交改动，退出码须为 0
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
./tools/dev/check-deploy-source.sh        # 重启前再核一次（§1.1 后并发会话可能又动了工作树）
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

Honor 刷机专项：[`honor-flash-runbook.md`](./honor-flash-runbook.md)（catalog 当前 **v1.3.10** per-model `latest.json`）。

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

---

## 7. ADR-0029 v2.5 / P2 / G15 增量附录（2026-09-01 起）

从 8 月窗口或更早 tip 升级到含 **ADR-0029 v2.5 D10**、**ADR-0030 P2 #429**、**G15 #462**、
OpenRouter UI 前端补丁的 main tip 时，§1–§6 仍适用，并追加下列项。

### 7.1 数据库（D10 M1→M4，Alembic head `j0k1l2m3n4o5`）

`alembic upgrade head` 一次性应用 M1–M4（顺序由 migration 链保证，勿手工拆步）：

| 步 | 内容 | 运维注意 |
|----|------|----------|
| M1 | `project_device_rule` → `project_model`；型号偏差一次重算 | 只读：迁移后 `project_model` 行数与型号映射符合预期 |
| M2 | 读路径改 JOIN（devices 筛选、DeviceOut、suite_binding、心跳新建设备） | API `GET /api/v1/devices?project_key=` 与详情页项目列仍可用 |
| M3 | 删 `device.project_id` / `device.project_pinned` | **不可逆**；禁止生产 `alembic downgrade` |
| M4 | `plan.project_id` 可空；GENERIC/LEGACY 哨兵出表；facet 减列 + jira 校验 | 详情页项目问题换为派生归属展示 |

迁移后复核：

```bash
cd "$CONTROL_DIR/backend"
../venv/bin/python -m alembic current    # 须 == j0k1l2m3n4o5（或更新 tip 的 head）
```

### 7.2 脚本 catalog（scan 后重点核对）

`POST /api/v1/scripts/scan` 后确认新版本已注册（conflicts 须为零）：

| 脚本 | 说明 |
|------|------|
| `flash_firmware` | **v1.3.10**（Honor per-model pin，见 honor-flash-runbook） |
| `sleep_test` / `gpu_test` / `powercycle_test` | G15 #462 三件套（toolkit 对齐后新版本） |
| `monkey_test` | v1.2.0（若 Plan 引用） |
| `mtbf_*` | 维持已绑定 Plan 的 pin 版本，勿原地改目录 |

### 7.3 前端重建（P2 套件页 + OpenRouter UI）

含 `/test-suites`、PlanRun `TestCaseResultsCard`、OpenRouter 设置页与布局补丁——**必须**干净 worktree 重建：

```bash
cd "$CONTROL_DIR/frontend"
npm ci
VITE_API_BASE_URL= npm run build    # 产出 dist-prod
# 原子切换 dist-prod → nginx root：见 control-plane-deploy SKILL §1.5
sudo systemctl reload nginx
```

浏览器抽检：`/test-suites` 可列出套件；MTBF PlanRun 详情页出现用例结果卡片。

### 7.4 Agent fleet（G15 / dedup 链）

- 全 fleet **重启** Agent（与 §2 相同；UnisocScanRunner / #463 展锐链在 Agent 侧）。
- 可选：确认 `STP_DEDUP_SCAN_*` 与 `STP_AEE_*` 路径经 hot-update 或 `.env` 与控制面一致。

### 7.5 增量完成勾选

- [ ] alembic current == 仓库 head（当前 `j0k1l2m3n4o5`）
- [ ] 设备列表/详情项目归属来自 `project_model` JOIN（无 `device.project_id` 依赖）
- [ ] `/test-suites` + PlanRun 用例结果 UI 可用
- [ ] G15 / flash v1.3.10 catalog 无 conflicts
- [ ] （MTBF）绑定 Plan 派发 + `TestCaseResultsCard` 有数据
