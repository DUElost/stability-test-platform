# #2755 关单：链触发最小稳定窗已落地

Status: implemented
Class: bug-fix

## Decision

关闭 [#2755](https://github.com/DUElost/stability-test-platform/issues/2755)。票面选方向 2
（最小稳定窗）：父 run `ended_at` 后 `CHAIN_TRIGGER_SETTLE_SECONDS`（默认 180s）内
跳过即时触发，由 reconciler 补偿。

| PR | 内容 |
|---|---|
| #2760 | 即时路径吃 settle 窗 |
| #2765 | 补偿路径 `respect_settle=True`（修有效窗≈60s 缺口） |

主干 `plan_chain_trigger` / `job_terminalization` 两路均带 `respect_settle=True`。

## Alternatives

- **等下一 monkey 窗现场再关**：弃——代码与回归已合入；现场复验属部署后观测，
  不阻塞关单。若复发另开单。

## Verification

- `#2760` / `#2765` MERGED；链族测试含 settle 用例
- 无未合跟进 PR

## Revisit

控制面需跑到含上述提交的版本后，下一链段才吃满 180s 窗。
