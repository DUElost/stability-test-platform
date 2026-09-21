---
name: test-env-self-check
description: 在本仓库运行后端/前端测试或排查环境异常前的自检清单（解释器、测试库指向、WSL ADB 端口、脚本根）。触发时机：准备跑 pytest/vitest、测试收集期报错、Agent 心跳正常但设备数为 0。
---

# 测试与环境自检

按序执行，任何一步红灯先修复再继续。权威约定见
`docs/development/testing.md` 与 `docs/development/environment-variables.md`
（本 skill 只列操作与检查命令，不复述理由）。

## 1. 解释器一致性

```bash
which python || echo "本机无 python 裸名——统一用 venv 的解释器"
```

- 所有测试/ruff 一律 `python -m` 形式调用（裸 `pytest` 会落到另一套解释器）。

## 2. 测试库指向（生产机红线）

```bash
case "${TEST_DATABASE_URL:-}" in
  *"/stp"|*"/stp_dev"*|*"@127.0.0.1:5432/stp"*)
    echo "BLOCK：TEST_DATABASE_URL 指向生产/开发容器库名"; exit 1;;
esac
unset TEST_DATABASE_URL   # 让 conftest 走 Docker testcontainers（推荐）
```

- 禁止把 `TEST_DATABASE_URL` 指到 `stp`（生产）或 `stp_dev`（compose 容器库名）。
- **无 SQLite 退路**：`conftest` 固定拉起 testcontainers Postgres（契约测试钉住不得
  存在 SQLite 回退路径，见 `docs/development/testing.md`）。

## 3. 快速短路验证（<40s）

```bash
python -m pytest backend/agent/tests/ -q          # Agent 侧自足套件
TESTING=1 JWT_SECRET_KEY=test-secret \
  python -m pytest backend/tests/api/<目标文件> -q  # 控制面单文件需 PG
```

## 4. WSL Agent 环境（仅涉及 Agent 联调时）

```bash
grep -q '^ANDROID_ADB_SERVER_PORT=' backend/agent/.env 2>/dev/null \
  && grep '^ANDROID_ADB_SERVER_PORT=' backend/agent/.env \
  || echo "WARN：未设 ANDROID_ADB_SERVER_PORT——WSL 下心跳正常但设备数为 0"
```

- WSL Agent 必须 `ANDROID_ADB_SERVER_PORT=5039`。
- `STP_SCRIPT_ROOT` 必须显式设置；扫描机≠运行机时另设
  `STP_SCRIPT_RUNTIME_ROOT`。

## 5. 更全的门禁矩阵

```bash
python scripts/run_gates.py check:quick    # 纯静态一轮
python scripts/run_gates.py check:gov      # 治理面专项
```

改完环境后若仍异常：查 `docs/development/local-development.md` 与
`backend/.env.example`，不要凭记忆猜键名。

## 踩坑守卫（负向约束）

- `TEST_DATABASE_URL` 一律不得指向 `stp`（生产）或 `stp_dev`（compose 容器库名）——
  §2 的短路检查不过就停；
- 测试与 ruff 一律 `python -m` 形式（裸 `pytest` 会落到另一套解释器，报错信号滞后）；
- **无 SQLite 退路**（`ALLOW_SQLITE_TESTS` 不存在于 fixture）；确需隔离库时 `unset
  TEST_DATABASE_URL` 走 testcontainers；
- WSL Agent 必须 `ANDROID_ADB_SERVER_PORT=5039`；Linux 生产 host 用默认 5037（误配
  表现为「心跳正常但设备数为 0」）；
- 环境异常时查 `docs/development/local-development.md` 与 `backend/.env.example`，
  **不要凭记忆猜键名**。

## 踩坑守卫：dev 栈与假 Agent 驱动（观测类）（#3034）

踩中后**不报错，只给错误观测**——测试者会把工具问题写成产品缺陷（先例 #2570）。
下列判据与「跑测试」半边并列；权威夹具见 `tools/dev/fake_agent.py`。

1. **`docker compose exec -T -e VAR` 只转发调用侧已存在的变量**  
   ```bash
   set -a; . ./.env.server; set +a
   docker compose exec -T -e AGENT_SECRET server \
     python /app/tools/dev/fake_agent.py heartbeat --count 1
   ```  
   误判表现：夹具打印「AGENT_SECRET 未设置，重连后放弃」→ 被读成 lifetime / 鉴权产品缺陷。

2. **`/proc` 扫描判存活会匹配到扫描者自身**（`sh -c` / `python -c` 的 cmdline 含被查串）  
   判据：匹配 cmdline 时排除自身 PID，或要求「令牌在 argv[0]/可执行路径」而非整行子串。  
   误判表现：误报「仍有 1 个残留夹具进程」。

3. **coordinator 心跳不喂执行心跳**（ADR-0026 §3）  
   `EXECUTING_STEP` 下 coordinator 心跳**刻意只持久化状态**；执行时钟靠 extend-batch /
   执行心跳。夹具 `serve` 的 `_http_beat` 只打 `/api/v1/heartbeat`，**从不发
   coordinator 心跳**。  
   ```bash
   # 喂执行/等待时钟（serve 不做这件事）：
   #   等待态用 PATROL_SLEEP（会刷 last_execution_heartbeat_at）
   #   EXECUTING_STEP 须由 extend-batch 提供执行心跳，别指望 coordinator 心跳
   ```  
   误判表现：job 恒落「未上报」、锚在 `started_at`，被 `running_timeout` 收掉 → 像
   「回收器杀了正常 job」。

4. **nginx entrypoint 重启窗口返回 `000` / `ERR_EMPTY_RESPONSE`**  
   判据：连续空响应时先看 compose / entrypoint 是否在重启，再判「升级打坏站点」。  
   误判表现：误报「升级把站点打坏了」。

5. **dev server 镜像无 `ps` / `pkill`**  
   清理只能用 Python 扫 `/proc` + `os.kill`（见夹具 docstring）；`pkill -f` 在镜像内
   不可用。  
   误判表现：宿主侧打印「已 terminate」为真，容器内夹具仍在替不存在的 host 心跳。
