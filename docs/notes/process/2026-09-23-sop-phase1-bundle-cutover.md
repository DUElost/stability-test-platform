# control-plane-deploy SOP 校准：ADR-0051 Phase 1 bundle 发布根实切（2026-09-23）

Status: implemented
Class: process

## Decision

本机控制面已按 ADR-0051 Phase 1（D6 选型 B）实切到 **bundle 发布根形态**，SOP 按
「实跑→改文→同 PR 追加 §7 行」回写：

- 发布根 = `/home/debian13/stp-releases/8bc6bc1e/`（build_bundle 产物，release-manifest：
  agent-code `sha256:4f5d6c63…`、host-resources `sha256:fa6f8bc9…`、schema_target `ad51c1d3f2a1`）；
  `current` 符号链接指它；`stability-backend.service` 与 `-migrate.service` 全部路径重写指
  `current`（旧 unit 备份 `*.bak-20260923-phase1`）；软守卫 `ExecStartPre=-check-deploy-source.sh`
  从 unit 移除（发布根无 `.git`），alembic 两道**硬** `ExecStartPre` 原样保留；
- **env 单源**：发布根 `.env.backend` 是指向仓根同名文件的 symlink（切换时先复制后改链，
  避免第二个事实源；改 env 只改仓根、重启生效）；
- SOP §1 重写为「pull+守卫（管构建源）→ build_bundle → 物料/venv → 切 current → 重启」，
  §3 与 §6 里两条 checkout 时代的坑（「desired digest 现算工作树、并行会话临时文件致全 fleet
  drift」「部署源守卫在 unit 里软跑」）按新形态改写；§7 追加实切行。

## Alternatives

- **用 `tools/site_config` 完整安装器切本机**：弃——它面向整站首装（nginx/PG/存储/prometheus 全碰），
  对已运行的本机风险大于收益；本单只要 unit 路径迁移。
- **unit 直接指 `stp-releases/8bc6bc1e`（不经 current）**：弃——回滚要改 unit + daemon-reload；走
  `current` 符号链接回滚是一次 `ln -sfn` + restart。
- **发布根复制一份 `.env.backend`**：先这么做、随即发现违反「唯一 env 源」——改 symlink，
  这正是 ADR-0051 反「第二事实源」的口径。

## Verification

（2026-09-23 实切现场，均只读验证或平台自身动作）

- 切换前烟测（发布根 venv）：`import backend.main` OK；`alembic current = ad51c1d3f2a1 (head)`；
  `check_alembic_at_head.py` exit 0；`host_updater._AGENT_SOURCE_DIR` 解析到
  `stp-releases/8bc6bc1e/backend/agent`；`tool_manifest.json` 在发布根；
- 切换后：`is-active=active`、`/health` healthy、journal 无 error；进程 `cwd` 实测
  `/home/debian13/stp-releases/8bc6bc1e`；`POST /scripts/scan` →
  `created=0 / skipped=210 / conflicts=0 / package_missing=0 / unregistered_active=0`；
- 热更新链在发布根取载荷：单机（已收敛 canary）→ `converged, reason=digest-matched
  (sha256:55bb3dfee75f…)`，`duration_ms=0`；presence refresh 17 present / 0 missing / 0 mismatch；
- fleet 现状：11 台新载荷（含 scripts/ 已排除，主机 `agent/scripts` 目录实测 GONE、
  tools_cache 52 包完好），37 台旧载荷（周期回归占用、run 间隙重跑 `batch_hot_update --direct` 补齐，
  strict 模式下不阻塞派发）；
- `tools/dev/check_governance_surface.py` → S1–S15 OK。

## Revisit

- 37 台收敛后无需再动 SOP；若 drift 告警异常，按 §3 新判据先 `cmp -r` 发布根与原 rev 树。
- 前端 nginx root 仍指仓根 `frontend/dist-prod`——§1.5 未动；若要一并入发布根，另立单
  （需同步 nginx site 与 SOP §1.5，属 Phase 1 的自然续集而非本单缺口）。
- `code_version` 展示值在发布根无 `.git` 后为空串（ADR-0040 v1.1 已把 revision 降为纯溯源文本，
  不驱动任何动作信号）；如需展示可改读 `release-manifest.json.product.version`，小改进另立单。
