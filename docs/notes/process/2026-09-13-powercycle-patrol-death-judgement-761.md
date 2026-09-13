# Agent Note: 记录 patrol 判死边界与挂死兜底分工（#761）

Status: implemented
Class: process
Issue: #761

## Decision

`#761` 的两项诉求都是**文档层**的，按建议写进
`docs/operations/new-specialty-onboarding-runbook.md` §5「易踩点」清单（紧邻既有的
`stall_seconds` 提示）：

1. **patrol 间隔必须小于结果文件写入间隔**（对以「结果文件 mtime 停滞」判死的脚本）；
2. **挂死 ≠ 文件停滞**：`alive=True` 恒清零 `dead_streak` 的后果是「服务活着但挂死」
   永不判死，该场景的兜底层是 **PlanStep `stall_seconds`**，二者互补不替代。

**为什么写 runbook 而不是脚本 docstring**：ADR-0020 下 `powercycle_check/v1.0.8/` 是
**已发布版本目录**，改 docstring 属内容变更 → 必须新开版本目录（v1.0.9）并走版本注册。
本单诉求是「让 plan 作者知道这条约束」，runbook 正是配置 `patrol_interval_seconds` /
`stall_seconds` 的地方；为一条注释开版本目录的收益/代价不成比例。issue 原文为
「docstring **/** runbook」，取 runbook 支。

## Alternatives

- **改脚本 docstring（新开 v1.0.9）**：不选，见上（一条注释换一次版本注册）。
- **新开一份 powercycle 设计文档**：不选。判死语义属「配 patrol 时要知道的约束」，
  与 runbook §5 的既有 `stall_seconds` 提示同处最易被读到；另开文档反而降低被读到的概率。
- **只写「patrol 间隔」那条**：不选。两项是同一处脚本逻辑（`v1.0.8:367` 的 `if alive ...`
  与 `:369` 的 mtime 停滞分支）的两面，拆开写会让人以为"文件停滞判死"能覆盖挂死。

## Verification

- **代码事实先核实**（而非照抄 issue）：`backend/agent/scripts/powercycle_check/v1.0.8/
  powercycle_check.py:366-372` ——

  ```python
  mtime = _result_mtime()
  if alive or cycles_done == 0 or result_bytes == 0 or not was_online:
      state["dead_streak"] = 0          # ← 服务在跑即清零（#761 第 2 条据此成立）
  elif mtime > 0 and mtime == state.get("last_mtime"):
      state["dead_streak"] = int(state.get("dead_streak", 0)) + 1
  ```

  与 issue 描述一致；`dead_grace_cycles` 默认 2（同文件 docstring）。
- **版本层面**：issue 记录「09-03 时版本止于 v1.0.6」，现为 **v1.0.8**（本单诉求与
  这两个版本引入的 `#1028` / `#813` 变更无关，故文档层仍未覆盖——非陈旧项）。
- **文档层**：`check:quick`（含 gov-surface）见 PR 描述；改动为 runbook 增补两条易踩点。
- **未验证（诚实标注）**：真机在 `patrol_interval_seconds` 逼近写入节奏下的实际抖动幅度
  （issue 自述为「设计脆性、稳态不触发」），本轮只固化约束表述，未做实测。

## Revisit

- 若将来把「文件停滞判死」改成「服务存活但文件停滞也判死」（即引入 hang 判死），
  本 note 的第 2 条与 runbook 对应条目需同步改写——那是一次**语义变更**，应按 ADR-0020
  开新版本目录，而不是改注释。
- 本单只覆盖 `powercycle_check` 形态；`sleep_check` / `mtbf_check` 等是否同样依赖
  「写入节奏 > patrol 间隔」，需要时按同一模板补文档（未在本轮展开）。
