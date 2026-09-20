# powercycle_setup v1.2.1：prefs 读取改 root 能力，「读空」不再当删除证据（#2846）

Status: implemented
Class: bug-fix

## Decision

按 version 纪律出 **`powercycle_setup/v1.2.1`**（v1.2.0 已发布、不可原地修改；
新版本 = 全量副本 + 两处语义修正）：

1. **读路径改 root 优先**（`get_prefs_xml`）：`is_root()` 时直接
   `adb shell cat {_PREFS_DIR}/{_PREFS_FILE}`；无 root 才回落 `run-as`。
   根因：AutoTestTool 是 platform 签名 system app（shared uid `android.uid.system`），
   AOSP 对 non-debuggable / shared-uid 包**恒拒绝 run-as** ⇒ 旧读路径在现场**恒空**。
2. **「读空」不再单独作为删除证据**（`repair_prefs_ownership` + 新增
   `_prefs_file_state()`）：读不到时先 root 下 `test -f` 做存在性核验，
   只有 `present`（文件确实存在却连 root 都读不到：真损坏/属主异常）才 `rm -f`；
   `absent`（本来没有）与 `unknown`（探测失败）→ 不动。

**影响面（为什么值得单独出补丁版）**：旧行为在每次 `set_prefs` / `set_stop_flags`
入口都先删健康 prefs ⇒ ① `set_prefs(reset_count=false)` 的续跑语义失效
（删后 `current_count` 恒 0）；② `start_task` 读空 ⇒ 跳过 `running=true`
⇒ `auto_resume` 跨重启断链；③ `set_stop_flags` 把完整 prefs 降成 2-key 最小图
（丢 `test_times` / `current_count`）。

**版本串与能力声明**：`capabilities.json` 原样继承（`progress_stamps`）；
`install_apk`（v1.2.0/#2756）语义不变；模块 docstring 顶部补 v1.2.1 条目。

## Alternatives

- **原地改 v1.2.0**：否决——ADR-0020 版本不可变（已发布目录是历史事实，改了会
  让「哪个版本产生过什么行为」不可考）。
- **保留旧判据、只在读空时多打一条 warning**：否决——误删是**破坏性**行为
  （真文件被删），warning 不能抵消；且 root 完全能读，没有理由删。
- **用 `dumpsys package` 判包是否 debuggable 再决定路线**：否决——多一次慢调用，
  且判据仍是「AOSP 对 shared-uid 恒拒 run-as」这一事实；直接 root 读更短更稳。
- **`repair_prefs_ownership` 整体删掉**：否决——真损坏/属主异常的文件仍需重建
  路径（保留 `present` 分支），且删除会让「最小修复」变成行为不可预期的更大改动。
- **用 `returncode` 判 `am kill` 类失败**（#2862 面）：与本单无关，不混。

## Verification

- `pytest backend/agent/tests/test_powercycle_setup_v121.py -q`（`env -i` 干净环境）→
  **6 passed**：root 可读不删 / absent 不删 / 探测 unknown 不删 / 无 root 不删 /
  present 但不可读删一次 / **v1.2.0 对照锚点**（同场景旧版确实 `rm -f`，证明差异
  来自本补丁而非环境）；
- `pytest backend/agent/tests/test_powercycle_scripts.py backend/agent/tests/test_powercycle_setup_v121.py -q`
  → **74 passed**（既有 v1.0.0–v1.2.0 用例不回归）；
- `check:quick` → **[OK] 12 gates**；`check:pr` → **[OK] 21 gates**
  （含 pr-migrate 空库迁移 + seed 身份对拍——新增版本目录未被身份门禁拦下）。

## Revisit

- **真机 E2E（pending，按 #310 既有 rig）**：本单只做代码级修复 + 单测；真机验证
  「续跑 current_count 保留、auto_resume 跨重启生效」待设备窗口（device-lease 类
  脚本，同 #2846 原文标注的 pending）。
- **上线路径**：新版本目录落地后需要一次 scan + `plan_step` 重指（#2865 同族问题：
  「新版本仓库侧零引用」）——本 PR 不含生产侧激活动作。
- **同类面**：`sleep_setup` / `gpu_setup` 等其他专项脚本若也有「run-as 读 prefs」
  形态，按同判据核（可在后续波次用 `git grep "run-as .*shared_prefs"` 复扫）。
