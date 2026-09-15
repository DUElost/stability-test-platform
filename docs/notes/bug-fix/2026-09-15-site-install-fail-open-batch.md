# 安装链 fail-open 判据收口：#2084 / #2088 / #2020 / #2017 残口

Status: implemented
Class: bug-fix

## Decision

四张单同源：I3 安装链（`39428fe8`）在「声明与实际不一致」时**继续报 PASS**。本批把它们
收到同一处判据上——冲突即 `install_conflict` 且**零写入**，不做「猜意图后降级安装」。

- **#2084 `security_profile` 与 scheme 必须一致**（`tools/site_config/models.py:215`
  `ControlPlane.security_consistency` + `validation.py` 新 `internal_https_profile_conflict`
  文案）：`internal` 配 https 在模型层阻断。`bootstrap.py` 已按 scheme 推导 profile，
  放开该组合只会让 nginx/env 模板对（纯按 profile 选择）静默装出 `listen 80` +
  `AUTH_COOKIE_SECURE=0`。测试改写了钉住旧语义的
  `test_https_always_requires_a_tls_reference[production/internal]`，拆为
  `test_https_requires_a_tls_reference` 与 `test_internal_profile_is_the_no_tls_exemption`
  （`tests/test_site_config.py`）——后者同时断言「internal + http + 无 tls_ref」仍是合法豁免位。
- **#2088 共享系统路径的归属判据**（`tools/site_config/stages.py`
  `shared_path_is_foreign()` / `install_shared_asset()`，`stage_s4_entry` 写入前全量判定，
  check_id `install.s4.shared_paths`）：systemd unit ×2、nginx site、logrotate 四类产物
  都由 `<deploy-root>` 渲染，故「文件内含本站部署根」既是归属证据也是幂等重跑判据；
  **读不到也判 foreign**（不可判定 = 不覆盖）。本站重跑先把旧内容副本写入
  `--state-dir/shared-path-prev/` 再覆盖，不往系统路径扔 `.bak`。
- **#2088 发行版默认站点只停用不删除**：`sites-enabled/default` 改为 `replace()` 到
  `sites-available/stp-disabled-default`。nginx 只 include `sites-enabled`，移出即失效，
  放回即恢复；`unlink()` 会销毁他人资产且不可回放。
- **#2020 量具不得来自被测物**（`tools/site_config/install.py`）：S0 重算 bundle 摘要改为
  按 `__file__` 路径加载安装器自身源码树的 `backend/agent/artifact_digest.py`
  （`_TRUSTED_DIGEST_SOURCE` + `importlib.util`，进程内计算），删除旧的
  `PYTHONPATH=ctx.bundle` + `sys.executable -c` 子进程。bundle 现在只作为**被测数据**进入。
- **#2017 残口（主案已在主干修复）**：`a8c6e53f` 已让 S2 首次写入时
  `generate_site_secret()` 生成 `JWT_SECRET_KEY`/`AGENT_SECRET`/`WS_TOKEN` 并入
  `MANAGED_ENV_KEYS`，`tests/test_site_install.py:338` 起已有断言。本批补的是第 3 条
  修复方向里剩下的那半：**env 文件也纳入占位符守卫**（`stage_s2_release_env` 写盘前
  `_UNRESOLVED_TEMPLATE_PLACEHOLDER.search(rendered_env)`，命中即 `install.s2.env`
  冲突且不落盘）——受管键与模板键失配时，占位值不再能带着 PASS 上线。
- 判据清单同步进设计文档 §5 末（`docs/design/2026-09-multi-site-installation.md` v0.9）。

## Alternatives

- **#2084 按 scheme 选模板对**（`internal` + https → 也走 TLS 模板）：放弃。它让
  `security_profile` 退化为「仅 http 时的子类型」，profile 变成双源，且使
  `internal`+https 与 `production` 实质等价——两剖面唯一差异（ADR-0024 v1.1 的无 TLS
  豁免）被抹平。豁免的前提是「未声明 TLS」，声明了就该换剖面，不是换模板。
- **#2020 在 `tools/` 下再造第三份 digest 副本**：放弃。仓内已有 control-plane 与
  agent 两侧镜像加 parity 测试，再复制一份会多出一个漂移点。按 `__file__` 加载受信副本
  把「谁提供量具」固定成安装器自身的源码树；代价是安装器只能在源码树内运行（已由
  `deploy/*.sh` 的调用形态满足），取不到实现时 `_digest_bundle()` 返回 None → 现有
  `release_digest` 冲突路径接管，不会静默放行。
- **#2088 备份落 `.bak` 后缀**（`/etc/logrotate.d/stability-backend.bak` 之类）：放弃。
  `logrotate.d/` 由通配收录，无扩展名过滤的发行版配置会把备份也轮转一遍；
  `state_dir` 已是 0700 的本站私有目录，不新增系统路径资产。
- **#2088 「foreign 时只告警继续装」**：放弃。它把契约里已写明的 `install_conflict`
  （*"never overwrites or elevates"*）重新变成 fail-open，正是本单要修的东西。
- **不改 `_has_unresolved_placeholder()`（`"<" in and ">" in`）而复用它、新增窄正则**：窄正则
  才能用于 env——`.env.backend.internal.example:6` 的注释
  `# CORS_ORIGINS=http://<控制平面内网IP>` 含尖括号且非 ASCII，宽守卫会确定性误杀。

## Verification

- `pytest tests/test_site_install.py tests/test_site_config.py tests/test_site_config_plan.py
  tests/test_site_bootstrap.py tests/test_site_preflight.py tests/test_site_agents.py
  tests/test_site_inventory.py tests/test_site_handover.py -q` → **378 passed**（新增 6 例全绿）。
- red→green：`git stash push -- tools/site_config` 后跑 `test_site_install.py
  test_site_config.py` → **6 failed, 218 passed**，失败集正是新增 6 例；`git stash pop`
  恢复后全绿。新断言不依赖实现内部结构。
- 新增用例：`test_foreign_shared_system_paths_block_s4_without_writes`（含「他人 unit
  内容逐字不变 + nginx/logrotate 目录未被创建」的零写入断言）、
  `test_shared_system_paths_rerun_leaves_rollback_copy`、
  `test_distribution_default_site_is_disabled_not_deleted`（断言 symlink 目标被原样搬运）、
  `test_bundle_cannot_supply_its_own_digest_implementation`（对抗样本
  `_LYING_DIGEST_STUB`：被篡改树里的 digest 实现回显清单声明值，并把 bundle 设为
  `cwd`；修复后 S0 仍报 `release_digest`，且断言不再出现 `-c` 子进程）、
  `test_unresolved_placeholder_in_env_template_blocks_s2`。
- `ruff check tools/site_config/` → All checks passed。
- **未做真机/站点安装验证**：S4 归属判据与 `.bak` 结论只在 `--system-root` 重定向的
  合成树上验证过；发行版 nginx include 语义、logrotate 通配收录按配置约定推理，
  未在本机（非站点宿主）实测。城市 B/C 现场验收仍是独立未决项。
- 门禁：`scripts/run_gates.py check:quick` 结果见本 PR。

## Revisit

- **同机多站点**：unit / nginx site / logrotate 资产名当前全局固定且无 `site_id` 后缀，
  因此第二站点必然同名 → 本批的 fail-closed 是**有意阻断**，不是待补的 bug。真要同机
  多站，需要先把资产名参数化（含 `system_root` 清理与旧名退役），不能靠放开判据实现。
- **#2088 提出的「一台主机是否只承载一个站点」在 PRD/ADR 层未显式裁决**
  （`docs/prd/2026-multi-site-delivery.md:68`：「一个站点是一组角色，不是一台服务器」）。
  本批按「不假设可同机共存」处理；若产品侧裁决为允许，则回到上一条。
- **#2017 第 4 条修复方向（`backend/core/agent_secret.py` 的占位符黑名单按剖面扩展，
  即 `change-me-in-internal` 也拒）未做**：越出本 Execution 的声明范围，且安装链侧已由
  「首次必生成 + env 占位守卫」双层封住。仍存在的敞口是**人工写入**的
  `change-me-in-internal`——若出现其它入口（手改 env、备份恢复），应单开 issue 处理运行期黑名单。
- #2020 的受信前提是「安装器源码树本身可信」。源码树的可信性属于 attestation 面
  （`validation.py` 里仍是 `DEFERRED`），本批不主张已解决。
- 交叉链接：I3 交付 note 见
  [`feature/2026-09-14-multi-site-site-install`](../feature/2026-09-14-multi-site-site-install.md)
  （主题不同，故新建而非原地改写）。
