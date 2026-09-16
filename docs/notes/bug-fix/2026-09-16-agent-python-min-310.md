# Agent 运行时强制 Python >=3.10（python-dotenv>=1.2.3）

Status: implemented
Class: bug-fix

## Decision

Agent `requirements.txt` 已抬到 `python-dotenv>=1.2.3,<2.0`，该版本**硬性要求 Python >=3.10**。
旧文档仍写 3.8+，且安装/热更新在 pip 前没有解释器下限，于是老主机（系统或既有
venv 仍是 3.9）会在 deps refresh 时直接炸，或装出不可用环境。

本单把下限做成 **fail-closed**，并改文档口径：

1. **`install_agent.sh`**：打印版本后立刻用 portable awk 判 `major.minor >= 3.10`，不达标
   `exit 1`（在创建用户/venv **之前**）。`pip install` 前再查
   `"$INSTALL_DIR/venv/bin/python"`（覆盖「系统已升、旧 venv 仍 3.9」）。
2. **`host_updater.py` `_build_remote_script`**：在 `NEED_PIP=1` 的 pip 前用同一
   `sys.version_info >= (3, 10)` 闸门；失败则 `STP_DEPS_REFRESHED=0` + `exit 1`，
   **不 restart**（与 pip 失败路径一致）。
3. **文档 / 注释**：`DEPLOY.md`、`docs/wsl-linux-agent-setup.md` 的 3.8+ → 3.10+；
   `requirements.txt` 在 dotenv 上行注明 runtime floor。

不降级 dotenv：下限跟依赖真相对齐，而不是用旧包掩盖主机解释器债。

CI 跟进（`pr-agent-tests`）：`tests/test_remote_script_privilege_paths.py` 沙箱原先只
stub `venv/bin/pip`、没有 `venv/bin/python`，健康 wrapper 场景在新闸门处
`No such file` → 被当成 <3.10 失败。fixture 改为 `symlink_to(sys.executable)`，
与「健康主机 venv 已是 >=3.10」的场景对齐；闸门本身不削弱。

## Alternatives

- **A. 降级 `python-dotenv` 到仍支持 3.8/3.9 的版本**——否。依赖抬升是既定方向；降级只
  推迟问题，且与其他抬升依赖可能再撞墙。
- **B. 只改文档、不加运行期闸门**——否。热更新仍会在 pip 阶段失败，且错误信息不如前置闸门清晰。
- **C. 热更新自动 `rm -rf venv && python3 -m venv`**——否（本单范围外）。重建 venv 有权限/
  停服/路径耦合；本单先 fail-closed 并给出明确 ops 动作，重建留给运维窗口。
- **D. 仅检查系统 `python3`、不查 venv**——否。现场常见「系统已升、venv 仍旧」；venv 闸门才是 pip 真入口。

## Verification

```bash
TESTING=1 JWT_SECRET_KEY=test-secret \
  python -m pytest backend/tests/services/test_host_updater.py -q --tb=short
TESTING=1 JWT_SECRET_KEY=test-secret \
  python -m pytest tests/test_remote_script_privilege_paths.py -q --tb=short
python scripts/run_gates.py check:quick
```

期望：`test_build_remote_script_retries_pip_when_deps_marker_stale` 断言远程脚本含
`sys.version_info >= (3, 10)` 且位于 pip 之前；privilege-path 沙箱 fixture 为
`venv/bin/python` 链到当前解释器，使健康 wrapper 场景能过 pip 前闸门；
`check:quick` 通过。

手工对照（可选）：在 3.9 解释器上跑 install 开头的 awk 闸门应 exit 1；把临时 venv 指到
3.9 再触发热更新 deps 路径应看到 ERROR 且服务未 restart。

## Revisit

- **Ops（PR 外）**：仍跑 <3.10 的 Agent 主机需先升级系统 Python，再**删除并重建**
  `/opt/stability-test-agent/venv`（或等价 INSTALL_DIR），然后重装/重跑热更新。仅升级系统
  `python3` 而不重建 venv **不够**。
- 若后续 dotenv 或其他 Agent 依赖再抬解释器下限，应同步改：install awk 闸门、
  host_updater `sys.version_info` 元组、文档、本 note 的 Revisit。
- 若车队全员确认 >=3.10 且长期无 3.9 残留，可考虑把「自动重建 venv」做成受控热更新步骤
  （需另开 ADR/issue，评估停服窗口与 wrapper 权限）。
