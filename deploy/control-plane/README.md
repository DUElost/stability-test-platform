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

## 启动：systemd 常驻（默认路径）

单元模板就在本目录 `systemd/stability-backend.service`，由站点安装器渲染到
`/etc/systemd/system/`（渲染面见 `tools/site_config/plan.py`；同目录另有 `-migrate` /
`-nomigrate` 两个变体）。启动用：

```bash
sudo systemctl start stability-backend
journalctl -u stability-backend -f
```

单元自带三道 `ExecStartPre`，**手工起进程一道都不会跑**：

| 前置 | 是否阻断 | 拦的是谁 |
|---|---|---|
| `alembic upgrade head` | 阻断 | 库停在旧 schema |
| `tools/dev/check_alembic_at_head.py` | **硬**阻断（#1882） | 代码超前于库——带病 500 对外服务 |
| `tools/dev/check-deploy-source.sh` | 带 `-` 前缀＝不阻断 | 部署源不是 `main` / 工作区脏（runbook 在部署动作前显式跑它） |

并有 `StartLimitIntervalSec=300` / `StartLimitBurst=3`（#2058）：守卫一红会停在稳定
`failed`，而不是 `Restart=always` 每 5s 重跑「upgrade + 守卫」把真因埋在 `activating` 里。

手工 `venv/bin/uvicorn …` 只用于**没有该单元的开发机**（`cd <repo> && venv/bin/uvicorn
backend.main:app --host 127.0.0.1 --port 8000`）；在生产机上它等于绕过上面整张表。

> 沿革：本文件曾自述「当前部署是手工拉起的前台/后台进程，仓库内没有启动脚本」——已过期
> （#2997）。仓库里既有单元模板也有渲染器，两份 runbook
> （`docs/operations/2026-08-29-post-review-deploy-runbook.md`、`docs/preprod-drill-runbook.md`）
> 用的都是 `systemctl`。

## 重启（推进代码后必做）

```bash
# 1) 确认不在写操作高峰（重启窗口内接口会短暂不可用）
sudo systemctl restart stability-backend
# 2) 复核（见「验证」）
systemctl is-active stability-backend && journalctl -u stability-backend -n 50
```

**不要再用 `kill <pid>` + `nohup uvicorn … &`**：那条路径同时绕过 #1882 的 schema 对齐硬门禁
与 #2058 的 start-limit 保护——正是本文件「两个坑」想避免的那类事故。旧文档里
「`PPID=1` 说明它是后台进程」只是 nohup 的副作用，不是它看起来正常的理由。

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
