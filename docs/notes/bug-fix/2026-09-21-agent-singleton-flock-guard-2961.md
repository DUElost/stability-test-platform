# Agent 同机单实例守卫：入口 flock，第二个进程立即失败（#2961）

Status: implemented
Class: bug-fix

## Decision

**守卫落在进程入口 `run_agent_application()`**（`main` 的唯一调用点，docstring 写的就是
"one process, one AgentApplication"），不在 systemd unit、也不在 `agentctl.sh`。
2026-07-27 的事故里，第二个进程是手工敲的 `venv/bin/python -m agent.main`（19/20 台 host
同一时刻批量执行）——守护进程与 agentctl 都管不到它，**入口是唯一所有启动方式都会经过的点**。

> 为什么不是 `main.py` 里：`backend/agent/main.py` 有 34 行 god-files 封顶（#736），
> 加守卫会撑到 38 行。封顶的指引是「新逻辑下沉」，而 `run_agent_application()` 本就承担
> 入口语义，故落在这里；`main.py` 本次零改动。

**机制选 `flock(LOCK_EX|LOCK_NB)`**（`startup_guards.enforce_single_instance`），
不选 pidfile：

- 内核在进程退出时自动释放 → 锁文件残留不阻塞下次启动，也没有「陈旧 pid 被复用」的误判；
- 锁 fd 带 `O_CLOEXEC`：Agent 会派生 adb fork-server 与脚本子进程，若子进程继承锁 fd，
  它在父进程退出后多活一会儿就会顶住锁，把 systemd 的 `Restart=always` 拖成启动失败。

**锁文件放 `$INSTALL_DIR/logs/agent.lock`（`config.LOG_DIR`）**，不放 `BASE_DIR`：开发模式下
`BASE_DIR` 是仓库根，会往检出里写文件（#1987 那类「他人未跟踪文件阻塞检出」的同类风险）；
`/logs/` 已 gitignore，且 agent 的 logrotate 配置点名 `agent.log` / `agent_error.log`
两个具体文件，不会轮转掉锁文件。

**失败方向分两档**：

| 情形 | 行为 | 理由 |
|---|---|---|
| 锁文件建不出来（目录不可写等） | warning 后**继续启动**（fail-open） | 守卫是纵深防御，不能因文件系统问题让整机 Agent 起不来 |
| 锁被别的进程持有 | CRITICAL（含 `holder_pid`）+ `sys.exit(1)` | 这正是要断言的故障态：同 HOST_ID 双实例会让 coordinator 心跳全被拒 |

**已知取舍（有意）**：若先跑起来的是游离进程，systemd 托管实例会启动失败并进入
start-limit，服务显示 failed。这比 7 月的「两个实例都活着、心跳互相覆盖、只留一条
`coord_hb_agent_instance_stale` 告警被淹没在日志里」要好——故障从静默变为显式，
`systemctl status` 与日志里直接给出持有者 pid。

## Alternatives

- **pidfile + pid 存活检查**：陈旧 pid 复用会误判；`kill -9` 后需人工清文件；且同样是
  「进程退出要有人收拾」。flock 让内核收拾。
- **`systemctl is-active` 前置检查**（#107 评论里的另一选项）：只覆盖 systemctl 启动路径，
  而事故恰恰是手工启动的那份绕过了 systemd——是必要不充分的半边，不能单独用。
- **安装链补 pkill 游离进程**：破坏性动作（有误杀正常实例的风险），且只在安装时生效，
  事故窗内没有安装动作。非必要不动手杀进程。
- **等 backend 通知后退出的 self-fencing**：7 月现场两个实例**都被**判 stale（fencing 依据的
  `last_agent_instance_id` 本身在振荡），backend 无法指出「谁才是正统」，自救不成立。
- **加 `STP_AGENT_LOCK_FILE` 逃生阀**：暂不加——当前没有真实需求，加了等于给绕过留口子。
  真出现「必须临时跑第二个 Agent 做诊断」时再补，并让它同时进告警面（见 Revisit）。

## Verification

| 命令 | 结果 |
|---|---|
| `python -m pytest backend/agent/tests/test_agent_singleton_2961.py -q` | **5 passed** |
| `env -i PATH="$PATH" PYTHONPATH=. python -m pytest backend/agent/tests/test_agent_singleton_2961.py -q`（CI 同款干净环境） | **5 passed** |
| `python -m pytest backend/agent/tests/ -q`（全量） | **2182 passed**，86.9s |
| 变异验证①：`sys.exit(exit_code)` → `return fd`（不再拒绝） | `test_second_acquisition_is_refused`、`test_cross_process_refusal` **变红** |
| 变异验证②：删掉 `run_agent_application()` 里的 `enforce_single_instance()` | `test_entry_takes_lock_before_running_application` **变红**（SourceGuard 报 FormRegression） |
| 还原后复跑 | 5 passed —— 用例有判别力，非恒真 |

用例覆盖的形状：同进程二次取锁被拒、**跨进程**被拒（子进程真持锁，且锁文件里记的是持有者
pid）、持有者被 kill 后锁自动释放、锁路径不可用时 fail-open、入口顺序（`enforce_single_instance()`
早于 `AgentApplication().run()`）。`test_agent_application_736.py::test_run_agent_application_constructs_and_runs`
同步把守卫打桩（只验「构造 + run」接线），免得在跑着开发态 Agent 的机器上取锁失败把 pytest 进程
`sys.exit` 掉。

## Revisit

- **覆盖边界**：锁按安装目录区分。同机存在**另一份安装目录**的 Agent（非常规部署）仍可叠加；
  若真出现，改为按主机全局路径取锁（如 systemd `RuntimeDirectory=` 下的固定路径）。
- **systemd start-limit 副作用**：若线上出现「游离进程顶住锁 → 托管实例反复失败并进 failed」，
  考虑让托管实例优先（unit 前置抢占 + 接管），前提是真出现而非假设。
- **逃生阀**：一旦有「临时第二 Agent 诊断」的真实需求，加显式 env 开关并让它进告警面，
  而不是移除守卫。
- **顺带**：本次未动 `agentctl.sh` / `install_agent.sh`。若日后要在启动脚本侧给出更友好的
  提示（"已有 Agent 在跑（pid=N），如需重启用 systemctl restart"），应建立在守卫之上而非替代它。
