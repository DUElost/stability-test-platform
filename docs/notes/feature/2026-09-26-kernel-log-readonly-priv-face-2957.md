# 内核日志只读窄面：wrapper `read-kernel-log` + Agent 消费（#2957 / ADR-0037 D7）

Status: implemented
Class: feature

## Decision

**给提权 wrapper 增加只读子命令 `read-kernel-log`（裁决 C 的收窄形态），不加组权限、
不新增 sudoers 行。** 动机：#2900 的两条内核证据类判据（`usb_host_controller_dead` /
`usb_link_degraded`）在机队上恒绿——Agent 以 `User=android` 运行，非特权
`journalctl -k` 的输出与「内核干净」同形（#2957 实测 rc=0 / stdout 空 / stderr 空，
同机 sudo 对照 373,600 行）。`usb_tree_empty` 只能说「整机看不见」，说不出「主控死亡」
还是「线被拔」。

### wrapper 面（`backend/agent/stp_agent_priv.py`）

```
read-kernel-log (--boot | --since-epoch <int>) [--lines <int>]
```

- **参数面只有两个整数**：互斥必选组 + `allow_abbrev=False`（`--since` 不被接受）；
  `--since-epoch` ∈ [0, now]、`--lines` ∈ [1, 5000]——解析层由 argparse 拦，
  执行层再独立复核一遍（跨层漂移不放过）。
- **固定 argv**：`/usr/bin/journalctl -k --no-pager -o cat` + 窗口 + `--lines`；任何
  路径/单元/匹配表达式（`--file`/`-D`/`-M`/`-u`/`--grep`…）**在解析层就不可达**。
- **子进程三收口**（裁决约束②：不复用 `_run`，后者继承环境且无超时/上限）：
  环境清空后只留 `LC_ALL=C` 与 `PATH=/usr/bin:/bin`、30s 超时、stdout 8 MiB 上限
  （`select` + 带上限的 `os.read`，超额直接 kill，不缓冲全量）。
- **截断/超时/非零退出**：exit 3 + `STP_READ_KERNEL_LOG_{TRUNCATED,TIMEOUT,FAILED}`
  标记，**不做部分投递**——#2957 核验实测某主机 `--boot` 373,600 行，截断后的偏小计数
  会被读成「完整样本」，比读不到更危险。
- 解析仍在 Agent 侧，wrapper 只搬运原始输出（评审材料约束④）。

### Agent 面（`backend/agent/kernel_usb_faults.py`）

- **能力探测**：`sudo -n stp-agent-priv capabilities` 含 `read-kernel-log` 才切提权面
  （进程内缓存）；旧 wrapper / 无 wrapper / 无免密 sudo → 维持非特权路径，结果仍是
  `unavailable`。两态混跑安全，wrapper 的灰度下行不改变未更新主机的行为。
- **截断样本按不可用处理**：wrapper 非零退出（含截断标记）→ 扫描返回 None，
  绝不把偏小计数当完整结果；截断单独记一条 warning 便于取证。
- **首扫从「整段 boot」改为「最近 1 小时」**（实施规格）：boot 读数必然被 8 MiB 上限
  截断，1 小时窗让首扫样本天然可用。配套把窗口样本改成 `(区间起点, 区间终点, 计数…)`
  并按终点裁剪——首扫覆盖一小时，按起点裁会下一次扫描就丢。**#2978 的「boot 累计
  冒充最近一小时」从结构上消失**（不再存在 boot 量纲的样本）。
  **代价（明示）**：进程启动前 1 小时以外的死亡不再进 L1 latch，该形态由结果层
  `usb_tree_empty`（#2902 + #2967 的账实合取）承接；触发条件见 Revisit。
- ADR-0037 升 v0.6：§2 新增 D7、§4 强控制与测试清单、§5 Revisit #5（灰度与生效判据）；
  `docs/adr/README.md` / `docs/DOC-MAP.md` 同步；`docs/operations/host-device-visibility-triage.md`
  的 L1 段按「逐台更新 wrapper 后才生效」改写。

## Alternatives

- **A：给 Agent 用户加 `systemd-journal` 组**：可读范围一步扩到整机全部 journal
  （所有单元、所有用户条目，可能含误打印的凭据），且绕开 ADR-0037 收敛的唯一提权入口。
  否决。
- **B：放弃内核日志通道**：零权限变化，但两条内核判据永久无数据，「主控死亡」与
  「线被拔」永远不可区分。否决（#2967 的结果层只是兜底，不替代 L1 归因）。
- **单独一条 `journalctl -k -o cat` 的 sudoers 行**（#2957 原文的 C）：sudoers 无法
  约束参数取值，且在 wrapper 之外新增 NOPASSWD 面，与 ADR-0037 收敛方向相反。否决。
- **保留 boot 首扫、截断判不可用**：那台 373,600 行的主机将**永久** `unavailable`
  ——截断不是偶发而是该形态的必然。否决。
- **wrapper 内做过滤/正则（如固定 grep `HC died`）**：把判定搬进 root 面，解析要维护
  两份；评审材料明确「解析仍在 Agent 侧」。否决。
- **首扫失败后回落整段 boot**：等于给「偏小计数」开口子（截断与策略回退不可区分）。
  否决。

## Verification

| 命令 | 结果 |
|---|---|
| `env -i PATH="$PATH" PYTHONPATH=. .venv/bin/python -m pytest backend/agent/tests/ -q`（CI 同款干净环境） | **2204 passed** |
| `pytest tests/test_agent_priv_read_kernel_log.py tests/test_agent_priv_parser_contract.py tests/test_agent_priv_boundary.py tests/test_agent_priv_apply_code_protection.py tests/test_agent_priv_flash_primitives.py tests/test_remote_script_privilege_paths.py tests/test_adr_index_status_2989.py -q` | **123 passed** |
| `scripts/run_gates.py check:quick` | **[OK] check:quick（16 gates）** |
| `ruff check`（改动文件） | **All checks passed** |
| `bash tools/dev/stp_agent_priv_smoke.sh`（容器冒烟，含新增第 10 段） | **ALL_OK**（`KERNEL_LOG_ARGS_REFUSED` / `KERNEL_LOG_FAILURE_MARKED`） |

用例覆盖（对裁决与实施规格逐条）：

- wrapper：`--boot`/`--since-epoch` 互斥必选、缩写拒绝、`--file/-D/-M/-u/--grep` 等
  选项解析层不可达、非整数拒绝、执行层复核负数/未来时间/行数超界、固定 argv 白名单、
  子进程环境清空与 locale 钉死（`PAGER`/`SYSTEMD_PAGER` 不泄漏）、截断（500 行上限桩）/
  超时（0.3s 桩）/非零退出/`journalctl` 缺失四条失败面都 exit 3 + 标记且 **stdout 为空**；
- Agent：能力探测四种形状（含子命令/缺子命令/非零/无 sudo）、提权 argv（含 `--boot`
  与 `--since-epoch` 两种）、非提权 argv 不变、截断 → `None` + warning、空窗探针同走
  提权面、首扫 = `now-3600`、窗口外旧计数被终点裁剪、latch 由窗口样本给出；
- 契约：`selftest`/`capabilities` 的 parser↔契约表校验自动覆盖新子命令。

## Revisit

- **灰度生效**：wrapper 不随热更新下发（D3 同款代价）——逐台 `update_agent.yml`（1 → 5
  → 全量），判据 = `stability_host_kernel_log_channel{state="unavailable"}` 回落 +
  `StabilityUsbKernelLogChannelDark` 回落。更新前主机行为不变。
- **首扫 1 小时窗的代价**：若出现「agent 重启前 >1h 的主控死亡」漏归因、且结果层
  `usb_tree_empty` 未覆盖的实例，评估给首扫加一次**有界** boot 摘要路径（须保留
  截断→不可用判据）。
- **独立安全评审**：#2957 的裁决核验由起草者按 owner 授权完成（非独立评审）；若团队
  要求，实施 PR 上另请评审人，意见冲突时以评审意见为准并回写 #2957 / ADR-0037。
- **`--lines` 上限**（5000）与首扫窗（1h）在真实数据上按误报/漏报回看。
