---
name: control-plane-deploy
description: 生产控制面部署与 Agent 热更新操作 SOP。触发时机：部署控制面到 127.0.0.1:8000、批量热更新 Agent 到 34 台 host、scan 后脚本版本核验、部署后版本一致性确认。
---

# 控制面部署与 Agent 热更新 SOP（v0 骨架）

> **状态：v0 骨架——生产步骤未真机校对（2026-08-27 预起草，素材来自运维 memory 与
> runbook）。本 skill 的每一条 `⚠️待校对` 步骤在**下一次真实部署时**以当天实况
> 校准后移除标记；在此之前按「未验证假设」对待，不当作已校准步骤执行。
> 权威细节：`docs/operations/agent-version-and-hot-update.md`、
> `docs/operations/2026-08-27-agent-rollback-readiness-audit.md`。

## 0. 前置确认（只读）

```bash
systemctl is-active stability-backend     # 控制面服务态
curl -s http://127.0.0.1:8000/health      # health 路由（非 /api/v1/health）
```

- 凭据注入（CLI 类脚本）：`set -a && . ./.env.backend && set +a`——`load_repo_dotenv()` 只读仓库根 `.env`（生产不存在）。⚠️待校对

## 1. 控制面后端更新与 DB 迁移（本机即生产控制面）

> 本机（127.0.0.1:8000）即是生产控制面：代码即跑在 **git 仓库根
> `/home/debian13/stability-test-platform`**（systemd `WorkingDirectory` 指向它），
> `#else` 上述清单里的 `/opt/stability-test-platform` 对**本机不成立**（已校准，
> 该目录不存在）。生产唯一 env 源是 `.env.backend`。
> 这是**既有人工 SOP**（`docs/production-minimum-deployment-checklist.md` §3.5），
> 不是 CI/CD 管道——每次上线都要人工执行。

1. **PR 合入** main（禁直推；auto-merge 由 AGENTS.md 门禁把关）。
2. **同步代码到生产目录**（本机=仓库根）：`git checkout main && git pull origin main`
   ——部署源就是工作树，务必保持在 main。
3. **应用 DB 迁移**（禁止直连生产库手动 `alembic upgrade`：
   AGENTS.md「迁移试验禁对生产库执行」；迁移必须先经 PR + 验证）：
   ```bash
   cd /home/debian13/stability-test-platform/backend
   ../venv/bin/python -m alembic upgrade head
   ```
   部署后回查：`venv/bin/python -m alembic current` 应等于目标 revision。
4. **重启服务**（迁移若已解耦为 `-migrate` oneshot，则只重启常驻服务）：
   ```bash
   sudo systemctl restart stability-backend
   systemctl is-active stability-backend
   curl -s http://127.0.0.1:8000/health
   ```
5. **迁移窗口观察**（改 host.id 类迁移）：Agent 的 `agent:{host_id}` socketio room
   键随 id 变更，Agent 需按新 id 重建心跳/连接——预期一次重连，观测心跳波动；
   完事后对照 `GET /api/v1/hosts` 全部 ONLINE 且 `host.id == host.ip` 派生号一致。

**本迁移样例**：`k8l9m0n1o2p3_align_host_id_with_ip_after_subnet_migration.py`
（把 20 台遗留 `172-21-8/9-*` 的 `host.id` 对齐到 `172-21-15-{ip末段}`，
等价变换验证过 6 FK 表 + 2 快照列，详见 Agent Note
`docs/notes/bug-fix/2026-08-28-align-host-id-with-ip.md`）。

## 2. 脚本目录 scan 与版本核验

```bash
venv/bin/python -m backend.scripts.scan   # ⚠️待校对：实际命令名/路由
```

- **scan 幂等**：seed 预建版本显示 created=0/skipped 是正常，勿误判未注册；conflicts 出现时先 `sha256sum` 比对磁盘 vs DB，再决定是否 `?force_rebaseline=true`（需无在途 PlanRun）。⚠️待校对
- **版本号无 v 前缀**：DB `script.version` 存 `2.3.4` 形式（scan 剥 v）。

## 3. Agent 热更新（34 host）

```bash
venv/bin/python -m backend.scripts.batch_hot_update   # ⚠️待校对：参数形态
```

- 推 `backend/agent/` 源码树（含 scripts/）→ 34 台 `/opt/stability-test-agent/agent/scripts/`，自动重启 Agent。⚠️待校对
- 完成后逐台 `agent_code_revision` 应全量等于期望 commit（回滚就绪审计已验 34/34 读路径）。
- **热更新会抹掉 host 上手工放入 agent 树的文件**（如临时 .so）——部署前确认无此类残留。⚠️待校对
- **不要**在 hot-update 未返回成功时抢 `reload_config`（曾致 event_uploader 读到旧 flag）。

## 4. 版本门控顺序（强制）

1. 先推 Agent → 2. 确认 `agent_code_sync_status` 多 matched → 3. 再设 `STP_AGENT_MIN_VERSION`。
   反序 → 旧 Agent claim 426、PENDING 积压。

## 5. 回滚路径（引用审计结论）

- 回滚 = 控制面 agent 源码树切旧 revision + 重发热更新（两步非原子，无一键入口）。
- 前置检查三要素见 `docs/operations/2026-08-27-agent-rollback-readiness-audit.md`。
- 真实回滚完成后在 runbook §5 表格追加记录行。

## 6. 已知坑速查

| 坑 | 处置 |
|----|------|
| SP Flash Tool host 缺库 | `sudo -n apt-get install -y --no-install-recommends libice6 libsm6 libxrender1 libfontconfig1 libglib2.0-0` ⚠️待校对 |
| MLD 拼写 | `getprop ro.product.model` 返回 `MLD-LX3`（连字符），`adb devices` 是下划线——以 getprop 为准 |
| 部署后代码 | 部署验证完成后按仓库流程走 PR 合入，不直推 main |

## 7. 校准记录

| 日期 | 校准了什么 | 来源 |
|------|-----------|------|
| 2026-08-27 | v0 骨架创建（全部 ⚠️待校对） | memory + runbook 预起草 |
| 2026-08-28 | 新增 §1 控制面后端更新与 DB 迁移路径（本机即生产控制面） | docs/production-minimum-deployment-checklist.md §3.5 + k8l9m0n1o2p3 迁移 |
| 2026-08-28 | 校准 §1：本机生产代码路径是仓库根 `/home/debian13/stability-test-platform`，非 `/opt/...`（后者不存在）；部署现已实操验证（k8l9m0n1o2p3 真机应用 + restart + 34 ONLINE） | 本机 pgrep/journalctl + 正式部署 |
| （下次真实部署） | | |
