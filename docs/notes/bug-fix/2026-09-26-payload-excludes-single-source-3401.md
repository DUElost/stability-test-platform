# 载荷排除集单一源化 + 修复 `scripts` 缺失导致的部署身份分叉（#3401 A3）

Status: implemented
Class: bug-fix

## Decision

台账 A3（#3401）原计划只是「排除集三处载体收敛」的 DRY 评估；**评估阶段实证出一个真实的分叉缺陷**，本 PR 一并修复并把两个 Python 载体收成单一源：

1. **缺陷**：`backend/agent/contracts/artifact_digest.py::PAYLOAD_EXCLUDES` 漏了 `"scripts"`（ADR-0051 Phase 3「脚本走包分发、不随源码树同步」只写进了控制面 `_TAR_EXCLUDES`、wrapper `FIXED_EXCLUDES`、Ansible `agent_install_excludes` 三处）。
   - 后果（真实树实测，`main@8f129a0d`）：契约口径枚举多出 **84 条** `scripts/*` 条目，两侧 digest 不同——`desired(code)` 与 Ansible/bundle 侧算出的身份**永远不一致**（`tools/ansible/compute_deploy_digest.py` 仍会给主机写 `ARTIFACT_DIGEST`，bundle manifest 的 `agent-code` 走同一契约口径）。
   - 为什么之前没红：两侧枚举的合成树 fixture 都**没有 `scripts/`**，而三处文字比对又**不含契约那一份**——测试面正好在缺陷的两侧留了缝。
2. **修复（对齐 Phase 3 既定意图）**：契约侧补 `"scripts"`；真实树上两侧 digest 复核一致（`sha256:60c7b388…`，条目差异 0）。
3. **收敛（单一源）**：`host_updater` 删除私有 `_TAR_EXCLUDES` / `_TAR_EXCLUDE_SUFFIXES` / `_TAR_EXCLUDE_GLOBS` 字面量，改为直接 import 契约包三个常量（与 ADR-0054 第 3 步「digest 归契约」同一归属；C3 通配行放行）。**四处 → 契约（单一源）+ 2 份不可 import 的拷贝（wrapper / YAML）**。
4. **守卫（防复发）**：
   - `tests/test_ansible_digest_contract.py`：比对对象由「Ansible / wrapper / `hu._TAR_*`」改为「**契约 / wrapper / Ansible**」；并加 `not hasattr(hu, "_TAR_*")` 三条断言——host_updater 再长私有副本即红；
   - `_build_tree`（ansible 合约）与 `agent_tree`（digest 测试）fixture 均补 `scripts/<family>/v<ver>/…`，让「脚本不进身份」这件事**有合成树可测**；
   - `.pyc` 后缀断言改指契约常量。
5. **不改**：wrapper `FIXED_EXCLUDES` 与 Ansible YAML 仍是拷贝——单文件脚本（装到 `/usr/local/sbin`，不得 import backend）与 YAML 数据无法 import Python；由上面的比对测试锁定。**不引入生成机制**（为一个 3 行的列表加构建期模板/生成器，收益不抵新增的移动部件）。

## Alternatives

- **只补 `scripts`、不收敛**：弃——同一分叉 2026-09-26 已发生过一次；两个 Python 载体共享同一对象是零成本，留着四处字面量等于允许它再发生。
- **让控制面把 `scripts` 算进身份**（即以契约侧为准）：弃——与 ADR-0051 Phase 3 冲突（脚本按包分发、不再随源码树同步），wrapper/YAML 也一直排除它。
- **wrapper/YAML 从契约生成**：弃——新机制（生成器 + 分发链改造）换来的只是去掉两份被测试锁定的拷贝；ADR-0054 §7 的复议条件（出现需要独立演进的共享定义）不成立。
- **把新守卫放进 `check:payload-root-clean` 之类的门禁**：弃——已有 `test_ansible_digest_contract.py` 这一载体，扩它比新增门禁便宜。

## Verification

- **真实树前后对照**（决定性证据）：修复前 `desired(code)` ≠ 契约口径（契约多 84 条）；修复后两者相等（`sha256:60c7b388…`，`set` 差异 0）。
- 相关测试：`backend/tests/services/test_artifact_digest.py`（fixture 已含 scripts）+ `test_host_updater.py` + `test_agent_version_info.py` + `tests/test_ansible_digest_contract.py` + `test_release_bundle.py` + `test_site_install.py` + wrapper 两件 → **187 passed**；再补 `test_ansible_digest_bookkeeping` / `test_agent_priv_write_digest` / `test_agent_priv_boundary` / `test_agent_import_boundary` → **67 passed**。
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (16 gates)**。
- **反向变异（临时，逐条复原）**：
  - 契约侧去掉 `scripts` → **3 例红**（三处比对 / code 身份排除 / 载荷 skip 规则）；
  - host_updater 复活私有 `_TAR_EXCLUDES` → 新断言红（"又长出了私有 …"）。
- **身份变更面**（显式记录）：契约口径的 digest 值发生一次性变化——bundle manifest 与 Ansible 写出的 `ARTIFACT_DIGEST` 从此与控制面 desired 一致；构建/安装工具随 bundle 同行，重算自洽，按常规发布流程出新 bundle 即可。历史上被旧 Ansible 路径写过 marker 的主机，会在修复后**被判一次 drift 并自愈**（一次热更新写回正确值）——属预期收敛，非新故障。

## Revisit

- 若 wrapper/YAML 将来有生成路径（例如 wrapper 改为随包分发），复议「四处 → 单一源」的最后一步；
- 若 `scripts/` 的交付模型再变（例如脚本重新随源码树同步），三处 + 契约需同 PR 改（测试会强制）；
- 合成树 fixture 现已含 `scripts/`；若未来出现新的「不传输目录」类别，记得同时加进 fixture，否则新排除项又会落在测试缝里。
