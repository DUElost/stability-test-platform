# 待裁 issue 第四轮：ADR-0056/0057 裁决、#2957 核验、授权留痕

Status: implemented
Class: architecture

## Decision

2026-09-26 owner 对上一轮留给人工的全部事项回复「均授权你进行推进」。本轮在云端会话能力范围内推进如下；
需要生产访问的两项（#3341 执行、采集清单）本会话**无法执行**，只做授权留痕。

| 对象 | 本轮结果 |
|---|---|
| ADR-0056 终态事实层（#3326） | **Accepted v1.0**：F1–F5 全部采起草取向（分表；适配器归一签名；租约时长作 MTBF 分母；存量尽力回填并标注来源；缺事实行即跳过删除并告警），见 ADR §8 |
| ADR-0057 设备退役（#2962） | **Accepted v1.0**：E1–E5 全部采起草取向（再上报保持退役并单次告警；有活跃 Job / 租约时 409；存量人工确认批量退役；陈旧 30 天给退役建议；设备面告警排除已退役），见 ADR §7 |
| 语义归属表 | 新增 `terminal-fact-layer`、`device-retirement` 两个 key，两份 ADR 的 `归属域` 由 n/a 改为对应 key |
| #3347 | 按授权代 owner 确认三项（ADR-0038 A2、ADR-0052 D6、ADR-0023 D6），关单；#3349 由此放行 |
| #2957 | 按评审核对清单逐项读码核验（见下），**采纳 C（wrapper 只读子命令）**，并补充两条实施约束。**说明：这是起草者按 owner 授权完成的核验，不是独立安全评审**；若团队要求独立评审，实施 PR 应另请评审人 |
| #3341 | 记录 owner 授权执行一次性恢复；执行仍需有生产权限的人按 issue 内命令进行 |
| 采集清单 | 无变化；需有生产权限的人执行 |

### #2957 核对清单逐项结果（`main@d8e3ef2` 读码）

| 核对项 | 结论 | 依据 |
|---|---|---|
| 参数校验能否被绕过 | 可控，但实施须加两条约束 | 现有 wrapper 以 argparse 解析（`stp_agent_priv.py:1100-1106`），子进程以列表 argv 调用、不经 shell，拼接注入不可达。约束①：新子命令的解析器设 `allow_abbrev=False`，避免 `--since` 之类缩写被接受；整数参数做范围校验（`since-epoch` ≥ 0 且不晚于当前时间，`lines` 1–5000） |
| 环境注入（`PAGER` / `SYSTEMD_*`） | **现有 `_run` 不满足，须新写调用** | `_run`（`stp_agent_priv.py:204-215`）继承调用者环境且无超时。约束②：新子命令不得复用 `_run`，改为显式 `env={"LC_ALL": "C", "PATH": "/usr/bin:/bin"}`、`timeout=30`，并配合 `--no-pager`；生成的 sudoers 没有 `env_keep`（`:562-583`），sudo 默认 `env_reset` 为第二层防护 |
| 超时与截断对心跳线程的影响 | 无影响 | 扫描在守护线程 `kernel-usb-scan` 中运行（`kernel_usb_faults.py:215-221`），扫描器异常被捕获并记为 `unavailable`（`:222-233`），不进心跳主循环 |
| 设备序列号的可见范围 | 不外传 | 控制面只收到计数（`hc_dead` / `not_responding` / `link_errors` / `cable_suspect` / `lines`，`contracts/kernel_usb_faults.py:63-71`）与通道状态（`capacity_reporter.py:116`）；原始内核行留在主机上 |
| 回滚路径 | 成立 | 删除子命令后 `capabilities` 不再列出它，Agent 回到非特权调用；读不到即 `unavailable`，与现状一致 |

**核验中发现的一个新问题（纳入实施规格）**：#2957 评论实测 root 读 `journalctl -k --boot` 在一台主机上有 373,600 行。
评审材料里「stdout 截断 8 MiB」会让首次整段扫描被截断，计数偏小却被当作完整样本。
实施规格改为：**首次扫描不用 `--boot`，改用 `--since` 最近一小时**（同时消除 #2978 所述「整段 boot 当作最近样本」的问题）；
wrapper 在输出被截断时以非零退出码 + stderr 标记告知，Agent 将截断样本判为 `unavailable`，不当作干净结果。

## Alternatives

- **ADR-0056 / 0057 逐项另选备选**：起草取向在草案 §3 / 裁决点表里已有逐项理由，备选项的代价（运行表永不删除、自动退役、只告警不阻断）都会削弱各自 ADR 的核心不变量。
- **#2957 等待独立安全评审再裁决**：owner 已授权推进；核验结论与约束写进实施规格，独立评审仍可在实施 PR 上进行，不阻塞领单。
- **#3347 保持开放等 owner 本人勾选**：owner 已明确授权推进，三项的依据均为读码确定的事实。

## Verification

- `python tools/dev/check_governance_surface.py --check` 与 `python -m pytest tests/test_adr_index_status_2989.py`，结果见 PR 描述。
- #2957 核验锚点见上表；未改任何代码与主机配置。

## Revisit

- ADR-0056 第 1 期实施时复核 F3（MTBF 分母）是否需要补充「脚本上报有效测试时长」切片。
- ADR-0057 实施后复核 E4 的 30 天阈值与陈旧度 7 天阈值的分工是否清楚。
- #2957 实施 PR 若获独立安全评审意见，与本表冲突时以评审意见为准并回写 #2957。
