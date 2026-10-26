# B7 U1：共享归还契约与控制面接入 GPU

Status: implemented
Class: bug-fix

## Decision

Refs #3809，Refs #3810。依据 [B7 方案 v1.2](https://github.com/DUElost/stability-test-platform/issues/3809)
§3.1 第 1–5 条、§3.2 第 4–6 条与 §4 U1 有限验收清单实现 G3。
基线为 `origin/main@ed21b906d35000012210d20584ffc8c27699aafd`。

`ONBOARDED["gpu"]` 登记循环脚本与 instrument 进程、平台自产循环脚本文件、飞行模式与
Wi-Fi 四项足迹，解除为 `gpu_disarm@1.0.0`，只收取最低版本为 `gpu_finish@1.1.0`。
`verified_device_classes` 保持空列表；测试注入的组合只存在于测试进程。
`FAMILY_STEPS["gpu"]["disarm_onboarded"]` 指向新解除族，历史 `disarm` 仍为 `gpu_finish`。

工具身份字段集中在共享契约的 `PROBE_TOOL_IDENTITY_FIELDS`，按专项选择。
开关机继续使用原 `autotesttool_apk_sha256` 字段及四个可信组合，GPU 使用宿主测试 APK 的
`apk_sha256`（与现有 `gpu_setup` metrics 字段同名）。U2 的 evidence 使用此字段。
`FINISH_COLLECT_ONLY_VERSIONS` 改为按脚本名登记：两个 finish 都只有精确的 `1.1.0` 为 collect；
1.0.x 为 disarm，缺失、不可解析或未登记的其它版本为 unknown_legacy_disarm。

GPU 解除墙钟通过 callable 计算：`max(0, wait_device_online_seconds) + 600`，默认等待 600 秒、
默认墙钟 1200 秒。零值沿用开关机参数惯例取默认等待。额外 600 秒覆盖最多二十次每次有界
30 秒的身份读取、停止、设置恢复及四项回读。U2 的最终包需核对其最坏执行路径落在这个预算内。
本单元只登记 GPU 解除预算；没有为 GPU 收取步骤另加方案未要求的墙钟约束。

`PROBE_READBACK_VALUES` 只包含方案已有五种值。共享政策与 Engine 对显式未知回读值判 failed，
包括没有嵌套 evidence 的成功输出；平台中断、失租和未开始执行的原优先级保留。

调用点复查：控制面 `evaluate_probe_entry` 已调用共享政策；Plan 的创建、更新与追加链尾已读取
同一 `ONBOARDED` 对象；Agent `run_init_checks` 与 `registered_step` 已按登记专项运行；
`DISARM_SCRIPT_NAMES` 已取历史与接入解除名称的并集。这些消费端复用原实现，无新增入口、
状态、表、env 或迁移。其他 job 占用检查继续由原 `ReturnDebtConsumer` 和租约路径承担。

## Alternatives

- 将工具身份和只收取版本集中为按专项 / 脚本索引的共享登记表，避免为第二专项复制开关机分支。
  这是对 B6 单专项参照实现的数据组织调整，没有改变方案判据或既有开关机可信组合。
- B6 的冻结包设备夹具只提供开关机包和设备 I/O，本轮显式把该夹具的登记表限定为开关机；
  原测试断言保持不变。B7 独立测试验证两个专项都会进入 init，未接入专项不会进入。
- GPU 解除脚本、收取脚本和模板 pin 由 U2 实施；本单元没有修改任何已发布脚本、manifest 条目或
  default_params，也没有以测试组合替代生产验证。

## Verification

所有 pytest 均经项目 Python 入口和 `scripts/run_pytest.py`，由 runner 验证不高于 6 GiB、swap=0 的
cgroup。Agent 使用 `env -i`；backend 未设置 `TEST_DATABASE_URL`，由 conftest 创建 testcontainers PG。

修复前直接在基线代码运行新增测试：

- Agent：17 failed、10 passed，退出 1。红例包含 GPU 未登记、GPU 自有身份字段、只收取版本、
  未知 / 空版本、未知 readback 的兼容路径、GPU 墙钟、init 登记与占用路径。
- backend：14 failed，退出 1。Plan 三入口 × 三类解除配置缺口均返回成功而被反例拦下；
  缺失 / 不足墙钟、有效参数预算和控制面 GPU 身份核验同样为红。
- 在补齐显式 null 回读与真实 Engine init 入口后，最终新增 Agent 测试以两份生产源码的基线字节
  重跑红例；恢复实现源码逐字节相同，再跑 27 passed、退出 0。红例驱动退出 0。
- 开关机四个可信组合及修改任一工具身份值的反例在新测试中保持绿；其它矩阵中的原有安全行为
  作为正向对照保留，不把其修前绿色描述为缺陷。

修复后定向：Agent 初轮 169 passed，最终新增矩阵 27 passed；backend 89 passed
（包含原 B6 Plan、墙钟及 U8 政策回归）。

通用门禁命令与结果：

```sh
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh tools/dev/check_script_packages.py
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh tools/dev/check_tool_manifest.py --base origin/main
XDG_RUNTIME_DIR="/run/user/$(id -u)" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -- env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_pytest.py backend/agent/tests/ -q
env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b7-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py backend/tests/ -q
XDG_RUNTIME_DIR="/run/user/$(id -u)" DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$(id -u)/bus" systemd-run --user --scope -p MemoryMax=3G -p MemorySwapMax=0 -- env -i PATH="$PATH" PYTHONPATH=. TESTING=1 JWT_SECRET_KEY=b7-isolated-test-only ./scripts/project_python.sh scripts/run_pytest.py tests/ -q
env -i PATH="$PATH" PYTHONPATH=. ./scripts/project_python.sh scripts/run_gates.py check:quick
```

- 脚本包：38 族树与最新登记等价，退出 0。
- manifest：44 族 / 266 版本，相对 origin/main append-only，退出 0。
- check:quick：16 gates 通过，退出 0；未配置数据库，schema-at-head 明确跳过，不视作迁移验证。
- Python 3.11 容器中以当前用户运行 `python -m compileall -q backend tools scripts tests`，退出 0。
- 开关机 FAMILY_STEPS、ONBOARDED 子树及两项墙钟函数的 AST 与基线相同。
- 根测试初次启动因缺用户总线环境而退出 1，pytest 未运行；补齐同一用户的总线路由后以原定
  3 GiB 硬顶重跑，没有放宽内存限制。
- Agent 最终全量：2913 passed，退出 0（3 GiB 硬顶）；此前首轮为 2908 passed，最后五项回读
  变体及真实 Engine init 补齐后重跑。根 tests 全量：2424 passed、18 skipped，退出 0。
- backend 全量：4437 passed、1 skipped，退出 0（testcontainers 隔离 PG，17 分 39 秒）。
- `ai_work.py drift --strict` 退出 1：advisory 只提示与 #3804 工作面的顶层目录 docs 相同；
  已按实际六个路径收窄 scope，`status --risk` 没有文件重叠或 declaration-drift，不改他人记录。
- required CI：pending，以 Draft PR 最终 HEAD 的服务器检查结果为准。

## Revisit

PR 保持 Draft，交规划者与独立复核者核对。生效仍依 #3809 §5 的整批闸门：U1 / U2 复核与最终
集成 HEAD 全量测试、控制面部署、GPU 新版本发布与 scan、Agent canary 后热更新、Owner 确认重指。
Agent 加载新登记后，每个 job 的 init 都检查 GPU；U2 包未发布时按现有 package_unavailable
失败，不能把 U1 单独部署当作整批激活。本轮没有执行生产激活或真机验证。

GPU 可信组合登记留待 U2 最终包的真机证据经规划者核对；U2 同时复核身份字段与最坏墙钟。
方案之外观察：无。
