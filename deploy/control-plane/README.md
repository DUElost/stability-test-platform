# 控制面（backend）启动与重启

本目录只记录**控制面进程**的启动/重启步骤。`deploy/` 下其它目录各管一件事：
`postgres/`（数据库）、`nginx/`、`prometheus/`（告警与抓取）、`sites/`。

> **为什么要有这份说明**：控制面**没有自动重载**（见下文「两个坑」）。改完后端代码、
> 或把检出推进到含修复的 `main` 之后，**必须重启进程**才会生效 —— 否则接口行为仍是旧代码，
> 而日志与库里都看不出异常，很容易误判为「修复没生效」。

## 前置

- 解释器与依赖：仓库根下的 `venv/`（如 `venv/bin/uvicorn`）。
- 配置：**`<repo>/.env.backend`**（`DATABASE_URL` 等）。缺失时应用启动即报错：
  `DATABASE_URL is not set and <repo>/.env.backend has no DATABASE_URL`。
- 端口：默认 `127.0.0.1:8000`（仅本机回环；对外由 nginx 代理）。

## 启动

```bash
cd <repo>            # 例如 /home/debian13/stability-test-platform
venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

长期运行时以后台方式起（nohup 或 systemd 均可）。**当前部署是手工拉起的前台/后台进程**
（`ps -ef | grep uvicorn` 可见 `PPID=1`），仓库内没有启动脚本。

## 重启（推进代码后必做）

```bash
# 1) 确认不在写操作高峰（重启窗口内接口会短暂不可用）
# 2) 记录当前进程
ps -ef | grep "[u]vicorn backend.main:app"
# 3) 停止后重新启动
kill <pid>
cd <repo> && venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 &
# 4) 复核（见「验证」）
```

> 自动合并队列与 CI 都跑在 GitHub 侧，**不受控制面重启影响**。

## 两个坑（都实际踩过）

1. **控制面没有自动重载**。把检出推进到新的 `main` 后，进程内仍是旧代码；
   表现为「代码里明明改了，接口返回没变」。
   实证：`git merge --ff-only origin/main` 推进到含修复的提交后，
   `GET /api/v1/plan-runs/{id}/watcher-summary` 仍返回旧结果，重启后才变化。
2. **主机 Agent 的代码跟「控制面检出」走，不跟远端分支走**。
   `backend/services/host_updater.py`：

   ```python
   _AGENT_SOURCE_DIR = Path(__file__).resolve().parent.parent / "agent"
   ```

   即 `POST /api/v1/hosts/{host_id}/hot-update` 打包的是**本检出**的 `backend/agent/`。
   给主机更新 Agent 代码的正确顺序是：**先把检出推进到含该改动的 `main`，再 hot-update**；
   否则 hot-update 会把旧代码推回主机（返回仍是 `ok: true` + `service restarted`，不易察觉）。

   > 检出若正被他人使用（有未提交改动），推进用 `git merge --ff-only origin/main`：
   > 它不会覆盖未提交改动，且无法快进时会直接拒绝。

## 验证

```bash
ps -ef | grep "[u]vicorn backend.main:app"

# 接口是否已换到新行为（示例：终态 run 的窗口末端应含迟到宽限）
curl -s -H "Authorization: Bearer <token>" \
  "http://127.0.0.1:8000/api/v1/plan-runs/<run_id>/watcher-summary?time_scope=all" | head -c 400
```

## 相关

- 主机 Agent 更新：`POST /api/v1/hosts/{host_id}/hot-update`（见「坑 2」）
- 告警规则与抓取：`deploy/prometheus/`
- 数据库：`deploy/postgres/README.md`
