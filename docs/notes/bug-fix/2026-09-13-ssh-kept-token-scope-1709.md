# #1709 修复：非 22 端口信任的删除集与比较集同域

Status: implemented
Class: bug-fix

## Decision

`backend/core/ssh_security.py` 的 `trust_host_key` 重写段，`kept`（删除集）从

```python
kept = [ln for ln in existing
        if _host_token(ln) != ip
        and (port_token is None or _host_token(ln) != port_token)]
```

改为与比较集**同域**：

```python
kept = [ln for ln in existing if _host_token(ln) != scan_token]
```

**问题链**（#1655 引入的行为变化，方向对、删集没跟上）：`port != 22` 时
`scan_token = "[ip]:port"`，`prior_for_host` 只取同 token 条目参与 #908 的换钥比较；
但 `kept` 仍同时剔除裸 `ip`（22 端口）条目与 `[ip]:port` 条目。于是：22 端口既有条目
**从未参与比较**就被删除——若其密钥与本次扫描不同（真实换钥场景），删除后连
「host key changed」判定都没发生即落盘，**绕过 #908 守卫**；且此后 22 端口连接因条目
丢失而失配。

**修复后语义**（同一域原则）：

- port 22：`scan_token = ip`，只比较/替换裸 `ip` 条目——与现网行为一致；
- 非 22：只比较/替换 `[ip]:port` 条目，裸 `ip`（其它端口）条目**原样保留**。

`#1655` 的 Agent Note Revisit 已自述此残余（「另单修订」），本单即该修订，不改变
#1655 的方向（比较集收窄到同 token 保持不变）。

## Alternatives

- **保留现状（非 22 也删裸 ip 条目）**：拒绝。删除未参与比较的条目 = 静默绕过换钥
  守卫；且丢失 22 端口条目的副作用无任何日志提示。
- **非 22 路径把裸 ip 条目纳入比较集**（回到 #1655 之前）：拒绝。这正是 #1655 修的
  误判（keyscan 对非默认端口只产出 `[ip]:port`，混集恒不相等 → 同密钥也误报换钥）。
- **显式错误/告警代替静默保留**：不采纳。保留是正确行为（不同端点的信任互不影响），
  不需要噪音；需要观测时 `known_hosts` 内容本身就是事实。
- **同时重构 `scan_token` 语义（如引入 endpoint 对象）**：超出本单范围；一行删集
  修复 + 两例测试已消除唯一错域点（`prior_for_host` 与 `kept` 现已同式）。

## Verification

- **红绿对照**：`git stash` 暂存实现后重跑新测试 →
  `test_trust_host_key_nondefault_port_preserves_22_port_entry` **FAILED**
  （22 端口条目被删）；恢复实现 → `pytest backend/tests/test_ssh_security.py -q` →
  **15 passed**；
- 新增两例：
  1. 非 22 端口信任：密钥不同的 22 端口既有条目**保留**，且新 `[ip]:port` 条目写入
     （红侧主场景）；
  2. 反向守卫：22 端口信任不比较、也不删除 `[ip]:port` 条目（回归，修复前后均过）；
- 既有 #1655 三例（同 token 同密钥误判修复 / 同 token 换钥仍拒绝 / 22 端口同键重扫）
  全部保持通过；
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- **同族端点语义**：`known_hosts` 的其它形态（`@cert-authority` / hashed host / IPv6
  `[::1]:port`）当前不在 `_host_token` 匹配范围——若现场用到这些形态，另行评估
  （不属于本单缺口）；
- **删除集/比较集同域的机械化**：两处现已同式（`_host_token(ln) == scan_token` /
  `!= scan_token`），但没有测试锁「将来新增第三处集合操作」。若再次出现错域，考虑
  提取 `_endpoint_filter(existing, scan_token)` 帮助函数由测试覆盖。
