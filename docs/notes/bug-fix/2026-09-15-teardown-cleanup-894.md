# 链式衔接设备清理完整化：#894 剩余缺口收口（monkey_teardown v1.0.2 / gpu_finish v1.0.4）

Status: implemented
Class: bug-fix

## Decision

- **本次只补剩余缺口，不重做已落地部分**：#894 的三项已在主干（`c89ea355`
  setup 跨专项 prefs 防御、`c2b0b915` finish prefs 回读验证 + monkey
  `att_clean`）。本次收口：设备端脚本/资源删除、清理失败语义、卸载语义评估。
- **monkey_teardown v1.0.2**：按 monkey_test 推送清单完整删除设备端资源
  （`/data/local/tmp/{MonkeyTest.sh, offlinemonkey.sh, aim, aimwd, aim.jar,
  monkey.apk, arm64-v8a, armeabi-v7a}`、`/sdcard/blacklist.txt`），**cleanup
  默认开启**（显式 `cleanup=false` 才跳过），删除后**逐项回读验证**——仍存在
  或探测命令不可用（rc≠0）都记 errors → step 失败。`aimwd` 看门狗纳入默认
  停测清单（删其脚本前先停进程）。探测命令以 `; true` 收尾：设备端 `for`
  循环的 rc 取最后一次命令结果，最后一项不存在时 `[ -e ]` 返回非零，否则
  「清理成功」会被误判成「探测不可用」（自查发现，已加命令形态回归用例）。
  结果产物（`/sdcard/Monkeylog.txt`、`/sdcard/systeminfo`）与大体积媒体资源
  （`/sdcard/resource`，重推成本高）不在清理范围。
- **gpu_finish v1.0.4**：结果 JSON 落盘后删设备端循环脚本
  `/sdcard/Auto/gpu_stress_loop.sh` 并回读验证。**保留 `test_log.txt`**——
  原始日志只 pull 到本机临时目录、平台侧只有摘要 JSON，删除即不可追溯；
  且循环脚本无自启能力，与 monkey 的 boot receiver 叠加场景不同。
- **卸载语义评估（issue 待办 2）：结论是不卸载 AutoTestTool，用跨专项 prefs
  清理替代。** AutoTestTool 是 platform 签名 system app（`pm uninstall` 无效），
  卸载重装成本（签名包 + 每专项一次 install/uninstall）高于残留风险；自启链路
  已由三层覆盖——finish 写后回读（`running=false` 失败即 raise）+ setup
  `clear_cross_prefs()` + monkey `att_clean`。**终态出口**：若出现「三层清理后
  仍 boot 自启」的实证，再评估 APK 级处置（`pm disable-user` 或换签名包）。
- **seed 迁移 `j7k8l9m0n1o2`** 注册两版本；停用引用为 0 的旧版
  （monkey_teardown v1.0.1、gpu_finish v1.0.0），引用不为 0 的旧版
  （v1.0.0 ×5、gpu_finish v1.0.3 ×1）保持 active，迁移内嵌 `#942` 引用守卫。
- **异常设备 6R0A57SSAE7000198 现场处置**：机队实测该设备 `adb devices` 为
  `offline`（2026-09-15），无法远程清理；已在 issue 留痕待设备恢复后处置。

## Alternatives

- **卸载 AutoTestTool**（issue 原文选项之一）：不可行——platform 签名系统 app，
  `pm uninstall` 无效（`c89ea355` 已实测），改为 prefs 清理。
- **gpu 连 `test_log.txt` 一起删**（issue 原文）：拒绝——原始日志未进中心存储，
  删除即丢证；如将来要求删，必须先把原始日志纳入产物上传（见 Revisit）。
- **清理验证失败仅告警**：拒绝（用户裁决）——与 finish prefs 验证的
  raise → FAILED 先例对齐，链式叠加不再静默。
- **原地修改 v1.0.1 / v1.0.3**：违反「已发布版本不可变」，全程用新版本目录表达。
- **不写 seed、仅靠 `POST /scripts/scan` 注册**：也能注册新版本，但激活/停用
  与 plan_step 引用守卫不落确定路径；沿用 `#830` 先例（seed + 守卫）表达。

## Verification

| 项 | 命令 | 结果 |
|---|---|---|
| 新增回归 | `./scripts/run_pytest.sh backend/agent/tests/test_teardown_cleanup_894.py -q` | **11 passed** |
| 反向验证 | 同用例临时指向 v1.0.1 / v1.0.3 | **11 failed**（缺清理/验证即转红），恢复后 11 passed |
| 相关既有套件 | `... test_teardown_rc_guards.py test_gpu_scripts.py test_teardown_cleanup_894.py -q` | **52 passed** |
| agent 全量 | `./scripts/run_pytest.sh backend/agent/tests/ -q` | **1996 passed** |
| 仓库离线子集（PR 路径） | `./scripts/run_pytest.sh tests/ -q --ignore=tests/test_alembic_upgrade.py --ignore=tests/test_script_seed_governance.py` | **594 passed** |
| 门禁 | `.venv/bin/python scripts/run_gates.py check:quick` | `OK (10 gates)` |
| 版本不可变 | `tools/dev/check-script-version-immutability.py --base origin/main` | `OK`（无已发布目录被原地改） |
| 空库迁移（隔离 `postgres:16` 容器 `127.0.0.1:55432`，**非生产库**） | `alembic upgrade head` → `downgrade -1` → `upgrade head` | 通过且幂等；`python -m backend.scripts.check_schema_sync` **rc=0** |
| 生产库只读核对 | `plan_step` 引用计数（只读 SELECT） | monkey_teardown v1.0.0 **×5**（保 active）、v1.0.1 ×0；gpu_finish v1.0.0 ×0、v1.0.3 ×1（保 active） |
| 机队核查 | `ansible -i hosts.ini android -m shell -a 'adb devices -l'` | 6R0A57SSAE7000198 = `offline` |

## Revisit

- **既有 Plan 需重指才吃到本次清理**：生产 `plan_step` 仍引用 monkey_teardown
  v1.0.0（5 处）与 gpu_finish v1.0.3（1 处），新版本不会自动生效——由 operator
  重指到 v1.0.2 / v1.0.4（或新建 Plan）。
- **真机验证留待**：本次未在真机执行删除路径（需在跑测试的设备上实跑 teardown）；
  设备台架恢复后按 issue 步骤验证「删除 + 回读 + 失败可见」三态。
- **异常设备现场处置**：6R0A57SSAE7000198 恢复上线后执行 prefs
  `running=false` + force-stop AutoTestTool，并复核 `expected_cycles` 旧配置残留。
- **gpu 原始日志归档**：若要求删设备端 `test_log.txt`，需先把 pull 到的原始
  文件纳入产物上传路径（当前只有摘要 JSON 进 NFS）。
- **monkey `/sdcard/resource`、`/sdcard/systeminfo` 不在清理范围**：前者重推成本高，
  后者是结果产物；若后续要求「完全还原」需另行评估。
- **sleep_finish v1.0.2 的 `_lib.py` 有重复 `_verify_stop_flags` 定义**（后者覆盖
  前者，行为等价）——已发布版本不可变，未在本单修正；下次 sleep_finish 发版时顺带清理。
