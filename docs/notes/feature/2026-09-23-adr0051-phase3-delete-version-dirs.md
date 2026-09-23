# ADR-0051 Phase 3：删除版本目录、每族一棵源码树、scan 改从 manifest + 包注册（2026-09-23）

Status: implemented
Class: feature

## Decision

前置已于同日满足（Phase 2a/2b 合入并部署，fleet 48/48 `STP_SCRIPT_PACKAGES=strict`、每台
tools_cache 52 包核验、回退 0），按 ADR-0051 §5 Phase 3 一次性落地：

1. **`backend/agent/scripts/` 210 个版本目录删除**，每族保留最新登记版本的内容为源码树
   （`git mv v<latest>/* ./`，80 文件移动、174 目录删除；`tool_manifest.json` 210 条目**原样保留**）。
   `check_script_packages.py` 改为族树模型：`--check` = 每族树重建 sha == 最新未退役条目、残留 v 目录红、
   改树未发版本红、幽灵族红；`--register <name> <version>` 从树打包追加；`--publish` 只落每族最新包。
2. **scan 的注册输入 = `tool_manifest.json` + 站点包源**（`script_catalog.sync_scripts_from_manifest`）：
   逐条目核验整包 sha 后从包内取入口 sha / 伴随 sha / capabilities 登记行；`package_missing`（未发布）、
   `package_conflicts`（站点包 sha 或库上包 sha 不等）、`unregistered_active`（只报告）三个新键；
   退役唯一入口 = manifest `retired:true`；**永不因盘上缺失反激活**（ADR-0046 D2 落地），
   `allow_deactivate` / `deactivation_skipped_versions` / `script_tree_matches_deploy_target` 退役；
   `STP_SCRIPT_ROOT` 在后端无读取点（站点安装仍写，兼容旧模板），新增可选 `STP_TOOL_MANIFEST`。
3. **`agent-code` tarball / wrapper / Ansible 三处排除集同源加入 `scripts/`**：主机上的旧版本目录随下次
   热更新 `rsync --delete-excluded` 清掉；脚本只从 `tools_cache` 执行。
4. **`check-script-version-immutability.py` 及其 CI 步骤、gate、锚配对退役**；不可变性由 tool-manifest
   门禁承担。`check_inner_imports` / `audit_silent_exceptions` 的排除面从 `v<ver>/` 冻结目录扩为整棵族树。
5. **AGENTS.md 条款去过渡句**（S11 锚与自测夹具同步）；`check_seed_identity` / 零引用巡检 /
   模板 pin 测试的「磁盘 head」改由 manifest 最新未退役条目定义。
6. 派发补推 `admission_pump._mismatched_entries_for_push` 跳过 `package_active` 行（包模式下推树文件无意义）。

## Alternatives

- **保留版本目录只删 N 个以上旧版本**：弃——目录模型本身是病根（ADR-0051 §1.1），保留任何目录都让
  「目录 = 发布单元」的判据继续存在。
- **scan 继续扫目录（族树）**：弃——树可变，扫树会把未发布内容登记进 DB；注册输入必须是不可变的包。
- **历史版本测试全部保留并重指族树**：部分弃——重指后能通过的保留（它们仍在验证最新行为），
  断言旧版本专有行为 / 比较两版本分叉的 19 个文件删除（`test_*_fork_guards_*`、`test_flash_firmware_v13x`、
  `test_monkey_setup_v23[8-10]`、`test_clear_recents_v10[34]`、`test_flash_preflight_v10[01]`、
  `test_powercycle_setup_v121`、`test_monkey_test_v120`、`test_oobe_skip_v100`、`test_flash_firmware_v135`），
  其中最后两个在最新树上会**挂起**（旧版行为的等待循环）。历史行为的证据在 Git 历史与包里。
- **seed 迁移改写/重放**：不需要——实测 seed 迁移只写字面量 sha 与 nfs_path，不读磁盘。
- **`check_seed_identity` 改读站点包**：弃——它挂在 pr-migrate-empty-db（CI 无 NFS）；改为只对
  族树最新版本判定，历史版本身份由 `check_script_package_equivalence` 守。

## Verification

- `tools/dev/check_script_packages.py --self-test` 绿；真实树 `--check`：35 个族树与最新登记等价、0 残留目录；
- `backend/tests/services/test_script_catalog_sync.py`（11 例：建行/幂等/缺包/包 sha 不等/退役显式与缺失不反激活/
  未登记只报告/漂移→conflict→force 重锚/回填与库侧不等/runtime_root 含 Windows/外部族与坏 manifest）+
  `backend/tests/api/test_scripts.py` 重写的 5 个 scan 用例：38 passed；
- `tests/test_ansible_digest_contract.py` 7 passed（三处排除集同源含 `scripts/`）；
- `tests/test_adr0051_phase2a.py`（族树模型）+ 模板 pin + flash 常量对拍 + provisioning + inner-imports 棘轮：82 passed；
- `tests/test_seed_revision_version_guard.py` 23 passed / 18 skipped（历史版本显式 skip）；
- `tests/test_check_exemption_protobuf_814_746.py` 18 passed；
- Agent 套件最终（`env -i … pytest backend/agent/tests`）→ 2117 passed；仓库级 `tests/` 全套 + 后端相关子集 → 1902 passed / 18 skipped；关键套件复跑 → 194 passed / 18 skipped；`check:quick` → 15 gates OK；治理面守卫 S1–S15 与 env 清单 `--check` OK；
- 测试清理最终量：19 个整文件删除（历史版本 / 版本分叉守卫）+ 逐文件 AST 摘除 92 个用例（含 powercycle 旧版窗口 21 例）；`test_gpu_power_sleep_resources` 的 `gpu_setup` 参数化改只断言族树；

## Revisit

- 合入后运维：热更新一轮（主机旧目录被清）、`POST /scripts/scan` 期望 `created=0 / package_missing=[] /
  unregistered_active=[历史 seed 行…]`；Start-Log-Scan 等外部族条目仍走 `tool_cache` 原路径。
- 新脚本版本流程：改族树 → `--register` → PR（tool-manifest 门禁）→ `--publish` → scan；SOP §2 已写。
- `nfs_path` 现为合成路径锚（主机上无该目录）；Phase 2b 的 `tree` 回退模式已无回退对象，
  `STP_SCRIPT_PACKAGES=off|on` 可在后续切片删除，默认改 `strict`。
