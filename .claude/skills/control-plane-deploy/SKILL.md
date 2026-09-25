---
name: control-plane-deploy
description: 生产控制面部署与 Agent 热更新操作 SOP。触发时机：部署控制面到 127.0.0.1:8000、批量热更新 Agent fleet、scan 后脚本版本核验、部署后版本一致性确认。
---

# 控制面部署与 Agent 热更新 SOP

> **状态：全文已真机校准，无 `⚠️待校对` 条目**（最近一次端到端：2026-09-22；两处历史遗留的
> 待校对项——§6 的 SP Flash Tool 依赖行与 §3 的带外资源身份——已于同日实机验证并收口，逐条见
> §7 校准记录）。**校准有保质期**：任何一条若与现场不符，请按「实跑→改文→同 PR 追加 §7 行」
> 的顺序更新——校准来自实跑，不来自推演。
> 权威细节：`docs/operations/agent-version-and-hot-update.md`、
> `docs/operations/2026-08-27-agent-rollback-readiness-audit.md`。

## 0. 前置确认（只读）

```bash
systemctl is-active stability-backend     # 控制面服务态
curl -s http://127.0.0.1:8000/health      # health 路由（非 /api/v1/health）
```

- 凭据注入（CLI 类脚本）：`set -a && . ./.env.backend && set +a`——`load_repo_dotenv()` 只读仓库根 `.env`（生产不存在）。
  用户名/口令变量是 `$STP_ADMIN_USER`（= `stp-admin`）与 `$STP_ADMIN_PASSWORD`——**不是 `admin`**。
- admin token（2026-09-15 实测校准）：`/api/v1/auth/token` 回**扁平** OAuth2 体，取 `.access_token`（不是 `.data.access_token`）；
  请求体是 `application/x-www-form-urlencoded`（用 `--data-urlencode`，不是 `-F`），且 `/api/v1/*` 非安全方法要过
  CSRF 中间件（`backend/core/csrf.py`：无 Bearer / `X-Agent-Secret` 时要求 `Origin`/`Referer` 在 `CORS_ORIGINS` 白名单）——
  取 token 这一步必须带 `-H 'Origin: http://127.0.0.1'`，否则 403 `CSRF check failed`；拿到 token 后其余调用用 `Authorization: Bearer` 即可：

  ```bash
  set -a && . ./.env.backend && set +a
  TOK=$(curl -s -X POST http://127.0.0.1:8000/api/v1/auth/token \
          -H 'Origin: http://127.0.0.1' \
          --data-urlencode "username=${STP_ADMIN_USER}" \
          --data-urlencode "password=${STP_ADMIN_PASSWORD}" | jq -r '.access_token')
  ```
- 响应形状不统一（脚本断言要 unwrap）：`/api/v1/hosts` **不分页时是裸数组**（分页体另说）；
  `/hosts/{id}/hot-update` 是**裸对象**——该路由没有 `response_model`（`backend/api/routes/hosts.py`），
  实测返回顶层 `{"ok":true,…}`，**没有** `{data: …}` 包装（2026-09-22 修正：旧稿写成 `{data: …}`，
  会让 `jq '.data.ok'` 恒为 null）；`/scripts/scan`、`/script-presence/*` 这些才是 `{data: …}`。
  可参考已跑通的抽验脚本（token → 目标 host → force 热更新 → 断言 `priv_mode`）：`/tmp/stp-acc/verifyD_sample.sh`（临时产物，机器重建即失）。

## 1. 控制面后端更新与 DB 迁移（本机即生产控制面，**bundle 发布根形态**）

> 本机（127.0.0.1:8000）即是生产控制面。ADR-0051 Phase 1（2026-09-23 实切）起：unit 的
> `WorkingDirectory`/`EnvironmentFile`/`ExecStart`/日志全部指向 **`/home/debian13/stp-releases/current`**
> （符号链接 → `stp-releases/<rev>/` 的 bundle 树），与开发工作区（git 检出）物理分离；仓根不再是运行路径。
> env 单源：发布根 `.env.backend` 是**指向仓根同名文件的 symlink**（改 env 只改仓根，重启即生效）。
> 仍是**人工 SOP**，不是 CI/CD 管道。

1. **PR 合入** main（禁直推；auto-merge 由 AGENTS.md 门禁把关）。
2. **构建 bundle**（在**已拉到目标 main 的检出**里跑；构建输入 = 当前工作树，先 `git pull` +
   `./tools/dev/check-deploy-source.sh` 确认在 main 且干净——守卫管的是**构建源**，不再是部署源）：
   ```bash
   git pull --ff-only origin main && ./tools/dev/check-deploy-source.sh
   venv/bin/python tools/release/build_bundle.py --repo-root . --out /home/debian13/stp-releases/<rev>
   ```
   `release-manifest.json` 的 `product.version` / 两个 ADR-0040 digest 即部署内容地址；构建机本地态由
   `find_forbidden_bundle_entries` fail-closed（#2269/#3112 在 bundle 形态的替身）。
3. **物料与 venv**（首建/新 rev）：发布根需 `venv/`（`python3 -m venv venv && venv/bin/pip install -r backend/requirements.txt`）、
   `logs/`、`.env.backend`（**symlink 到仓根**，勿复制成第二源）；`tools/ansible/inventory.ini` 随 bundle 携带
   （gitignored，构建机没有它 → 热更新 SSH 凭据回退会静默消失）。
4. **切 current 并重启**（回滚 = 把 current 指回旧 rev 或仓根 unit 备份 `*.bak-20260923-phase1`）：
   ```bash
   ln -sfn /home/debian13/stp-releases/<rev> /home/debian13/stp-releases/current.tmp && mv -Tf /home/debian13/stp-releases/current.tmp /home/debian13/stp-releases/current
   sudo systemctl restart stability-backend   # unit 路径走 current，无需 daemon-reload
   curl -s http://127.0.0.1:8000/health
   ```
   DB 迁移由 unit 的硬 `ExecStartPre`（`alembic upgrade head` + `check_alembic_at_head.py`）在启动时执行——
   禁止绕过 unit 直连生产库手动 upgrade（AGENTS.md 红线不变）。
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
ls -dt dist-prod.bak-* 2>/dev/null | tail -n +3 | xargs -r rm -rf   # 只保留最近 2 个 bak
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
- **scan 只写控制面注册表**：它不往主机送文件。新版本要**对主机生效**必须再跑 §3（见
  `script-version-lifecycle` §A 第 5 步）——2026-09-22 实测 `fill_storage` v1.1.1 已 active
  而 47 台主机无此文件，就是漏了这步。
- **scan 幂等**：seed 预建版本显示 created=0/skipped 是正常，勿误判未注册；conflicts 出现时先 `sha256sum` 比对磁盘 vs DB，再决定是否 `?force_rebaseline=true`（需无在途 PlanRun）。
- **版本号无 v 前缀**：DB `script.version` 存 `2.3.4` 形式（scan 剥 v）。
- **ADR-0051 Phase 3 起 scan 的输入 = `tool_manifest.json` + 站点 `packages/`**（不再扫描检出目录）：响应新增 `package_missing`（未发布）/ `unregistered_active`（活跃行不在 manifest，只报告）；退役 = manifest `retired:true` + scan；`agent-code` 载荷不再含 `scripts/`（主机上的旧目录随热更新清掉，脚本从 `tools_cache` 执行）。
- **ADR-0051 Phase 2a 起 scan 还回填 `package_sha256`**（✅2026-09-23 实跑）：响应多两键 `package_backfilled` / `package_conflicts`，来源是仓根 `tool_manifest.json`。首次回填期望 `package_backfilled=210`、`package_conflicts=[]`；之后只读证明 `DATABASE_URL=… venv/bin/python -m backend.scripts.check_script_package_equivalence` 应 `EQUIVALENCE OK … backfilled=<行数>`。新增脚本版本目录后先 `venv/bin/python tools/dev/check_script_packages.py --register`（否则 tool-manifest 门禁红），合入后 `--publish --packages-root /mnt/stp-aee/packages`（把 tar.gz 与 manifest 副本发到站点包源，Agent 从这里拉）。

## 3. Agent fleet 热更新

**先单机 canary，再批量**——批量脚本没有 `--dry-run`，也没有单机参数：

```bash
# 1) canary：任取一台 ONLINE host，成功回 {"ok":true,...,"code_version":"<期望>"}
curl -s -H "$AUTH" -X POST http://127.0.0.1:8000/api/v1/hosts/<host_id>/hot-update | jq .
# 2) 校验该台 agent_code_sync_status 变 matched、心跳新鲜、设备数未掉，再批量：
set -a && . ./.env.backend && set +a
PYTHONPATH=. venv/bin/python -m backend.scripts.batch_hot_update --direct
```

- `--direct` 走 SSH（不受登录限流）；默认**跳过有活跃 job 的 host**，`--include-active` 才纳入。
- **判据是 digest，不是 revision**（ADR-0040 v1.1；`docs/operations/agent-version-and-hot-update.md` §1）：
  `agent_code_sync_status` 只由 code artifact digest 决定，`agent_code_revision` /
  `expected_code_revision` 是**溯源文本**（`expected` 取仓库 HEAD，任何不动 `backend/agent/**`
  的提交都会让它前进）。期望 revision 仍可用
  `backend.services.host_updater.get_agent_code_version()` 取值，但**别拿它判等**。
- **desired digest = 现算发布根 `backend/agent/` 树**（Phase 1 起不再读开发工作树——并行会话碰
  仓根不会再让全 fleet drift，2026-09-22 那类事故形态（工作树临时文件 → 全 fleet drift）已消除；
  drift 只剩「发布根被手改」或「bundle 构建时工作树不干净」）。见到整片 drift：先 `cmp -r` 发布根
  与 `stp-releases/<rev>` 原树，再查最近一次构建源。
- **自愈路径**：收敛成功后远端脚本执行 `write-digest`，写入的是**控制面现算的 desired
  digest**（不是主机自算），所以一次成功推送必然把该台置 `matched`（`host_updater.py:380-384`）。
- **实测 ~3s/台**（2026-09-22：canary `duration_ms=3068`；48 台批量约 4 分钟，
  `SUMMARY ok=48 converged=1 fail=0 skipped=0`。旧稿写的「约 20s/台」已过时）。
  stdout 是块缓冲，重定向到文件时日志会长时间为空，**进度看 DB/API 的分布，别盯日志**。
- 推 `backend/agent/` 源码树（含 scripts/）→ 各 host `/opt/stability-test-agent/agent/`，自动重启 Agent。
  code 载荷口径 = 整棵 agent 树 −（`tests/`、`test_*.py`、`__pycache__`、`resources/**`、`VERSION`/`ARTIFACT_DIGEST*`/`.env`）
  ⇒ **`backend/agent/scripts/` 里的新版本靠这一步才落到主机**（DB `active` ≠ 主机有文件，
  见 `script-version-lifecycle` §A 第 5 步）——判断「有没有下发」不要用 `script-presence`
  的 `missing=0`（无 Plan 引用的新版本不在账本全集内）。
- **带外文件**：`resources/**` 是 protect-only（`stp_agent_priv.PROTECT_ONLY_PATHS = ["resources/***"]`，
  #1950/#2019，契约测试逐项锁定）——只防删除、不做 exclude，必须写 `***`（尾斜杠只匹配目录节点自身）；
  `resources/` **之外**的带外文件仍会被 `--delete` 抹掉，故带外资源仍在**最终**热更新之后放置。
  ✅2026-09-22 实测：连跑两轮 code 推送（48 台）后，主机 `resources/`（aimonkey 82M + flashtool 149M）
  仍在位且 mtime 未变 ⇒ 「code 推送不抹带外资源」成立。
- **但「resources 已收敛」不能只看徽标**：主机上报的 `agent_resources_digest` 是部署流程**写进去的
  意图值**（`write-digest` 只校验 `sha256:<hex>` 格式、从不重算），不是主机实测——`plan_convergence`
  的 `resources_drift` 因此在比对「控制面写的值 vs 控制面期望」，主机真实内容不参与。
  ✅同日逐台复核（用**主机上同一份** `artifact_digest.py` 现算 48 台，先核对两侧模块 sha256 一致）：
  code 侧 48/48 与期望一致；resources 侧 **41/48 一致、7 台偏离**（差异只是 CRLF→LF，语义同一，
  但平台判其 converged 且永远不会收敛它）。要判 resources 真实到位，必须自算比对——
  探针与结论见 `docs/notes/process/2026-09-22-sop-warn-items-verification.md`，机制缺口是 issue #3128。
- **不要**在 hot-update 未返回成功时抢 `reload_config`（曾致 event_uploader 读到旧 flag）。
- **判「哪些 host 忙」要经 `device.host_id` join**（✅2026-09-23）：`job_instance.host_id` 列在生产上常为空，按它查会得到 0 台忙、随后 hot-update API 回 `HOST_HAS_ACTIVE_JOBS`（带 `active_jobs` 清单；**不要**顺手 `abort_running_jobs`）。批量脚本默认跳过忙碌主机（`"skipped": "active_jobs"`），run 结束后**重跑同一命令**即可补齐，已收敛的回 `converged`（实测 `SUMMARY ok=11 converged=11 fail=0 skipped=37`）。
- **ADR-0051 Phase 2b 脚本包执行开关**（✅2026-09-23 灰度实跑）：在 `.env.backend` 加 `STP_AGENT_SCRIPT_PACKAGES=on|strict`，**重启后端**（API 路径的 env 来自后端进程环境）再 hot-update，响应 `env_keys_synced` 应含 `STP_SCRIPT_PACKAGES`。**digest 相等时 env-only 变更是 `nothing-to-converge`**（不下发），改开关值必须 `batch_hot_update --direct --force`。验证：`POST /script-presence/refresh?host_id=…`（走 verify_scripts 的整包核验并预热 `tools_cache`）后主机上 `find /opt/stability-test-agent/tools_cache -name .stp-verified | wc -l` = 全集版本数（实测 52），`journalctl -u stability-test-agent | grep -c script_packages_fallback_tree` = 0。全 fleet 无回退后再切 `strict`；**Phase 3 删目录前 fleet 必须全在 strict**（ADR-0051 v1.1）。

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
| SP Flash Tool host 缺库 ✅2026-09-22 真机验证 | 五个包名与工具真实依赖**一致**（`ldd` 在控制面与真机各核一次：缺库主机上 `flash_tool` 有 11 个未解析依赖，正好含这五个；工具自带的 Qt4 在 `lib/` 里，不是系统前置）。**正经处置走平台 provisioning**：`POST /api/v1/hosts/{id}/flash-prereqs/ensure`（= `tools/ansible/playbooks/ensure_flash_prereqs.yml`，内含 `libglib2.0-0t64` 兜底 + dialout/udev 归位；ADR-0037 D5「provisioning 归位、运行期脚本不装包」）。逃生阀（平台不可用时）：`sudo -n apt-get install -y --no-install-recommends libice6 libsm6 libxrender1 libfontconfig1 libglib2.0-0`——Debian 13 上 `libglib2.0-0` 是过渡名，apt 装别名 rc=0，但**复查要用 `dpkg -s libglib2.0-0t64`**。实测 fleet 分布：48 台中 38 台齐、**10 台五库全缺（全在 `agent_legacy` 组）**，缺库主机上刷机前置检查会明确失败（不静默）；处置命令与探针见 `docs/notes/process/2026-09-22-sop-warn-items-verification.md` |
| MLD 拼写 | `getprop ro.product.model` 返回 `MLD-LX3`（连字符），`adb devices` 是下划线——以 getprop 为准 |
| 部署后代码 | 部署验证完成后按仓库流程走 PR 合入，不直推 main |
| 本地 ref 陈旧 | worktree 基于 origin/main 前必 fetch；构建前用 `merge-base --is-ancestor <PR mergeCommit> origin/main` 校验 |
| **CLI 跑 `run_sweep` 全量 = 静默打脏**（✅2026-09-25 实跑踩坑，#3315/#3333） | verify RPC（`call_agent_rpc`）走 **backend 进程内 socketio 长连接**，CLI 进程里 48 台全 `AgentNotConnectedError`，但 CLI 返回形似成功（`hosts_verified=48/rows=2496`）——副作用：`host.script_packages_mode` 全被 None 打脏、账本按 `agent_offline` 落库并推进 `checked_at`（summary/UI 新鲜度被喂假；alert 不受影响，它吃进程内 gauge）。全量 sweep 合法触发点**只有每日 cron**（`script_presence_sweep_cron`，默认 09:30）；部署后要立即重采走 `POST /refresh?host_id=` 逐台循环（实测 48 台 fail=0，列恢复 `{package:48}`；gauge 仍要等全量轮）。入口缺口见 #3333 |
| **热更新清带外资源**（2026-08-31 记录，**该形态已被修**） | 08-31 当时 `--delete` 会清掉 `resources/` 下非豁免目录（只有 `resources/mtbf/` 豁免）。**当前不再成立**：`stp_agent_priv.PROTECT_ONLY_PATHS = ["resources/***"]`（#1950/#2019，契约测试逐项锁定）把整棵 `resources/` 设为 protect-only——只防删除、不做 exclude，且必须写 `***`（尾斜杠只匹配目录节点本身）。`resources/` **之外**的带外文件仍会被 `--delete` 抹掉，故带外资源仍在最终热更新后放置 |
| **载荷根未跟踪文件**（#3112→bundle 形态） | checkout 时代由部署源守卫在部署时硬拦；Phase 1 后判据前移到**构建时**：bundle 复制工作树，未跟踪文件会随构建进发布根并改 desired digest——构建前照跑守卫（§1 步 2），发布根内禁止手改（改动只发生在构建） |

## 7. 校准记录

| 日期 | 校准了什么 | 来源 |
| 2026-09-23 | **Phase 1 实切**：§1 重写为 bundle 发布根流程（build_bundle → `stp-releases/<rev>` + `current` 链接；unit 指 current；发布根 `.env.backend` 为仓根 symlink；回滚 = 重指 current / 恢复 `*.bak-20260923-phase1`）；§3「desired digest 现算工作树」与 §6 守卫两条按新形态改写。实切数据：烟测（import / alembic 对齐 / `_AGENT_SOURCE_DIR` 落发布根）全过；切换后 scan `skipped=210 conflicts=0`、单机热更新 `converged(digest-matched, sha256:55bb3dfee75f…)`、presence 0 missing/0 mismatch | 本次实切 + `docs/notes/process/2026-09-23-sop-phase1-bundle-cutover.md` |
|------|-----------|------|
| 2026-08-27 | v0 骨架创建（全部 ⚠️待校对） | memory + runbook 预起草 |
| 2026-08-28 | 新增 §1 控制面后端更新与 DB 迁移路径（本机即生产控制面） | docs/production-minimum-deployment-checklist.md §3.5 + k8l9m0n1o2p3 迁移 |
| 2026-08-28 | 校准 §1：本机生产代码路径是仓库根 `/home/debian13/stability-test-platform`，非 `/opt/...`（后者不存在）；部署现已实操验证（k8l9m0n1o2p3 真机应用 + restart + 34 ONLINE） | 本机 pgrep/journalctl + 正式部署 |
| 2026-08-27 | 新增 §1.5 前端段：nginx root=仓库内 `frontend/dist-prod`、worktree 构建 + 双 rename 原子切换、浏览器/curl 双验证 | 登记簿 UI 批次（#476/#477）部署实操 |
| 2026-08-28 | 坑表补「本地 ref 陈旧」条目：目标 PR mergeCommit ∈ origin/main 用 `merge-base --is-ancestor` 校验（曾打出旧包） | 同上事故复盘 |
| 2026-08-30 | 四步全链路真机部署（r0s9t8u7v6w5 迁移 + 前端换包 + backend restart + 48 台热更新）后校准：§0 凭据与 token 取值路径（扁平 `.access_token`、双 `-F`）；§1.5 **`/tmp` 是 tmpfs、跨盘 `mv` 非原子**→ 改 `cp -a` 到同盘再双 rename，并记录无需 `VITE_API_BASE_URL`；§2 scan 无 CLI 模块、只有 HTTP 路由 + 「diff 为空则免跑」判据；§3 改为 canary→批量两段式，补 `--direct` 语义、串行 20s/台与 stdout 块缓冲（看 DB 不看日志）。§0/§2/§3 相应 ⚠️待校对 解除 | 本次部署实操 |
| 2026-08-31 | 坑表补「热更新清带外资源」：rsync --delete 清 resources/ 非 exclude 目录（sleep/powercycle/gpu 带外 APK 实测被抹），带外资源须在最终热更新后放置 | #462 三专项部署实操 |
| 2026-08-30 | 新增部署源守卫步骤（§1 step 2/4 前各一行 `tools/dev/check-deploy-source.sh`）：共享工作树曾跑在未合入分支上被推上生产，重启前强制校验 HEAD==main 且工作区干净；已装 systemd unit 另加 `ExecStartPre=-` 兜底（失败仅记日志不中断） | 2026-08-30 事故复盘 + PR |
| 2026-09-15 | **修正 §0 凭据段**（上表 08-30 的「双 `-F`」在实测中不可用）：token 端点请求体是 form-urlencoded（`--data-urlencode`）；取 token 必须带 `Origin: http://127.0.0.1` 过 CSRF（否则 403 `CSRF check failed`）；用户名来源 `$STP_ADMIN_USER`= `stp-admin`（按 `admin` 会 401）；补响应形状差异（`/api/v1/hosts` 裸数组 vs hot-update `{data:}`） | #2180 D 步上线实操（issue #2203） |
| 2026-09-22 | **四段全链路端到端实跑（后端 pull+restart / 前端换包 / scan / 48 台热更新到 `45c159cf`）后逐条校准**：① §0 **修正** 09-15 行记的「hot-update 是 `{data:}`」——该路由无 `response_model`，实测回顶层裸对象 `{"ok":true,…}`；② §1 守卫描述补「载荷根未跟踪文件」硬拦；③ §2 补「scan 只写注册表、主机生效必须跑 §3」；④ §3 补「判据是 digest 不是 revision」「desired digest = 现算工作树（含未跟踪文件）」「write-digest 写控制面 desired ⇒ 自愈」「实测 ~3s/台（旧稿 20s/台过时）」「code 载荷口径」；⑤ §6 **推翻** 08-31 的「热更新清带外资源」——`resources/***` 已是 protect-only（#1950/#2019），并新增「载荷根未跟踪文件」行（#3112） | 本次部署实操 + #3111/#3112 |
| 2026-09-23 | **ADR-0051 Phase 2a/2b 上线实跑校准**：§2 补 scan 的 `package_backfilled`/`package_conflicts` 与 `--register`/`--publish` 两步；§3 补「忙碌判定经 `device.host_id` join、批量跳过后重跑补齐」「脚本包开关 `STP_AGENT_SCRIPT_PACKAGES` 需重启后端再推、env-only 变更须 `--force`、用 presence refresh + `tools_cache` 计数 + 日志 fallback 计数三件套验证」。现场：迁移 `ad51c1d3f2a1`、发布 210 包、scan 回填 210、11/48 台切到 `on`（其余 37 台被 plan_run 518 活跃 job 跳过） | 本次上线实操 |
| 2026-09-22 | **两处历史 `⚠️待校对` 项实机验证并解除**（详见 `docs/notes/process/2026-09-22-sop-warn-items-verification.md`）：① §6「SP Flash Tool 缺库」——五个包名与工具真实依赖一致（控制面+真机 `ldd`、缺库主机 11 个未解析依赖），fleet 分布 38 齐 / 10 缺（全在 `agent_legacy`），处置改指平台 provisioning（ADR-0037 D5）+ 保留带 `t64` 说明的逃生阀；② §3 带外资源——protect-only 实测成立（两轮 code 推送后 resources 仍在位、mtime 未变），**但**盘点 48 台发现 resources 身份 41/48 一致、7 台字节级偏离（仅 CRLF→LF）而平台判 converged：身份是自报意图、从不自测，机制缺口立 issue #3128 | 真机 ansible 只读探针（48 台全量，含主机侧自算 digest 对拍）+ issue #3128 |
| 2026-09-25 | §6 新增「CLI 跑 run_sweep 全量 = 静默打脏」行：#3315 部署观察中 CLI 全量 sweep 把 `script_packages_mode` 48/48 package 打脏为全 None 且返回形似成功；恢复通道=逐台 `POST /refresh?host_id=`（实测 48 台 fail=0 回到 `{package:48}`）；全量合法触发点只有每日 cron；alert 侧不受影响（吃进程内 gauge，随进程重启清零是正确行为）。入口/防护缺口立 #3333 | #3319 部署实跑 + issue #3333 |

## 踩坑守卫（负向约束）

- 部署源守卫（`tools/dev/check-deploy-source.sh`）**现在管的是 bundle 的构建源**：构建前不过
  就停（非 `main` / 有未提交改动 / 载荷根 `backend/agent/` 未跟踪文件 / schema 超前 head 或修订未知，
  判据不变；库落后 WARN 语义同旧）。unit 侧：alembic 对齐是**硬** `ExecStartPre`（`upgrade head` +
  `check_alembic_at_head.py`，无减号）；checkout 时代的软守卫 `ExecStartPre=-check-deploy-source.sh`
  已随 Phase 1 从 unit 移除（发布根无 `.git`，留着只产噪声）；发布根内容以 `release-manifest.json`
  的 digest 为准；
- **禁止直连生产库手动 `alembic upgrade`**——迁移走代码与 PR 流程（§1 step 3）；
- scan `conflicts` 出现时先 `sha256sum` 比对磁盘 vs DB，再决定是否
  `?force_rebaseline=true`（且需无在途 PlanRun）；seed 预建版本 created=0/skipped 是
  正常，勿误判未注册（§2）；
- **DB `active` / `matched` 都不等于「主机上有这个脚本文件」**：脚本从主机本地树执行，
  scan 只写注册表——新版本必须跑一次 §3 才算生效（§2/§3）；判断到位不要用
  `script-presence` 的 `missing=0`（无 Plan 引用的版本不在账本全集内，见
  `script-version-lifecycle` §A 第 5 步）；
- 带外文件：`resources/**` 由 `PROTECT_ONLY_PATHS` 保护（§6 已修正在案），但
  `resources/` 之外的带外文件仍会被 `--delete` 抹掉——部署前确认无此类残留或放到热更新之后；
- **`agent_resources_digest` 是「自报意图」，不得当「实测到位」用**：`write-digest` 只校验格式、
  主机从不重算。要判 resources 真到位必须自算比对（§3 的探针；机制缺口见 #3128）。同理，
  `resources_digest` 一致而实际偏离时平台**不会**自愈——发现偏离要连同「为什么没测出来」一起记；
- **刷机前置缺库走平台 provisioning，不手敲 apt**：`POST /api/v1/hosts/{id}/flash-prereqs/ensure`
  （ansible `ensure_flash_prereqs.yml`，含 t64 兜底与 dialout/udev 归位；ADR-0037 D5）。
  手敲 apt 只是平台不可用时的逃生阀（§6 行内给了带 t64 说明的写法）；
- **不要**在 hot-update 未返回成功时抢 `reload_config`（曾致 event_uploader 读到旧
  flag）；
- 版本门控顺序强制：先推 Agent → 确认 `agent_code_sync_status` 多 matched → 再设
  `STP_AGENT_MIN_VERSION`；反序会让旧 Agent claim 426、PENDING 积压（§4）。
