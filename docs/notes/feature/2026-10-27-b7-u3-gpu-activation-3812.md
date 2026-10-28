# B7-U3：GPU 可信组合与待执行激活清单

Status: implemented
Class: feature

## Decision

按 [#3809 v1.4](https://github.com/DUElost/stability-test-platform/issues/3809) §3.3 的登记范围、
以及 [v1.5 验收裁定](https://github.com/DUElost/stability-test-platform/issues/3809#issuecomment-6444209519) 实施 U3（Refs #3812）。
实施基线是已合入 U1/U2 的 `82e3d8aaa1643f55cbbee4344ad23734ba0a17f9`。

只在 ONBOARDED.gpu 登记 MTK × MLD_LX2 / MLD_LX3 / MOD_LX3 × root / non-root 六组合，
沿用 power-cycle 的列表推导写法。APK、脚本版本、包 SHA 和四项 report_ref 对应规划者已核对的 U2 最终包证据。
power-cycle 登记、匹配政策、脚本族源码、tool_manifest、模板和生产计划均未修改。

Device 原值前检于 2026-10-27 21:51:10 +08:00 完成：目标由站点配置确认，以 stp_ro、
`transaction_read_only=on` 查询 MTK 三型号，原值逐字匹配；计数依次为 317 / 195 / 25。
同型号另有 UNKNOWN × MLD_LX3 59 条，未纳入信任范围。
规划者 v1.5 评论明确接受此前检，无需重做。没有读取或打印应用数据库账号口令。

新 [B7 激活清单](../../operations/2026-10-27-b7-gpu-activation-runbook.md) 覆盖第 0–6 步的命令、闸门、停止条件和回退。
Owner 已确认保持原 scope，因此旧 testing.md、ADR 与 B6 历史方案的过时 GPU 说明在清单中集中勘误，
原文保留历史语境；operations README 增加入口。本单元没有生产激活授权，清单保持“未执行”。
Owner 另行授权仅把 `backend/tests/api/test_onboarded_gpu_b7_u1_3810.py` 加入 scope，
为控制面 U1 测试显式补空表前置；原断言与开关机预期不变，Registry scope 已同步。

## Alternatives

- 未把同型号的合法权限互换判为失败：完整六组合覆盖两种权限，互换仍应 confirmed。
  v1.4 的矛盾先按退回纪律报告，规划者将验收修为 v1.5；没有修改 U1 共享政策。
- 未扩面修改 testing.md 或修复旧清理夹具：Owner 明确选择原 scope 集中勘误。
- U1 的“未登记”测试显式注入空表，继续覆盖登记前场景；没有删除原断言或改开关机测试预期。

## Verification

登记前在未改契约的基线上新增 49 个用例，结果 **13 failed / 36 passed**，退出 1：
字段登记断言 1 项、六组合确认 6 项、完整表权限互换 6 项为红。
登记后与 U1/B6 定向测试合跑 **111 passed**，退出 0。
首次定向命令因指定 basetemp 的父目录不存在而 **110 passed / 1 error**，退出 1；创建父目录后重跑通过，
没有改动测试行为或提高内存上限。

新增用例覆盖完整表的 APK/包 SHA/表外型号反例、单条隔离表的双向权限反例、完整表权限互换绿例、
未知权限 permission_unknown、空 APK identity_missing；原 U1 四个开关机组合测试保持原判据。

实际执行命令（pytest 由项目入口和 cgroup 内存保护 runner 执行；根 tests 与 backend 全量各使用 3 GiB、swap=0）：

```bash
./scripts/project_python.sh tools/dev/check_script_packages.py
./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main
./scripts/project_python.sh -m ruff check backend/agent/contracts/device_return.py backend/agent/tests/test_gpu_verified_classes_b7_u3_3812.py backend/agent/tests/test_onboarded_gpu_b7_u1_3810.py backend/tests/api/test_onboarded_gpu_b7_u1_3810.py
env -i PATH="$PATH" XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/test_gpu_verified_classes_b7_u3_3812.py backend/agent/tests/test_onboarded_gpu_b7_u1_3810.py backend/agent/tests/test_device_return_u1_3726.py -q --tb=short --basetemp=/tmp/b7-u3-evidence/pytest-targeted-green
env -i PATH="$PATH" XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/ -q --tb=short --basetemp=/tmp/b7-u3-evidence/pytest-agent
env -i PATH="$PATH" XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus PYTHONPATH=. ./scripts/project_python.sh scripts/run_gates.py check:quick
XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -- env -i PATH="$PATH" XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b7-u3-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py tests/ -q --tb=short --basetemp=/tmp/b7-u3-evidence/pytest-root
XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -- env -i PATH="$PATH" XDG_RUNTIME_DIR=/run/user/$(id -u) DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b7-u3-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py backend/tests/ -q --tb=short --basetemp=/tmp/b7-u3-evidence/pytest-backend-final -o faulthandler_timeout=120 --durations=5
```

已完成：脚本包 39 族等价；manifest 45 族 / 268 版本相对 origin/main append-only；Ruff 通过；
Agent 全量 **2999 passed / 158.34s**，退出 0。check:quick **16 gates** 通过，退出 0；
schema-at-head 因未配置 DATABASE_URL 跳过，不代表生产 schema 已验证。
首轮 check:quick 因本 Note 缺 Status/Class 头部退出 1，补齐后重跑通过。
根 tests 全量 **2424 passed / 18 skipped / 648.84s**，backend 隔离 PG 全量 **4461 passed / 1 skipped / 991.65s**，
均退出 0；跳过项不计为已验证。backend 首次全量加载旧的空表断言，获 scope 授权补前置后，
已向该测试进程精确发送 SIGTERM，退出 143，不计通过；最终全量使用新的 basetemp 重跑。
另完成 power-cycle 登记 AST 不变核对、runbook shell/Python 语法与相对链接检查、
只读盘点程序的 9 类替身场景（无真实 subprocess/设备访问）。
required CI 按 PR 当前 head 单独回查并记录在 PR 验证节，不以本地结果代替。没有执行清单中的生产命令。

## Revisit

U3 独立复核通过并合入后，Owner 另行授权最终集成 HEAD 的部署、发包、scan、机队热更新与 L2。
在用 GPU 计划清单与预算由 Owner 单独确认；没有重指就不能声称旧计划已升级。
GPU 登记使每个新 job 的 init 检查覆盖 GPU，生效证据按 L0–L3 收集，合入不等于生效。
UNISOC/UNKNOWN 等表外组合、过时清理夹具、存量按专项重初始化与其他设置类足迹均沿用 #3809 §8 的后续边界。
