# 心跳逐拍重读 ARTIFACT_DIGEST——no-op 稳态修复（#1943）

Status: implemented
Class: bug-fix

## Decision

ADR-0040 D3 的 no-op 稳态事实未建立：48 台中 47 台心跳不上报
`agent_artifact_digest`（生产快照 2026-09-14）。根因是**时序**：远端脚本在
重启探活通过后才写 ARTIFACT_DIGEST（刻意设计——digest 只描述健康收敛过的
状态，见 #1907 Note 的 Alternatives），而 Agent 只在进程启动时读一次
（`main.py:741`）——重启发生在写盘**之前**，新进程永远读到上一轮的值
（首装时为空）。后果：每次内容变更后多一轮冗余全量部署，协议主机长期不进
no-op 稳态。

按 issue 建议 1 修复（最小、与 D2「不做每次心跳全树重算」不冲突——72B
文件读取，非全树哈希）：

- `HeartbeatThread.agent_artifact_digest` 参数扩展为
  `str | Callable[[], str]`：callable 时**每次发送前解析**（`_resolve_
  artifact_digest`），字符串形态保留（既有调用方/测试向后兼容）；
- `main.py` 构造处改传 `lambda: read_artifact_digest()`（本地双模式导入，
  遵循 main.py 既有惯例）；
- 解析异常按空值处理（心跳路径不因 digest 读取失败判 Agent 死亡）；
- 修正 `main.py:741` 处的错误前提注释（「启动读一次即足够新鲜」不成立于
  写盘时序；启动读取保留用于身份日志）。

## Alternatives

- 写盘时机改到 restart 前 + 探活失败回写旧值——需要新的回滚语义（原
  Alternatives 否决「restart 前写」正是为避开假收敛态）；复杂度高于重读。
- 控制面忽略心跳值、每次直接 SSH 读远端文件——每台每轮一次额外 SSH 往返，
  且与「心跳上报通道」的既有信任模型分叉。
- Ansible 轨道 digest 缺口（`update_agent.yml` 不写 ARTIFACT_DIGEST）——
  收口前需先对账其 rsync 排除集与部署输入集契约（错 digest 上报比缺口更
  危险），按 issue 口径随 #1900 收口，不混入本单。

## Verification

- `backend/agent/tests/test_heartbeat_artifact_digest_reread_1943.py`
  3 passed：callable 提供方逐拍重读（同一进程内文件变化下一拍可见——
  write-digest 晚于重启的时序复现）、字符串形态向后兼容、提供方异常降级
  空值；
- 既有 `test_heartbeat_catalog_versions.py` 2 passed（无回归）；
- `check:quick` 10 gates 全绿。

## Revisit

- Ansible 入口 digest 写入随 #1900 收口（前置：排除集契约对账）；
- fleet 侧验证：修复合入 + 存量机 wrapper 升级后，心跳快照应全量上报
  digest，且连续两轮批量热更新出现 `converged(reason=digest-matched)`
  no-op（P1 稳态成立的实证口径）。
