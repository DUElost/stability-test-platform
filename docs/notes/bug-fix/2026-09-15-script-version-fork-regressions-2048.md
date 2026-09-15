# #2048 脚本新版本从旧基线拷贝，把已修缺陷带回（gpu_setup #755 / monkey_launch #1711）

Status: implemented
Class: bug-fix

## Decision

`#1690` 批量补 PROGRESS 打戳时新发的版本目录，是从**比当时最新版更旧**的版本拷贝的，
把已修缺陷带回主线；而 `backend/services/script_catalog.py:222` 对新发现的目录直接插入
`is_active=True`，新版本一上线即可被派发。

修复：**发新版本**（版本目录不可变，ADR-0020/0039），内容 = 当前版本 + 补回丢失的修复：

| 新版本 | 基线（含本窗口的 #1690 改动） | 补回的修复 | 证据 |
|---|---|---|---|
| `gpu_setup/v1.2.0` | v1.1.0（PROGRESS 打戳） | `#755` `_retry_uninstall_pkgs_for_apk`：重试安装前只卸「当前失败 APK」对应包 | `284a0a42`（v1.0.10）是 `c766dbb2`（v1.1.0）的祖先 → v1.1.0 抄自 v1.0.9 |
| `monkey_launch/v5.2.0` | v5.1.0（PROGRESS 打戳） | `#1711` aimwd post-check 使用**独立** `max_wait` 窗口 | `4e387e12`（v5.0.2）**不是** `c766dbb2` 的祖先 → v5.1.0 抄自 v5.0.1 |

守卫测试同时改造：原 `_load_gpu_lib_v110()` **名为 v1.1.0、实加载 v1.0.10**，新版本出现
后无人覆盖。现在两处：

- `test_gpu_power_sleep_resources.py`：loader 改名为 `_load_gpu_lib(version)`，两个 #755
  用例参数化到 `("1.0.10", "1.2.0")`（历史修复版 + 当前最新版）；
- `test_script_version_fork_guards_2048.py`（新增）：**动态解析最新版本目录**再断言——再
  出现「新版本从旧基线拷贝」时立刻红，不需要有人记得改测试。

**不做**：不原地改 v1.1.0 / v5.1.0（违 ADR-0020/0039）；不加 seed 迁移下线它们（下线是运维
动作，需生产 `plan_step` 引用视图，见 Revisit）。

## Alternatives

- **原地修 v1.1.0 / v5.1.0**：违版本不可变硬不变量，且已按 v1.1.0 派发过的主机拿到的字节
  会变——否决。
- **只改测试指向新目录、不发新版本**：线上回归仍在（可用版本还是 v1.1.0）——否决。
- **seed 迁移里 `is_active=false` 下线 v1.1.0/v5.1.0**：无生产引用视图时只能靠
  `raise_if_any_version_referenced` 兜底，一旦确被引用即**部署中止**——为一次 P2 修复引入
  部署阻断面，收益不抵；留给 Revisit。
- **通用「新版本必须与现存最高版本 diff」门禁**：diff 是语义判断（增删函数哪些是回归、
  哪些是收窄），机械化容易误报——本窗口以「最新版本行为守卫」覆盖，成本更低。

## Verification

**反事实验证**（同一套 harness 跑各版本，证明守卫抓得住回归）：

```
gpu_setup  _retry_uninstall_pkgs_for_apk 存在性：v1.0.10=True  v1.1.0=False  v1.2.0=True
monkey_launch 迟到 sh 场景（MonkeyTest.sh 窗口末段才出现、aimwd 需二次 poll）：
  v5.0.1 success=False  err='aimwd (MonkeyWatchdog) not running after 15s'
  v5.0.2 success=True
  v5.1.0 success=False  err='aimwd (MonkeyWatchdog) not running after 15s'   ← 本单修的回归
  v5.2.0 success=True
```

- `python -m pytest backend/agent/tests/test_script_version_fork_guards_2048.py
  backend/agent/tests/test_gpu_power_sleep_resources.py
  backend/agent/tests/test_monkey_watchdog_chain_809.py -q` → **66 passed**
- `python -m pytest backend/agent/tests/ -q` → **1970 passed / 23 failed**；同 23 个失败在
  **纯净 `origin/main`** 上同样存在（`test_saq_scan_pipeline` 16 / `test_p3_3_multi_instance` 4 /
  `test_legacy_tool_cleanup` 2 / `test_cron_scheduler` 1，均为本机环境基线，与本改动无关）
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**
- v1.2.0 的 `_retry_uninstall_pkgs_for_apk` 与 v1.0.10 逐字一致（除新增的版本注释）；
  v5.2.0 的 deadline 逻辑与 v5.0.2 逐行一致（`sh_deadline` / `aimwd_deadline` 各一），
  且保留 v5.1.0 的 `progress_tick` 打戳与 `capabilities.json`

## Revisit

- **v1.1.0 / v5.1.0 仍是 active 且含缺陷**：若生产有 plan 钉着这两个版本，需按
  `docs/development/script-versioning.md` 的 #942 流程（引用检查后改钉或下线）处置；本 PR
  无生产 `plan_step` 视图，不代为判断。版本下线属 #735 的零引用清理轨道。
- **流程面**：本窗口的根因是「新版本从手头打开的旧版本拷贝」。除本单的行为守卫外，建议在
  脚本评审 checklist（`docs/notes/process/2026-09-13-script-review-checklist-705.md`）里加一条
  「新版本必须与现存最高版本对比，而不是与旧版本对比」——文档面改动留给该 checklist 的
  维护者（避免本 PR 触碰共享元文件）。
