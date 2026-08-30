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

- 凭据注入（CLI 类脚本）：`set -a && . ./.env.backend && set +a`——`load_repo_dotenv()` 只读仓库根 `.env`（生产不存在）。
- admin token：`/api/v1/auth/token` 回**扁平** OAuth2 体，取 `.access_token`（不是 `.data.access_token`）；用户名/口令必须是两个独立 `-F`。

## 1. 控制面后端更新与 DB 迁移（本机即生产控制面）

> 本机（127.0.0.1:8000）即是生产控制面：代码即跑在 **git 仓库根
> `/home/debian13/stability-test-platform`**（systemd `WorkingDirectory` 指向它），
> `#else` 上述清单里的 `/opt/stability-test-platform` 对**本机不成立**（已校准，
> 该目录不存在）。生产唯一 env 源是 `.env.backend`。
> 这是**既有人工 SOP**（`docs/production-minimum-deployment-checklist.md` §3.5），
> 不是 CI/CD 管道——每次上线都要人工执行。

1. **PR 合入** main（禁直推；auto-merge 由 AGENTS.md 门禁把关）。
2. **同步代码到生产目录**（本机=仓库根）：`git checkout main && git pull origin main`
   ```bash
   git checkout main && git pull origin main
   ./tools/dev/check-deploy-source.sh   # 部署源守卫：非 main / 有未提交改动 → 退出 1，先处理再继续
   ```
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
   ./tools/dev/check-deploy-source.sh   # 重启前再核一次（并发会话可能已把工作树切走）
   sudo systemctl restart stability-backend
   systemctl is-active stability-backend
   curl -s http://127.0.0.1:8000/health
   ```
5. **迁移窗口观察**（改 host.id 类迁移）：Agent 的 `agent:{host_id}` socketio room
   键随 id 变更，Agent 需按新 id 重建心跳/连接——预期一次重连，观测心跳波动；
   完事后对照 `GET /api/v1/hosts` 全部 ONLINE 且 `host.id == host.ip` 派生号一致。

**本迁移样例**：`k8l9m0n1o2p3_align_host_id_with_ip_after_subnet_migration.py`
（把 20 台遗留旧网段 `host.id`（形如 `10-0-8-*`）对齐到新网段点转横杠规则
`10-0-15-{ip末段}`，等价变换验证过 6 FK 表 + 2 快照列，详见 Agent Note
`docs/notes/bug-fix/2026-08-28-align-host-id-with-ip.md`）。

## 1.5 前端静态资源部署（✅ 2026-08-27 真机校准）

nginx 站点 `stability-platform` root 指向仓库内 `frontend/dist-prod`
（gitignore 排除、无 CI 自动发布）——部署 = 干净 worktree 构建 + 目录原子替换：

```bash
git fetch origin && git worktree add /tmp/stp-deploy origin/main   # 必先 fetch！
ln -sfn $PWD/frontend/node_modules /tmp/stp-deploy/frontend/node_modules
cd /tmp/stp-deploy/frontend && npm run build                        # 产物 → dist/
cd $REPO/frontend
cp -a /tmp/stp-deploy/frontend/dist ./dist-new                      # 先落到同一文件系统
mv dist-prod dist-prod.bak-$(date +%Y%m%d-%H%M)                     # bak 目录留原地回滚
mv dist-new dist-prod                                               # 同盘双 rename 原子切换
sudo systemctl reload nginx
```

**`/tmp` 是 tmpfs**：直接 `mv /tmp/stp-deploy/frontend/dist dist-prod` 是**跨文件系统
拷贝而非 rename**，会给 nginx 留一段「目录已就位但文件没拷完」的窗口。必须先
`cp -a` 到仓库同盘再做双 rename（`df --output=source /tmp <repo>` 一眼可辨）。

构建 env：仓库无 `.env.production`，`client.ts` 里 `baseURL` 硬编码 `/api/v1`，
所以裸 `npm run build` 即同源包，不需要设 `VITE_API_BASE_URL`。

验证清单（实测有效）：`curl -s http://127.0.0.1/<path> | grep -o 'assets/index-[^"]*\.js'`
与磁盘对比；浏览器强刷目标页核对新文案 testid。

**坑（2026-08-27 已踩）**：worktree 基于的本地 `origin/main` 引用可能落后于
远端——构建前必 fetch，并用
`git merge-base --is-ancestor <目标PR mergeCommit> origin/main`
校验目标 PR 确实在基线里（曾打出不含当日 PR 的旧包）。

## 2. 脚本目录 scan 与版本核验

scan 只有 HTTP 路由，没有 CLI 模块（`backend/scripts/` 下无 `scan.py`）：

```bash
curl -s -H "$AUTH" -X POST http://127.0.0.1:8000/api/v1/scripts/scan \
  | jq '.data | {created, skipped, conflicts, deactivated}'
```

- **何时需要**：`git diff <上次部署commit>..HEAD -- backend/agent/scripts/` 非空才需要跑；为空是 no-op。
- **scan 幂等**：seed 预建版本显示 created=0/skipped 是正常，勿误判未注册；conflicts 出现时先 `sha256sum` 比对磁盘 vs DB，再决定是否 `?force_rebaseline=true`（需无在途 PlanRun）。
- **版本号无 v 前缀**：DB `script.version` 存 `2.3.4` 形式（scan 剥 v）。

## 3. Agent 热更新（48 host）

**先单机 canary，再批量**——批量脚本没有 `--dry-run`，也没有单机参数：

```bash
# 1) canary：任取一台 ONLINE host，成功回 {"ok":true,...,"code_version":"<期望>"}
curl -s -H "$AUTH" -X POST http://127.0.0.1:8000/api/v1/hosts/<host_id>/hot-update | jq .
# 2) 校验该台 agent_code_sync_status 变 matched、心跳新鲜、设备数未掉，再批量：
set -a && . ./.env.backend && set +a
PYTHONPATH=. venv/bin/python -m backend.scripts.batch_hot_update --direct
```

- `--direct` 走 SSH（不受登录限流）；默认**跳过有活跃 job 的 host**，`--include-active` 才纳入。
- 期望 revision：`backend.services.host_updater.get_agent_code_version()`；逐台 `agent_code_revision` 应全量等于它、`agent_code_sync_status=matched`。
- **串行约 20s/台**（48 台 ≈ 16 min）且 stdout 是块缓冲——重定向到文件时日志会长时间为空，**进度看 DB/API 的 `agent_code_revision` 分布，别盯日志**。
- 推 `backend/agent/` 源码树（含 scripts/）→ 各 host `/opt/stability-test-agent/agent/`，自动重启 Agent。
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
| 本地 ref 陈旧 | worktree 基于 origin/main 前必 fetch；构建前用 `merge-base --is-ancestor <PR mergeCommit> origin/main` 校验 |

## 7. 校准记录

| 日期 | 校准了什么 | 来源 |
|------|-----------|------|
| 2026-08-27 | v0 骨架创建（全部 ⚠️待校对） | memory + runbook 预起草 |
| 2026-08-28 | 新增 §1 控制面后端更新与 DB 迁移路径（本机即生产控制面） | docs/production-minimum-deployment-checklist.md §3.5 + k8l9m0n1o2p3 迁移 |
| 2026-08-28 | 校准 §1：本机生产代码路径是仓库根 `/home/debian13/stability-test-platform`，非 `/opt/...`（后者不存在）；部署现已实操验证（k8l9m0n1o2p3 真机应用 + restart + 34 ONLINE） | 本机 pgrep/journalctl + 正式部署 |
| 2026-08-27 | 新增 §1.5 前端段：nginx root=仓库内 `frontend/dist-prod`、worktree 构建 + 双 rename 原子切换、浏览器/curl 双验证 | 登记簿 UI 批次（#476/#477）部署实操 |
| 2026-08-28 | 坑表补「本地 ref 陈旧」条目：目标 PR mergeCommit ∈ origin/main 用 `merge-base --is-ancestor` 校验（曾打出旧包） | 同上事故复盘 |
| 2026-08-30 | 四步全链路真机部署（r0s9t8u7v6w5 迁移 + 前端换包 + backend restart + 48 台热更新）后校准：§0 凭据与 token 取值路径（扁平 `.access_token`、双 `-F`）；§1.5 **`/tmp` 是 tmpfs、跨盘 `mv` 非原子**→ 改 `cp -a` 到同盘再双 rename，并记录无需 `VITE_API_BASE_URL`；§2 scan 无 CLI 模块、只有 HTTP 路由 + 「diff 为空则免跑」判据；§3 改为 canary→批量两段式，补 `--direct` 语义、串行 20s/台与 stdout 块缓冲（看 DB 不看日志）。§0/§2/§3 相应 ⚠️待校对 解除 | 本次部署实操 |
| 2026-08-30 | 新增部署源守卫步骤（§1 step 2/4 前各一行 `tools/dev/check-deploy-source.sh`）：共享工作树曾跑在未合入分支上被推上生产，重启前强制校验 HEAD==main 且工作区干净；已装 systemd unit 另加 `ExecStartPre=-` 兜底（失败仅记日志不中断） | 2026-08-30 事故复盘 + PR |
| （下次真实部署） | | |