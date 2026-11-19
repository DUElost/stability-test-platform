# ADR-0051 Phase 3 `check_script_package_equivalence` 对齐迁移（2026-11-19）

Status: implemented
Class: bug-fix

## Decision

将 [check_script_package_equivalence.py](../../../backend/scripts/check_script_package_equivalence.py)
从废弃的 ADR-0051 Phase 2a 版本目录模型迁移到现行 Phase 3 族树 + 站点包源模型：

- 入口对齐：`main()` 直接使用 `resolve_database_url()` + `normalize_sync_database_url()`
  连接数据库（只读，不回显凭据），并通过 `sa_inspect(conn).get_columns("script")` 兼容 PG 与 SQLite；
  在 [`tests/test_sync_database_url_scripts.py`](../../../tests/test_sync_database_url_scripts.py)
  的 `HELPER_SITES` 登记该脚本（#2471 同步取串守卫）。
- 站点包核验：复用 [`script_catalog.read_package_facts`](../../../backend/services/script_catalog.py)
  与 `default_packages_root()`（支持 `--packages-root` 覆盖），对每条 `script` 行对照
  `tool_manifest.json`（`kind == "script"`）与 `<packages_root>/<name>/<version>.tar.gz`
  核验整包 sha、入口名与 `nfs_path` basename、入口文件 sha、`support_files_manifest`、
  `capabilities` 以及行上 `package_sha256`。
- 豁免边界：已退役且不在 script manifest 中的历史行（`retired_untracked`）、manifest 标记
  `retired: true` 且已按 ADR-0051 D5 清理站点包的历史行（`retired_without_package`）及
  `LEGACY_AEE_SCRIPT_NAMES` 不判红；不在 manifest 中的活跃行（`unregistered_active`）判红。
- 源头闭环：新增 `evaluate_manifest_heads()` 检查每族最新未退役条目均已 `--publish` 到站点，
  并复用 `tools/dev/check_script_packages.py`（`rebuild_all` + `check`）校验当前族源码树
  与 manifest 最新条目一致（可用 `--skip-tree` 跳过）。

## Alternatives

- **恢复 `check_script_packages.expected_entries` 兼容壳**：弃——Phase 3 已删除
  `backend/agent/scripts/<name>/v<version>/` 版本子目录，旧 `evaluate()` 仍会按 `v<version>`
  目录判失，无法验证历史包的真实 tarball 内容。
- **直接退役删除 `check_script_package_equivalence.py`**：弃——`control-plane-deploy`
  、`2026-08-29-post-review-deploy-runbook.md` 与 `check_seed_identity.py` 均将该脚本定义为
  历史/全量 `script` 行等价只读证明入口，补齐 Phase 3 实现可即刻恢复运维与上线核验闭环。

## Verification

- 单元测试与守卫套件：
  `backend/tests/test_check_script_package_equivalence.py`（10 用例，覆盖全绿、历史退役豁免、
  6 维行漂移、`tarball` 篡改/缺失、头版本未发布、`--skip-tree` 与缺参 exit 2）、
  `tests/test_sync_database_url_scripts.py`、`tests/test_adr0051_phase2a.py` 全通过。
- 控制面生产库只读实测（连接 `.env.backend` + `/mnt/stp-aee/packages`）：
  `EQUIVALENCE OK: {"rows": 273, "ok": 269, "backfilled": 269, "retired_exempt": 4, "failing_rows": 0, "head_errors": 0, "tree_errors": 0, "failing": 0, "package_column_present": true}`。

## Revisit

- 待后续合入并下发新控制面发布根后，可直接在 `/home/debian13/stp-releases/current`
  复用 `venv/bin/python -m backend.scripts.check_script_package_equivalence` 执行上线核验。
