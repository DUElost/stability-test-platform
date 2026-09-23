# ADR-0051 Phase 2b：Agent 按包身份执行脚本（DB 权威 → tools_cache）+ Phase 3 依赖勘误（2026-09-23）

Status: implemented
Class: feature

## Decision

1. **勘误先于实现**：ADR v1.0 §5「Phase 3 删目录只依赖 2a」不成立。实测两条运行时事实：
   `host_updater._TAR_EXCLUDES` 不含 `scripts/`，热更新 tarball 整树带脚本；主机端
   `stp_agent_priv.cmd_apply_code` 是 `rsync --delete --delete-excluded`。仓库删目录 =
   下一次热更新删主机目录；scan 又以目录为注册输入。故 Phase 3 前置改为
   **2a + 2b + fleet 全部 `strict` 且一轮 verify 全 `package_active`**（ADR v1.1）。
2. **新建「DB 权威 → 包身份」一环**：`backend/agent/script_packages.py::resolve_script_path`
   —— `ScriptEntry.package_sha256`（控制面 `/api/v1/scripts` 已带该列）→
   `tool_cache.ensure_package(name, version, sha, packages_root, cache_root)` → 从
   `tools_cache/{name}/{version}/<入口 basename>` 执行，cwd = 包根。
3. **开关 `STP_SCRIPT_PACKAGES`**（Agent 侧，默认 `off`；控制面源键 `STP_AGENT_SCRIPT_PACKAGES`
   经 `_AGENT_SCOPED_ENV_KEYS` 下发）：`off` 一律 `nfs_path`、不碰包源；`on` 优先包、失败回退
   `nfs_path`（WARNING `script_packages_fallback_tree reason=…`）；`strict` 只走包、失败 =
   步骤 exit 2 `script package unavailable`。行级灰度（`package_sha256` 为空 → 旧路径）叠加其上。
4. **三处 `nfs_path` 形态耦合解除**（`pipeline_engine._run_script_action`）：PYTHONPATH 注入
   的 agent 目录改为本模块所在目录（不再 `parents[3]`）；cwd 取解析结果；
   `_script_terminate_grace_seconds` 改按脚本**名**判定。子进程额外得到 `STP_SCRIPT_SOURCE=package|tree`。
5. **`verify_scripts` 双轨**：expected 带 `package_sha256`（`precheck.scripts` 与
   `script_presence.build_expected_manifests` 同口径）且开关开 → 以整包核验为准并预热缓存，
   ack 行新增 `package_active`；否则维持文件 sha 判定。
6. Agent 本地 sqlite `script_cache` 加 `package_sha256` 列（幂等 ALTER，断网回退保真）。
7. **本版不排除** tarball 里的 `scripts/`（排除即删主机目录，归 Phase 3）。
8. **打包器确定性缺陷修复**（2a 遗留）：`build_deterministic_tar_gz` 曾把文件权限位原样写进 tar 头，
   同一份 Git 内容在 umask 002 的机器上打出 0664、CI 上打出 0644，sha 分叉——本机 210 项全部
   不等价，而 `72250d4b` 只是按 644 环境重登记了一遍。现按 Git 语义归一化（文件 0644/0755 按可执行位、
   目录 0755、symlink 0777），与 ADR-0040 digest 的「可执行位」口径一致。`backend/agent/scripts`
   无可执行位文件，归一化后的值 == 现登记值，**无需重登记**；`Start-Log-Scan` 已发布 tarball 的 blob
   sha 不受影响（Agent 校验的是 blob），但从源目录**重打**会得到新 sha → 须发新版本号。

## Alternatives

- **只靠行级灰度、不加开关**：弃——scan 回填一次把 210 行全置非空，节奏不可控；且包未发布时
  每步都先读一次 NFS 再回退，全 fleet 告警噪声。开关 `off` 时零 NFS 读。
- **`ScriptRegistry._compute_version` 纳入 `package_sha256`**：弃——该值与控制面
  `script_catalog_version` 双侧镜像实现对照，单侧改动即制造永久 drift。
- **verify 失败时不触发 SFTP 补推**：未做——`admission_pump._mismatched_entries_for_push`
  仍按 `ok=False` 推树文件；包模式下补推无用但无害（再次核验仍失败 → 显式
  `script_verify_failed`）。记入 Revisit。
- **`agent-code` 立即排除 `scripts/`**：弃——见 Decision 7。
- **`_script_terminate_grace_seconds` 保留路径判定**：弃——这是「路径形态被当契约」的实证
  （ADR-0051 D4 第 3 条），包路径恰含子串只是侥幸。

## Verification

- `env -i … pytest backend/agent/tests/test_script_packages.py test_pipeline_engine_script_action.py
  test_script_registry.py test_script_verifier.py test_device_flash_scripts.py`（含新增：off/on/strict
  三态、包内执行 cwd/PYTHONPATH/`STP_SCRIPT_SOURCE`、回退原因、Windows basename、verify 预热与
  树上无文件仍 ok、sqlite 老库 ALTER、registry 断网回退带包 sha）：见 PR 描述；
- `backend/tests/services/test_precheck_scripts.py test_script_presence.py test_agent_env_sync.py`
  （expected 带/不带 `package_sha256`；源键→无前缀键、未设不推、控制面同名键不漏）：见 PR 描述；
- 权限位归一化：`tests/test_adr0051_phase2a.py::TestModeNormalization`（644/664/600 同 sha，755/775 同 sha 且异于 644）；本机（umask 002）`check_script_packages` 由 210 项不等价 → 绿，且 `tool_manifest.json` 零改动；
- `tools/dev/check_governance_surface.py`、`env_inventory.py --check`、`check:quick`：见 PR 描述；
- ADR-0043 宽限旧用例改按名断言（路径串现在判 2.0）。

## Revisit

- fleet 切 `on` 后观察：`script_packages_fallback_tree` WARNING 应为 0；verify ack
  `package_active=true` 全绿；再切 `strict`。任一 host 长期回退 = 该站 `packages/` 未同步。
- Phase 3 时：tarball 排除 `scripts/`、scan 注册改读 manifest、开关默认改 `strict` 并删 `off/on`。
- 包模式下 SFTP 补推的无效动作：Phase 3 随 `nfs_path` 语义一起收掉。
- Windows host（`PureWindowsPath` 的 `nfs_path`）：basename 解析已覆盖，但 `tools_cache`
  在 Windows 上未真机验证。
