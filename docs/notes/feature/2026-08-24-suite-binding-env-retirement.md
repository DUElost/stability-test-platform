# `STP_MTBF_EXPECTED_TESTPOINT_COUNT` 双层退役 + ADR-0030 七挂靠位收口（#404 PR-D）

Status: implemented
Class: feature

## Decision

ADR-0030 P1b 的收尾批（issue #404 拆分的 D）：fleet 单值 expected 旋钮退役，
不允许「注入新基准」与「fleet 单值旧基准」跨版本并存。

1. **控制面摘键**：`STP_MTBF_EXPECTED_TESTPOINT_COUNT` 移出
   `_FLEET_ENV_KEYS`（agent_env_sync.py）——hot-update 不再下发，即使控制面
   env 残留设置也不推（测试锁定）。这是评审 #400/#404 定性的「正确性悬崖」：
   fleet 单值在第二套套件上线当天即系统性出错，且错得无声。
2. **脚本侧新版本**：`mtbf_check` v1.3.0——`expected_testpoint_count` 只读
   STP_STEP_PARAMS 注入（托管绑定由 dispatcher 自动注入），删除 env 回落；
   `dead_grace_cycles` 的 host 级 env（STP_MTBF_DEAD_GRACE_CYCLES，不在退役
   范围）保持 param_or_env 不动。v1.2.0 按 ADR-0020 版本不可变保留原读取。
3. **运维文档**：mtbf-api.md §1.5 重写为「P0→P1b 配置通道」现状口径——
   注入优先、host 级手工键清单、双层退役语义、`.env` 历史残留行的处置说明
   （对绑定 Run 无影响：注入优先；新脚本直接忽略）。
4. **七挂靠位 v1.5 收口**：ADR 头部状态行 + 修订记录新增 v1.5 行（P1b 实施
   记账）、adr README 清单行/里程碑行、CLAUDE.md 决策表、DOC-MAP 两行
   （设计文档「草案」→已实施、mtbf-api「占位」→定稿）全部刷新；reviews/
   历史快照按惯例不回改。顺手修正 mtbf-api.md §2 软删行描述（守卫已落地）。

## 放弃的备选

- **只摘键不动脚本**：拒绝。merge_env_overrides 只增改不删行——已下发过该键
  的 host `.env` 会永久残留旧值，v1.2.0 脚本会继续消费陈旧基准；新版本只读
  注入才真正切断。两层缺一不可（issue #404 第二条评论的核验结论）。
- **顺带退役 `STP_MTBF_PROJECT`**：拒绝。它从未进 `_FLEET_ENV_KEYS`，本就是
  host 级手工键；绑定 Run 有注入的 project 覆盖，无绑定的 P0 存量仍靠它指目录
  ——退役它会破坏存量兼容，超出 #404 范围。
- **给 mtbf_check v1.3.0 配 seed 迁移**（flash_firmware 先例）：不需要。
  flash 种子迁移是为了 default_params 预置；mtbf_check 托管参数走注入通道、
  default_params 恒空是既定约定，catalog scan 发现新版本目录即注册。

## 如何验证

- `test_agent_env_sync.py::test_mtbf_expected_testpoint_count_retired_from_fleet_sync`
  （翻转自 propagate 测试）：控制面设值也不下发。
- `test_mtbf_scripts.py::TestCheckV13ParamsOnlyExpected` ×3：注入仍生效 /
  env 残留被忽略（expected=0 只报绝对数）/ 无注入安全降级。
- 全部既有 mtbf_check v1.2.0 测试不改一行仍然全绿（ADR-0020 已发布版本
  行为不变的回归证明）；agent tests + agent_env_sync 测试全绿，ruff 干净。
- 部署注意：新版本需随发布同步到各 host 脚本根并由 catalog scan 注册后，
  引用 v1.3.0 的 Plan 步骤方可派发（ADR-0020 常规流程）。

## 边界与何时重议

- 引用 ≤v1.2.0 的存量 Plan 若无绑定且 host 残留 env，行为与退役前一致
  （读残留值）——升级步骤版本 + 删手工行才是彻底清理；fleet 层面已无下发源。
- 真机冒烟（ADR-0030 D6 总验收信号：init trace `suite_sha256` == 门禁比对
  sha；外部 agent 仅凭 API 完成 导入→改→导出→派发 全程有审计）待 P1c
  CLI 一并执行后回写 ADR 修订记录。
- P1c（CLI + 文档定稿 + 状态传播终版）另起 PR。
