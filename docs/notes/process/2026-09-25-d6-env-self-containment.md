# D6 env 自持：生产 env 真身落站点，运行时不触开发检出（2026-09-25）

Status: implemented
Class: process

## Decision

ADR-0051 D6「控制面部署源与开发工作区物理分离」的最后一米：Phase 1 只分离了载荷，
发布根 `.env.backend` 仍是指向开发仓根的 symlink——删 checkout 生产即瘫，transition
`control-plane-env-lives-in-checkout` 挂账。本次实切（owner 批准生产写操作）：

1. 站点真身 `/home/debian13/stp-releases/env.backend`（600，自仓根 `cp -a` 一次，`cmp` 证一致）。
2. 全部 11 个 rev 根 `.env.backend` 改树内相对 symlink `→ ../env.backend`（含并行会话新构建的
   e9ca6933；老 rev 一并改，防回滚时复活 checkout 依赖）。
3. **零代码改动**成立的前提是消费面清点：`main.py`/`env_source`/alembic/各 checker 全按
   `Path(__file__)` 派生「代码树根」→ 生产上即发布根，穿透 symlink；unit `EnvironmentFile` 走
   `current/`；守卫测试 `test_production_env_source` 只钉文件名与层级不钉绝对位置。
4. **显式代价**：仓根 `.env.backend` 降级为纯 dev 配置，两文件自此可漂移——SOP §0/§1、runbook §0
   写明「生产取数一律站点文件」；「独立站点 env 的渲染分发面」留给多站点续集（ADR-0041），
   本单只闭合 D6 的运行时依赖。
5. 台账转 done（evidence=实测），ADR v1.6 行，README/DOC-MAP S12 token 同步。

## Alternatives

- **每 rev 复制 env 真身**：第二/第三源、换 rev 要重放，违「单源」且制造漂移面更大；弃。
- **env 落 `/etc/stp/`**：语义更正但引入新目录/权限惯例与 unit 改动；站点层目录
  `stp-releases/env.backend` 与发布树同生命周期、少一个概念；选后者，`/etc` 归多站点续集定夺。
- **只切 current 指向的 rev**：回滚到旧 rev 即复活 checkout 依赖，D6 判据（删 checkout 无恙）
  要求全量；11 个 link 一次改完。

## Verification

- 切换后实测：`systemctl restart` 后 health `status=healthy`、alembic 对齐（ExecStartPre 三道含
  `check_db_pool_budget` 全过=站点 env 被正确解析）、admin token 签发 OK（JWT 键来自站点文件）、
  48/48 心跳新鲜（socketio 重连）、`fleet_packages={package:48, unknown:0}`（不变量未受扰动）
- `cmp` 证明真身与源一致后才改 link；回滚路径=link 改回仓根目标+重启（SOP §7 行记录）
- 门禁：`check_transitions`（done 需 evidence + exit 可解析）与 S12 随 `check:quick` 复跑

## Revisit

- 明早 09:30 cron 全量 sweep 后核对 gauge `stability_host_script_packages_mode{package}=48`
  （#3315 观察收口，与本单同链）。
- 多站点交付（ADR-0041）时把站点 env 升级为渲染面（模板+secrets 注入），并评估 `/etc` 归位；
  届时 ADR-0051 §D6 补一句终态出口。
- 仓根与站点 env 漂移暂无机器对账（键集合层面）；若 #3333 类守卫扩展，可加 env 键奇偶对
  （当前不做：dev/prod 键集本就不同，对账易假红）。
