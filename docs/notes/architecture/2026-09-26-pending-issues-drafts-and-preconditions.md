# 待裁 issue 第三轮：草案、推荐备注与认领前提

Status: implemented
Class: architecture

## Decision

2026-09-26 同日第三轮，owner 指示：第二类两个尾巴收口；第三类授权把推荐方案写成备注，并起草三份材料；第四类把所需数据写成认领前提，并整理一份采集清单。
本 PR 只加文档与草案，不改代码、不做任何裁决（两份 ADR 均为 Proposed）。

| 对象 | 本轮产出 | 状态 |
|---|---|---|
| #3066 | issue 评论：按中止来源的分支裁决表 + 采纳 C 时的实施要点 | 等数据（采集清单第 1 项） |
| #2962 B 期 | 新 ADR-0057（Proposed）：`device.retired_*` 三列、八面收口、E1–E5 裁决点 | 待 owner 裁决 |
| #3347 | issue 评论：三项均建议确认 | 待 owner 勾选 |
| #2957 | 评审材料 [`2026-09-26-kernel-log-read-privilege-review-2957.md`](2026-09-26-kernel-log-read-privilege-review-2957.md)：推荐以 wrapper 只读子命令实现 C | 待安全评审 |
| #3326 | 新 ADR-0056（Proposed）：事实层与运行层分表，F1–F5 裁决点 | 待 owner 裁决 |
| #3341 | issue 评论：一次性恢复的预检、执行与核对命令 | 待 owner 授权执行 |
| 第四类 8 单 | 采集清单 [`docs/operations/2026-09-26-pending-issues-data-collection.md`](../../operations/2026-09-26-pending-issues-data-collection.md)；各单评论写明「启动认领前提」 | 等数据 |

**与上一轮记录的一处出入**：[第二类裁决记录](2026-09-26-tier-b-issue-decisions.md) 把 #2962 的 B 期写作「ADR-0038 增补」。
起草时读到 ADR-0038 D7 明文「不做设备退役（独立议题，设备行当前无删除面，需求出现时单独提案）」，因此改为单独提案 ADR-0057，
不修改 ADR-0038 正文。两者内容一致，只是载体不同；已在 #2962 评论说明。

## Alternatives

- **ADR-0057 并入 ADR-0038 增补**：与 ADR-0038 D7 的明文冲突，且主机退役 ADR 会同时承载两个对象的矩阵。
- **#2957 按原文 C（单独一条 journalctl sudoers 行）**：sudoers 约束不了参数取值，且在 wrapper 之外新增 NOPASSWD 面，与 ADR-0037 收敛方向相反。
- **#3341 经 SAQ 重新入队**：`enqueue_sync` 依赖运行中的事件循环，从发布根 CLI 调用拿不到；同步调用幂等且结果可逐个核对。
- **第四类逐单各写采集说明**：同一个人要多次登录生产环境；合成一份清单可以一次跑完。

## Verification

- 读码锚点（`main@8dc9d82`）：`backend/services/post_completion.py:48-146`（链补偿与 RISK_HIGH 副作用）、`backend/services/plan_chain_trigger.py:582-612`、
  `backend/services/plan_run_abort.py:285-300/:414-426`、`backend/agent/stp_agent_priv.py:1-35/:562-583`、`backend/services/host_updater.py:201-215`、
  `backend/services/dedup_scan.py:559-590/:666`（`merge_started` 在锁内打印）、`backend/core/metrics.py:122-127`、各模型表名与列名。
- `python tools/dev/check_governance_surface.py --check` 与 `python -m pytest tests/test_adr_index_status_2989.py`，结果见 PR 描述。

## Revisit

- ADR-0056 / ADR-0057 裁决后，各自转 Accepted 并补登语义归属 key。
- 采集清单全部回贴后，移入 `docs/archive/`，第四类各单按数据逐单裁决。
