# SSH 换钥需显式确认 + 指纹审计（#908）

Status: implemented
Class: bug-fix

## Decision

#908（R02-R02，设计风险）：`trust_host_key` 在建主机/地址变更时自动
`ssh-keyscan` 并**无条件覆盖** known_hosts 旧条目——后续连接走 RejectPolicy，
但首次信任/换钥无人工确认，中间人前提下可被静默换钥。测试此前明确锁定
「替换旧 key」的行为。

修复（issue 验收「换钥需显式确认，自动覆盖需可审计」）：

- `trust_host_key(..., allow_replace: bool = False)`：
  - 无既有条目（首次信任）或与扫描结果相同（同键重扫）→ 行为不变；
  - **既有不同密钥且未确认 → 拒绝替换**：返回
    `host key changed ... explicit replace required`（含新旧 `SHA256:` 指纹），
    **known_hosts 文件保持不动**；
  - 显式 `allow_replace=True` → 替换并返回
    `replaced old=<fp> new=<fp>`（供调用方审计）；
- `host_key_fingerprints()`：known_hosts 行的 SHA256 指纹（ssh-keygen 风格），
  拒绝原因与审计文本均可比对；
- API：`HostCreate` / `HostUpdate` 新增 `replace_host_key: bool = False`；
  create/update 透传 `allow_replace`；响应 `host_key_trust` 三态
  `ok / changed / failed: <reason>`；
- **审计**：只有确实执行替换时写 `host_key_replaced`（details 含
  ip/port/新旧指纹）——首次信任与拒绝替换不写；
- 运维文档《linux-agent-ansible-runbook》新增「SSH 主机密钥信任与换钥」节：
  明示 TOFU 信任模型、首次信任无边界的现实、换钥确认与审计流程、以及
  「为什么不全自动」。

与 ADR-0033 的关系：无直接约束——这是控制面到宿主的部署通道（SSH），
不是外部工具接入契约；与 #1263（Ansible 严格主机校验）互补：那条管
**连接期**校验，本单管**信任建立期**的换钥同意。

## Alternatives

- 只写文档不收紧（验收允许的弱形态）：中间人风险原样保留，且「自动覆盖需
  可审计」也无从落地——收紧的成本只是重装主机后多传一个参数；
- 全自动 + 只记审计：审计只能事后发现，无法阻止一次已被利用的换钥；
- 强制人工核对指纹（TOFU 改为手工 pin）：与 UI 一键建主机的可用性冲突，
  且首次信任仍是无指纹通道——换钥同意 + 审计是当前边界下的最优折中。

## Verification

- `pytest backend/tests/test_ssh_security.py`：11 passed——拒绝静默替换
  （文件不动 + 原因含双指纹）/ 显式同意替换（返回 `replaced old/new`）/
  同键重扫幂等 / 指纹形状与稳定性；
- `pytest backend/tests/api/test_hosts.py`：33 passed——建主机未确认 →
  `host_key_trust="changed"` 且文件不动；`replace_host_key=true` → 替换成功
  + `audit_log` 出现 `host_key_replaced`（details 含 SHA256 指纹）；
- `pytest backend/tests` 全量 2179 passed；ruff 全绿。

## Revisit

- 首次信任无人工指纹核对通道（TOFU 固有边界）——若未来引入带外指纹分发
  （装机时打印指纹/配置管理下发），可把首次信任也收紧；
- 现有 production known_hosts 中若已有旧条目，下一次换钥需要管理员显式
  确认一次（预期行为）；
- `HostUpdate` 只在 IP/端口变化时重扫——地址不变但宿主重装的场景，管理员
  改回同地址不会触发重扫；如需该场景的显式重扫入口（如「重新信任」按钮）
  另立单。
