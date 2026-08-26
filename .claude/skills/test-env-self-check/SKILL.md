---
name: test-env-self-check
description: 在本仓库运行后端/前端测试或排查环境异常前的自检清单（解释器、测试库指向、WSL ADB 端口、脚本根）。触发时机：准备跑 pytest/vitest、测试收集期报错、Agent 心跳正常但设备数为 0。
---

# 测试与环境自检

按序执行，任何一步红灯先修复再继续。权威约定见根 `CLAUDE.md` / `AGENTS.md`
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
- SQLite 兜底：`ALLOW_SQLITE_TESTS=1` 仅限少量用例，partial unique index 类
  用例会跳过（见 AGENTS.md §Test quirks）。

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
