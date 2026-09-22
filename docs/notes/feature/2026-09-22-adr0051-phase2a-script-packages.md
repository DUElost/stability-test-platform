# ADR-0051 Phase 2a：脚本版本目录打包登记 + package_sha256 回填 + 等价证明（2026-09-22）

Status: implemented
Class: feature

## Decision

按 ADR-0051 §5 Phase 2a 落地，**`nfs_path` 与执行路径一律不动**（2b 事项）：

- `tools/dev/check_script_packages.py`：枚举 `backend/agent/scripts/<name>/v<ver>/`（判据镜像
  `script_catalog._iter_script_entries` / `_pick_entry`，后端测试逐目录对拍），成员取
  `git ls-files`（sha 只取决于 Git 内容，不受工作树未跟踪产物影响），复用
  `package_tool_asset.build_deterministic_tar_gz` 打包，`--register` 追加登记、默认 `--check`
  重建比对、`--publish` 写站点 `packages/`。登记 35 族 210 版本，`python: null`。
- `check_tool_manifest.py`：`python` 允许 `null`（Agent 自身解释器，ADR-0051 D4）；`""` 仍红。
- `tool-manifest` 门禁与同名 CI 步骤追加 `check_script_packages`（**不新立 gate**，D8）。
- `script.package_sha256` 可空列（迁移 `ad51c1d3f2a1`，不读文件系统）；scan 从
  `tool_manifest.json`（仓根 / bundle 根，`build_bundle` 已随身复制）按 (name, version) 回填，
  行上已有且不等 → `package_conflicts`（不改写）；`force_rebaseline` 同步重锚包 sha。
- `backend/scripts/check_script_package_equivalence.py`：只读四项判定（目录 / 入口 sha /
  伴随 sha / 包 sha ⇄ manifest ⇄ 行上列），基准 = 当前树字节（§1.3）。
- API `ScriptOut.package_sha256` + `types.ts ScriptEntry.package_sha256`（契约测试对拍）。
- `tool_cache.resolve_packaged_scan_tool`：`python: null` → `sys.executable`（2b 前不会命中）。

## Alternatives

- **成员用 `os.walk` + 排除面**：弃——版本目录里有未跟踪 `__pycache__`，排除面靠猜；
  `git ls-files` 让「登记值可由任何人从 Git 复现」成为定义而非约定。测试夹具（不在 Git）退回排除面。
- **新立 `script-packages` gate**：弃——D8 治理面做减法；并入 `tool-manifest` 门禁与同一 CI 步骤，
  S5x 配对不变。
- **迁移里回填 `package_sha256`**：弃——迁移读文件系统会把「部署树是哪棵」的问题带进 schema
  历史（ADR-0046 病根）；回填放在 scan，输入树由 `STP_SCRIPT_ROOT` 决定。
- **回填不等即覆盖**：弃——manifest append-only 下不等只可能来自库侧，覆盖会掩盖事故；
  记 `package_conflicts` 交人判。
- **等价证明基准取首次发布 commit**：弃——生产 `scan_rebaseline` 5 次 / 24 版本（§1.3）。
- **本 PR 发布包到 `/mnt/stp-aee/packages`**：弃——站点存储写入属运维推进项（同 #3075 v1.12 口径），
  命令已写进 `script-versioning.md`。

## Verification

- `tools/dev/check_script_packages.py --self-test` / `--register` / `--check`：210 目录登记等价，复跑同 sha；
- `tools/dev/check_tool_manifest.py --self-test` 绿（含 null/"" 双向）；`--base origin/main` 见 PR 描述（读 HEAD，需提交后跑）；
- `scripts/run_pytest.sh tests/test_adr0051_phase2a.py backend/tests/services/test_script_catalog_package_backfill.py`：见 PR 描述；
- `tests/test_api_response_shape_contract.py -k Script`、`test_script_catalog_activation.py`：13 passed；
- 生产只读等价证明：`EQUIVALENCE OK rows=212 ok=210 retired_without_dir=2 failing=0 package_column_present=false`
  （`gpu_setup@1.0.3` / `unisoc_probe@1.0.2` 无目录且已退役，不判红）；
- `alembic heads` 单头 `ad51c1d3f2a1`；`check:quick` 与 agent 套件见 PR 描述。

## Revisit

- 迁移合入生产后跑一次 `POST /scripts/scan`，响应 `package_backfilled` 应为 210、`package_conflicts` 为空；
  再跑等价证明应 `backfilled=210`。
- Phase 2b 起 `package_sha256` 成为运行时校验唯一判据；`python: null` 分支在 `resolve_packaged_scan_tool`
  被真实命中，需补 Agent 侧用例。
- Phase 3 删目录后 `check_script_packages` 的「重建」输入改为单棵源码树 + 登记时 commit，届时改判据。
