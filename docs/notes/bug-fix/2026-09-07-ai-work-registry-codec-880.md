# ai_work registry codec 修复：`#`/`:` id 与损坏隔离（#880）

Status: implemented
Class: bug-fix

## Decision

修复 #880（P1 MVP 的 codec 缺陷——declare 报 OK 后 registry 全命令崩）。三层修复全采纳：

1. **语义层守门**：`normalize_requirement_id`——declare 拒绝 `#` 开头与含 `:` 的 id（REFUSED + 恢复指引：issue 号引用写作 `issue-878` 式 slug 或剥离前导 #）；
2. **codec 层对称化（防御深度）**：`yaml_dump` 的 record key 走 `_quote`；`_quote` 字符集移除 `#`（含 `#` 一律加引号——行首裸 `#` 会被当注释）；`yaml_load` 支持引号 key 行解析。历史损坏文件与手工修复场景由此可正确往返；
3. **损坏恢复兜底落地**（契约 §2.2 此前只有条款没有实现）：`read_registry` 解析失败 → `os.replace` 隔离为 `registry.yaml.corrupt-<时间戳>` 留证 + 人类可读报错（含恢复指引「可重 declare、不要静默清空」）——issue 指出的「registry 锁死无恢复路径」消除；
4. **补齐契约 §2.2 残留 tmp 清理**：`load_locked` 持锁后删除无主 `.tmp`（前次崩溃遗留）。

自测扩充：declare 双拒绝红样例、值位 `#` 合法绿样例、引号 key codec 往返、corrupt 隔离留证断言（含「坏 yaml 必须真非法」的样例构造修正——`#broken` 是裸标量值并不非法，首版样例构造错误）。

## Alternatives

- **只做 codec 对称、放开 `#` 开头 id**——放弃：语义层也守门可让报错在 declare 时点就出现（而非落盘后其他命令才炸），且 id 保持简洁标识形态利于 status 展示；
- **corrupt 时自动重建空 registry**——放弃：静默清空会伪造「无人在工作」（契约 §2.2 原文理由），留证交人工。

## Verification

- `--self-test`：#880 三缺口红绿样例全过（declare 双拒绝 / 引号 key 往返含 `#878` key / corrupt 隔离留证）+ 既有 12 规则不回归；
- 复现仓（/tmp 一次性 git 仓库）终验：`declare "#878"` → REFUSED 带指引；`declare "P0: sync"` → REFUSED；预置坏 registry 后 `status` → 隔离留证 + 恢复指引；清理后重新 `declare issue-878-slug` 可用且 untracked 文件被 derived 捕获；
- 治理门禁 S1–S11+S5x 全绿；ruff 通过；本修复自身经 Registry 全周期登记（declare → PR → update --pr → finish）。

## Revisit

- codec 引号 key 使 registry.yaml 与标准 YAML 工具互通（quoted key 是合法 YAML）——后续若引入真实 YAML 库可无缝切换；
- #880 的「执行契约残留 tmp 清理/corrupt 隔离缺失」关联项随本修复关闭，可请 #880 提报会话复核关闭。
