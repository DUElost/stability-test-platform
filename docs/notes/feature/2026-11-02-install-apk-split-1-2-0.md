# install_apk v1.2.0：split APK 与装后版本回读

Status: implemented
Class: feature

## Decision

`install_apk` 新增 v1.2.0，保持单 APK 行为不变，增加：

- `apk_paths`（显式列表）或 `apk_dir`（目录内 `*.apk`，`base.apk` 优先）时，
  走 `adb install-multiple -r`；
- 给出 `pkg_name` + `required_version` 时，安装后回读 `dumpsys package` 的
  `versionName` 并与目标精确比较，不一致即失败；
- 未指定新参数时仍走 `adb install -r <apk_path>`，`apk_install:<basename>`
  的 PROGRESS phase 保持不变；
- 默认 `timeout_seconds` 由 120 调整为 300（大 APK/split 安装留余量，调用方仍可覆盖）。

影响文件：`backend/agent/scripts/install_apk/install_apk.py`、新增
`backend/agent/tests/test_install_apk_split_1_2_0.py`、`tool_manifest.json`
追加 `install_apk@1.2.0`（package_sha256 `0a2cef43...`）。该版本用于 Chrome 156
这类 Google Play split bundle（base + split_chrome + 语言/功能 split）；当前
`install_apk@1.1.0` 的 `adb install -r` 无法安装 split 集。

## Alternatives

- **Host 侧 adb 批处理**：不改平台即可 `install-multiple`，但没有 Plan/Run 台账，
  设备/主机映射与失败重试要另维护；作为一次性兜底保留。
- **要求单 APK 通用包**：方案 A 可原样使用，但 Google Play 的 Chrome 156 只给
  split 集；重新合并/重签会破坏 Google 签名，不可行。
- **新增独立 `install_apk_split` 脚本族**：语义重复，且会让调用方按 APK 形态选脚本；
  扩展同族参数更小。
- **不做装后回读**：只依赖 `adb install` 的 Success。历史上有“安装返回成功但版本未变”
  的盲区，split 场景更需要回读，故纳入本版本。

## Verification

- `./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_install_apk_split_1_2_0.py backend/agent/tests/test_script_progress_stamps_1690_batch2.py -q`
  → 28 passed（split 命令构造、base 优先、单 APK 回归、skip、装后不一致、缺文件）。
- `python tools/dev/check_script_packages.py --register install_apk 1.2.0` +
  `python tools/dev/check_script_packages.py` → 39 个族树与 manifest 最新登记等价。
- 发布后需在单台 NX2 上 canary：`adb install-multiple` 安装 Chrome 156
  （base + 5 个 split），`versionName=156.0.8078.73`，Chrome 可启动；通过后再放量。
- 单 APK 回归由既有 `test_script_progress_stamps_1690_batch2.py` 覆盖，
  新测试再断言 `install` 子命令与 `-r` 参数不变。

## Revisit

- 若目标 host 的 adb 不支持 `install-multiple`（版本过旧）→ 改为 host 侧工具或升级 adb。
- 若 split 签名/版本与已装 Chrome 不兼容（`INSTALL_FAILED_UPDATE_INCOMPATIBLE` /
  `INSTALL_FAILED_MISSING_SPLIT`）→ 先做 `pm uninstall-system-updates` + 重试的
  独立流程，不要在本脚本里静默降级。
- 若后续需要降级安装（`-d`）或指定 `--abi`/`--staged`，以新参数或新版本表达；
  不原地改已发布的 1.2.0 包。
- 若引擎层将来提供通用的“装后版本断言”步骤，可移除本脚本内的回读，避免双重口径。
