# RunConsole 取消收敛判据改为进程组级（#1115）

Status: implemented
Class: bug-fix

## Decision

#1115（R11-F07）：RunConsole 取消虽用 `killpg(SIGTERM)`，但「是否升级 SIGKILL」
只看 `proc.wait(timeout)` —— **父进程**。父退出、组内子孙忽略 SIGTERM 时 wait
成功即跳过强杀：界面已 CANCELED，后代继续跑。连锁后果：后代握着 stdout 管道写
端，reader 线程永远等不到 EOF，`_finalize` 不执行、run_key 不释放 —— 同 run_key
的重起会一直撞 `RunKeyBusyError`。与 R07-F02（#1003，pipeline_engine）同根因族，
收敛判据同型：**父已回收 且 整组已散**。

修复：

- `ConsoleRun` 增加 `_pgid`：**spawn 时刻**留存进程组身份（`start_new_session=True`
  下 pgid == pid，但显式 `getpgid` 一次不依赖 Popen kwargs 实现细节）。必须在任何
  wait/poll 之前 —— 父被回收（reader 线程 `proc.wait()`）后 `getpgid` 会 ESRCH，
  pid 还可能被复用，而 cancel 要处理的恰恰是「父已退出」的情形；
- cancel 的 POSIX 分支：`killpg(SIGTERM)` → `_await_group_exit`（父已回收 **且**
  `killpg(pgid, 0)` 探测整组已散；循环内 `poll()` 顺带回收僵尸父进程）→ 未收敛
  记 warning 并 `killpg(SIGKILL)` → 二次等待，仍不收敛才 error；
- pgid 拿不到（捕获失败且父已回收）时**退化为单进程 kill**：绝不用可能已复用的
  pgid 去打陌生进程组；
- Windows 分支不变（`NEW_PROCESS_GROUP` 下 terminate/kill 语义本就偏组）。

## Alternatives

- 只把 `proc.wait(timeout)` 换成组探测但不留存 pgid：父已被 reader 回收时
  `getpgid` ESRCH，cancel 大概率拿不到组身份 —— 恰好在最需要它的场景失效；
- 复用 #1003 在 `proc` 对象上挂 `_stp_pgid` 的做法：RunConsole 的 proc 生命周期
  由 `ConsoleRun` 管，把身份放在 run 上比挂在 Popen 私有属性上更顺，语义相同；
- 取消时只杀父进程组不管 reader：不行 —— reader 的 EOF 依赖后代死亡，组级收敛
  本身就是 reader/run_key 释放的前提。

## Verification

- `pytest backend/tests/services/test_run_console.py`：11 passed。新增真实进程组
  回归 `test_cancel_kills_descendants_ignoring_sigterm`：父起忽略 SIGTERM 的子孙
  后退出、子孙继承 stdout 管道 → cancel 后 run 到 CANCELED 且**同 run_key 可立即
  重起**（后代不死则 reader 等不到 EOF，该断言必超时）；另加 pgid 留存断言；
- **对照原实现验证**：回归用例在 HEAD 版本（修复前）上真实失败（5s 超时路径），
  修复后通过 —— 不是恒真断言；
- `pytest backend/tests/services/test_run_console.py test_agent_installer.py
  backend/tests/api/test_dedup_{jira,scan}_endpoints.py test_main_lifespan.py`：
  消费方全绿；ruff 干净。

## Revisit

- 与 #1003 是同一收敛原语的两处落点（agent pipeline_engine / 控制面 run_console），
  实现各自内联；若第三处再出现（如 watcher/reconciler 的子进程管理），应抽公共
  模块而不是继续复制；
- `_cancel_grace` 默认 5s，两阶段各占一次 → 最坏 ~10s；若 UI 取消超时反馈，先查
  这里而不是加重试；
- Windows 分支仍无组级探测（`terminate` 语义依赖 `CREATE_NEW_PROCESS_GROUP`），
  本单未验证 Windows 行为。
