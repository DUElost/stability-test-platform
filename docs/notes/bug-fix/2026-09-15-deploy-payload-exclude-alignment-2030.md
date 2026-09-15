# #2030 第 4 条：部署身份枚举与三通道排除集对齐（venv/logs/wrapper/stp_schemas）

Status: implemented
Class: bug-fix

## Decision

ADR-0040 D1 明文：「digest 输入集 = 部署流程实际拥有并覆盖的文件集——排除
VERSION、ARTIFACT_DIGEST、.env、deps marker、**venv、logs**、resources/mtbf/、
__pycache__、tests/」。实现未对齐：

- `host_updater._TAR_EXCLUDES` / `agent.artifact_digest.PAYLOAD_EXCLUDES` 缺
  `venv` / `logs` / `stp_agent_priv.py` / `stp_schemas` / `.deps_installed_sha`；
- `stp_agent_priv.py` 由 install/update playbook 装到 `/usr/local/sbin`
  （不进安装目录），却被算进 code 身份——**编辑 wrapper 会让全机群判
  code_drift**（#2030 实测：本窗口改了两次 wrapper，各触发一次冗余全量收敛）；
- 三条部署路径（wrapper `FIXED_EXCLUDES` / 热更新裸 rsync / Ansible
  `agent_install_excludes`）排除集互不同源（Ansible 有 `venv/`、`logs/`，
  digest 没有；wrapper 用三个窄 test 模式，另两处用宽模式）。

决定（一处对齐、三处同源）：

1. **digest 输入集（双侧镜像）**：补 `venv`、`logs`、`stp_agent_priv.py`、
   `stp_schemas`、`.deps_installed_sha`；`test_*.py` 由内联判断提升为显式常量
   （`_TAR_EXCLUDE_GLOBS` / `PAYLOAD_EXCLUDE_GLOBS`，`fnmatch` 语义与原
   `startswith+endswith` 等价）——使「同源」可逐项比较。
2. **热更新裸 rsync**（`host_updater` 远端脚本 fallback 分支）：加同批
   `--exclude`；`--delete`（非 `--delete-excluded`）下 excluded 项不参与删除，
   宿主侧已有文件不受影响。
3. **wrapper `FIXED_EXCLUDES`**：补 `venv/`、`logs/`、`*.pyc`、
   `.deps_installed_sha`；三个窄 test 模式统一为 `test_*.py`（与另两处取齐）。
4. **Ansible `agent_install_excludes`**：补 `*.pyc`、`stp_schemas/`、
   `.deps_installed_sha`。
5. **kind 默认值必填化**（#2030 附带）：`_iter_payload_files` 原默认 `full`、
   `_build_tarball` 原默认 `code`——不对称会让漏传时「打包范围」与「身份范围」
   静默错配（#2019 同源风险）；两者改必填，调用点（含 4 处测试）显式传参。
6. **测试**：`tests/test_ansible_digest_contract.py` 的排除集契约从「YAML
   字符串存在性」升级为①三处规范化集合逐项相等；②效果级——fixture 树里
   `stp_agent_priv.py` / `venv/lib.py` / `logs/a.log` / `stp_schemas/stale.json`
   不得进 code 身份（`stp_schemas/pipeline_schema.json` 的 extra 附加不受影响）。

**影响面（一次性校准）**：desired code 身份由「含 wrapper 的 485 项」变为
「不含 wrapper 的 484 项」（venv/logs/stp_schemas 源树不存在，无差异）→ 下次热
更新/批量更新时全机群判**一次** `code_drift`，收敛写入新 `ARTIFACT_DIGEST` 后
稳定；此后编辑 wrapper/venv/logs 不再触发误判。不涉及迁移、不改 ADR（本就是
ADR-0040 D1 的落地对齐）。

## Alternatives

- **只改 Ansible 一侧**：digest 与通道仍分叉——正是 #2030 原缺陷形态（两侧镜像
  内部一致、跨通道不一致，parity 测试拦不住）。否决。
- **digest 输入集改为「只含 rsync 一定传输的文件」白名单**：需与 rsync 模式做
  双向语义映射，复杂度高于收益；排除集对齐已由测试锁定。否决。
- **保留 kind 默认值只加注释**：漏传仍是静默错配（#2019 类风险）。否决。
- **不排除 `stp_schemas/`、只排除具体文件**：源树若出现 stp_schemas/ 目录，其
  内容不是部署产物（schema 由 extra 通道独立安装到 `$INSTALL_DIR/schemas/`）。
  维持目录级排除。否决。

## Verification

- `python -m pytest tests/test_ansible_digest_contract.py -q` → **7 passed**
  （新增 2 例：三处同源 + 效果级）；
- **逐点 mutation**（改实现、恢复后复绿）：
  1. 两侧镜像同时删 `stp_agent_priv.py` 排除 → 同源断言 + 效果级 **FAILED**，
     parity 用例仍 **passed**——证明「两侧镜像一致但三通道不同源」时只有新增
     断言能拦（#2030 原缺陷形态）；
  2. 两侧镜像同时删 `logs` 排除 → 同上（2 failed / 5 passed，parity 绿）；
  3. 仅 digest 侧（host_updater）删 `logs` → parity 也转红（双侧镜像分叉），
     3 failed；
- `python -m pytest backend/tests/services/test_artifact_digest.py
  backend/tests/services/test_host_updater.py -q` → **43 passed**；
- `check:quick` → 10 门禁全绿。

## Revisit

- 本单处理 #2030 第 4 条；四条至此全部落地（前三条 PR #2111 / #2115 / #2122），
  issue 可收口。
- 排除集「同源」目前靠测试逐项锁定；若未来出现第四条部署通道（如镜像分发），
  需把该用例扩为 N 处比较。
- #2019（`--filter=protect resources/` 只护目录节点不护内容）是独立缺陷，未在
  本单处理。
