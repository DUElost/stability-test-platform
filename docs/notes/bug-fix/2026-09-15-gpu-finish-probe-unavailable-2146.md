# gpu_finish v1.0.5：清理验证补「探测不可用」态（#2146）

Status: implemented
Class: bug-fix

## Decision

- **缺陷**：#894 交付的 `gpu_finish v1.0.4` 只覆盖三态中的两态。`_lib.adb_shell()`
  只取 stdout、**丢弃 rc**（`_lib.py:148`），探测返回空串时 `_cleanup_device_script`
  判「没有 REMAINS」→ **静默放行**并写 `cleanup_verified: true`（`gpu_finish.py:118`）。
  设备离线 / 超时 / 重启窗口都会命中这条假绿路径。
- **取向修正（本次的核心判断）**：「找不到证据」不能等于「干净」。完成态是
  **可证伪的正面信号**——探测必须同时满足 rc=0 且输出明确（`CLEAN`）。v1.0.5 用
  `adb()`（返回 `(rc, stdout, stderr)`）分四支判定：
  `rm` rc≠0 → 「清理命令失败」；探测 rc≠0 → 「清理验证不可用」；
  输出既无 `REMAINS` 也无 `CLEAN` → 「清理验证输出异常」；`REMAINS` → 「仍存在」。
- **同族对照（已核对，无需改）**：`monkey_teardown v1.0.2` 本就检查 rm/探测 rc；
  `powercycle_finish` / `sleep_finish` 的 prefs 回读走 `get_prefs_xml()` 失败返回空串，
  但调用方 `_verify_stop_flags()` 把「空」当「未验证」而 raise——**同为 fail-safe**。
  只有 gpu_finish 把「无输出」当成「干净」，属孤例。
- **seed 迁移 `p6q7r8s9t0u1` 只 INSERT v1.0.5、不停用 v1.0.4**：v1.0.4 仍被 4 个
  Plan（P24/27/29/36）的 teardown 步骤引用，迁移内停用会被 #942 引用守卫 abort。
  退役顺序固定为：**分发 → 重指 PlanStep → API 置 `is_active=false`**。
- **单测补盲区**：v1.0.4 的用例里 fake responder 永远返回 `CLEAN`/`REMAINS`，
  没有「adb 失败」分支——本单补 6 例（含 rc≠0 与空输出），并保留 v1.0.4 用例不动
  （已发布版本不可变）。

## Alternatives

- **在 `adb_shell` 里加 stderr 判定（不拿 rc）**：脏——`adb` 失败时 stderr 文案千差
  万别（offline / timeout / 无 device），rc 才是权威信号。否决。
- **把探测写成 `[ -e X ] && echo REMAINS || echo CLEAN` 依赖远端的 OR 语义**：rc 仍
  不可靠（adb 层失败时整条命令的 rc 未必反映「未执行」）。改为消费 `adb()` 的 rc。否决。
- **原地改 v1.0.4**：违 ADR-0020/0039 版本不可变（且已分发到 48 台主机）。否决。
- **seed 里一并停用 v1.0.4**：会被引用守卫 abort，且会让部署失败——退役是重指之后的
  独立运维动作。否决。
- **在 v1.0.5 里复制 monkey 的 `subprocess.run` 写法**：`_lib.adb()` 已在同目录提供
  rc/stdout/stderr，复制只会多一份要维护的 adb 调用面。否决。

## Verification

| 项 | 命令 | 结果 |
|---|---|---|
| v1.0.5 用例（含 v1.0.4 旧用例） | `./scripts/run_pytest.sh backend/agent/tests/test_teardown_cleanup_894.py -q` | **17 passed** |
| 反事实 1（变异：去掉 rc 判定） | 注入变异重跑 | **2 failed**（rm rc≠0 / 探测 rc≠0 用例转红），还原后 17 passed |
| 反事实 2（变异：空输出静默放行） | 注入变异重跑 | **1 failed**（`清理验证输出异常` 用例转红），还原后 17 passed |
| agent 全量 | `./scripts/run_pytest.sh backend/agent/tests/ -q` | **2014 passed** |
| 门禁 | `.venv/bin/python scripts/run_gates.py check:quick` | `OK (10 gates)` |
| 空库迁移（隔离 `postgres:16` 容器，端口 55433/55434，**非生产库**） | `alembic upgrade head` → `downgrade -1` → `upgrade head` | 通过且幂等；`content_sha256` 与磁盘一致（`5789894e…`） |
| schema 对齐（**从仓库根**跑） | `python -m backend.scripts.check_schema_sync` | `rc=0`（5 项基线、0 新增漂移） |

> 踩坑记录：`check_schema_sync` 必须在**仓库根**执行（`backend/` 下跑会
> `ModuleNotFoundError: No module named 'backend'`，rc=1 是 cwd 错误而非漂移）。

## Revisit

- **真机三态仍留待**（#894 的留待项）：本单只把代码补到三态齐全，设备台架可用时仍需
  在真机实跑「删除成功 / 残留转红 / **探测不可用转红**」——本态的构造方式是把设备
  在 teardown 窗口内断连（或跑之前 `adb kill-server`）。
- **产线顺序**：分发 v1.0.5 → 重指 P24/27/29/36 → 之后 v1.0.4 才变零引用，方可 API
  退役。若跳步（先退役）会被引用守卫挡下。
- **同类审查建议**：本次核对范围只有 gpu/monkey/powercycle/sleep 四个 teardown 脚本。
  其余脚本里若还有「用 shell 输出存在性判成功」的写法（丢 rc），应按同一取向复查——
  优先看所有以「探测命令输出」判定完成态的分支。
