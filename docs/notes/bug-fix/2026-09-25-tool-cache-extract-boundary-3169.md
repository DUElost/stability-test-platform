# tool_cache 解包边界：摘要只证内容身份，不证落盘边界（#3169）

Status: implemented
Class: bug-fix

关联：[#3169](https://github.com/DUElost/stability-test-platform/issues/3169)（本单）、
[ADR-0051](../../adr/ADR-0051-release-unit-and-content-addressing.md)（包身份与 strict 执行）、
[#3197](https://github.com/DUElost/stability-test-platform/issues/3197)（包内相对路径判据对拍）、
[ADR-0037](../../adr/ADR-0037-agent-host-privilege-boundary.md)（宿主提权边界，本修复不替代它）、
2026-09-25 复审 A02 / 复核 C4（[`docs/reviews`](../../reviews/) 同日稿 §9）。

## Decision

**定级与动机**：能进入受信发布链的包本身就会被执行，越界写入不扩大攻击面，所以按 #3169 的 P2 处理。
值得尽早修的原因有两个：一是防止**误制包**在解包阶段写坏宿主机（`verify_scripts` 预热会解包却不执行，
解包时的权限与执行时相同）；二是 strict 成为缺省后，`ensure_package` 是全 fleet **每个脚本**的必经路径。

两道防线，第一道是权威：

1. **整包预检 `_archive_rejection`**（所有 Python 版本生效）：在原有逐成员检查（绝对路径、`..`、
   硬链、设备、FIFO）之外，再加两条跨成员判据：
   - **路径唯一**：同一路径至多出现一次，防止「先软链、后同名普通文件」顺着软链改写目标；
   - **不经软链落盘**：除软链成员自身外，任何成员路径都不得以某个软链成员为前缀，防止「`d → /elsewhere`」
     加「`d/x`」这类组合，也包括软链指向包内的情形。

   判据只比成员路径，不做 realpath 解析，因此与链式软链、解包顺序、竞态都无关。
2. **PEP 706 `tar` 过滤器**（解释器支持时才启用）：沿软链解析后越出解包根就拒绝。不用 `data` 过滤器，
   因为它会拒掉一切绝对软链，而 venv 解释器按约定指向系统 python（Start-Log-Scan 的
   `venv/bin/python3 → /usr/bin/python3`）。显式指定过滤器，也避免 Python 3.14 把缺省改成 `data` 后判坏现有包。

**第二洞：manifest 字段在消费时刻再判一次**。`python` / `script` 与登记侧 `validate_relative_member`
逐分支对齐，由 #3197 的对拍测试扩为三方同输入、同裁决（门禁侧、登记侧、Agent 消费侧）。
拼接方式从 `pkg_dir / str(x)`（遇到绝对路径会丢掉左段）改为按 `/` 分段的 `joinpath`；存在性检查统一为
`is_file()`（`.` 与目录不再当作解释器）。脚本还必须**实体在包内**，即 `resolve()` 后仍位于包目录下。
解释器只做字面 containment，因为 venv 软链解析后按设计就在包外。

## Alternatives

- **照 #3169 建议直接 `extractall(filter="data")`**：会拒绝 Start-Log-Scan 的 venv 绝对软链，在 strict 下
  直接让该工具不可用；而且无回移的旧解释器（Agent 只要求 3.10+）根本没有 `filter` 参数。
- **只靠 PEP 706 过滤器**：主机的补丁级别不可假设（Debian 12 的 3.11.2 早于回移版本 3.11.4），旧解释器上
  等于不设防。所以预检才是权威，过滤器是加固。
- **realpath 逐成员解析判越界**：对链式软链与解包顺序敏感，而且有「检查后、写入前」的竞态窗口。
  按成员路径做前缀判据更简单，也同样完整。
- **解释器也要求 `resolve()` 在包内**：会把 venv 约定整类判坏。解释器只做字面 containment；执行时的宿主
  隔离沿 ADR-0037，不把摘要或解包判据当作执行沙箱。
- **顺带给 `script_packages.resolve_script_path` 加同样的 resolve containment**：已发布的平台脚本包里没有
  软链（全站扫描结果），且该入口只取 `nfs_path` 的 basename。不在本单扩范围，列入 Revisit。

## Verification

- **全站回放（零误拒是硬前提）**：只读扫描站点 `packages/` 下全部 **214** 个已发布包：
  - 成员共 1295 个普通文件、3 个软链，无目录条目、无硬链或设备；
  - 位于软链下的成员、重名成员、绝对路径或含 `..` 的成员均为 0；
  - 用新 `ensure_package` 把 214 个包全部解到 tmp 缓存，两种模式（PEP 706 过滤器 / 仅预检）均为
    **214/214 放行**；
  - 站点 manifest 的 428 个 `python` / `script` 字段全部通过新判据；
  - 真实 Start-Log-Scan 走完整解析链：解释器沿 `python → python3 → /usr/bin/python3` 解析成功，脚本在包内。
- **反例先红**：新增 8 个反例用例、共 11 个实例，旧代码上全红：
  - 原形态攻击、包内软链、同名覆写 3 个用例，各按两种解包模式参数化（6 个实例）；
  - 重名 1 个；
  - 字段类 4 个（绝对 / `.` / `..` / 反斜杠）。反斜杠用例在包内真放了同名文件，避免因文件不存在而碰巧通过。

  另有真实 venv 形态的正例，两种模式下都保持绿（2 个实例）。
- **变异**：
  - 去掉「不经软链落盘」判据后，仅预检模式的攻击用例与两种模式的「包内软链」用例变红；PEP 706 模式下的
    攻击用例仍被 `tar` 过滤器拦住，说明纵深防御成立；
  - 去掉消费侧字段判据后，`..` 与反斜杠用例变红。
- **测试**：
  - `backend/agent/tests/`：**2150 passed**；
  - `tests/test_adr0051_package_member_validator_parity_3197.py`：三方对拍 **17 passed**；
  - `ruff` 通过；
  - `python scripts/run_gates.py check:quick`：见 PR 描述。
- 本机实验（Python 3.13.5）：`filter="tar"` 放行真实 venv 包、拒绝原形态攻击（`OutsideDestinationError`），
  无文件逃逸。

## Revisit

- `script_packages.resolve_script_path` 的入口也按「实体在包内」判定：平台脚本包一旦出现软链（例如新族
  自带 venv）即须补上，届时与本判据共用一个 helper。
- Agent 主机若统一到带 PEP 706 的解释器，也保留预检：它才是权威判据，过滤器只是第二道。
- 本修复不替代执行隔离与提权边界（ADR-0037）。签名、摘要、解包判据都不是执行沙箱。
