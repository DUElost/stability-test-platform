# 包门禁判据可信度批：#3197 两项 + #3196 显式守卫（含 #3191 复验为「已修未关」）

Status: implemented
Class: bug-fix

## Decision

同族三处「声明与实现不符 / 判据靠副作用兜底」，一起收：

1. **#3197 项1（两份路径校验器漂移）**：不合并实现——作者显式选择「门禁侧独立实现，
   避免跨工具 import 脆链」，这个选择成立。漂的不是结构，是**「同判据」这句话没人钉**。
   于是：补齐门禁侧少掉的反斜杠与盘符两个拒绝分支，并新增
   `tests/test_adr0051_package_member_validator_parity_3197.py`——同一输入集跑两侧、
   断言**裁决**逐点相同（文案不比，两侧文案本就可以不同），另加一条
   `test_case_set_covers_every_branch` 防样本集退化（删样本 ⇒ 判据重新失明）。
2. **#3197 项2（无入口目录的豁免面）**：Phase 3 已把「族树存在但无入口文件」改成硬红
   （`check()` 里 `facts is None ⇒ 报错`），报单人登记的豁免**实际已缩小为**
   `iter_family_trees` 跳过 `.`/`_` 前缀目录——而这类目录里的脚本源码仍随热更新下发，
   却不进任何 sha 台账。做法是不再「披露」而是**闭合**：新增 `skipped_source_dirs()`，
   被跳过却带 `.py`/`.sh` 的顶层目录直接红；legacy 名（`scan_aee` /
   `export_mobilelogs`）是 ADR-0033 登记的显式例外，自测里钉住它**不**被本判据报出
   （防把显式例外误当漏洞处理）。`__pycache__` 只有 `.pyc`，天然不误报。
3. **#3196（rebaseline 覆写 `package_sha256`）**：机制在 Phase 3 重写
   （`64d0ef93`，晚于本单）后已**结构性不可达**——本函数三处写列值（新建 /
   force_rebaseline / 回填）全部位于「登记值 == tarball 实算 sha」被证明之后，空登记值
   不可能等于任何实算 sha。但那是**巧合级**保护：分支重排一次就失效，且理由不写在脸上。
   于是把判据挪到前台：`_is_package_sha()` 显式拦 + `package_conflicts` 记
   `manifest_package_sha_missing`（带 `db_sha256`/`manifest_sha256` 供人判），并补两条
   回归钉（坏/缺 manifest ⇒ 列值与 `is_active` 均不动；条目缺登记值 ⇒ 不覆写、
   **内容身份也不被顺手改写**）。
4. **#3191（权限位进 sha ⇒ 组可写检出恒红）**：本批**不改代码**，只做 main 侧复验——
   打包器 `_normalized_mode` 已随 `62b071af`（Phase 2b，标题即含「打包器权限位归一化」）
   落地，且本单点名的「测试集不含 mode 维度」也已补（`tests/test_adr0051_phase2a.py:177`
   跑 0o644/0o664/0o600/0o755/0o775 并断言前三种同 sha）。本机 `umask=0002` 实测
   `check_script_packages`：35 族等价、exit 0 ⇒ 属「已修未关」，走证据评论关闭。

## Alternatives

- **合并两份校验器为单一实现**：漂移一次性消失，但要付跨工具 import（作者显式否决，
  且门禁侧还要承担 `None`/非字符串的健壮性）；对拍测试拿到同样的收敛，代价小得多。
- **项2 只在 docstring 登记豁免**（报单人的最低要求）：留下「已知漏洞 + 无人被拦」，
  与 #2974/#2987 那批「判据假阴性口」同形；这里判据只有 5 行，没必要留洞。
- **#3196 直接当已修关单**：列值保护会在下一次重排分支时静默消失，正是本仓反复付学费
  的那类「判据退化后仍报绿」。
- **#3191 再补一条跨 mode 测试**：已存在，重复登记只会稀释唯一判据。

## Verification

全部实跑（tip `083981bc`；pytest 一律套 `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`，见 #3200 后 test-env-self-check §3）：

- `pytest tests/test_adr0051_package_member_validator_parity_3197.py -q` → **17 passed**
- `pytest tests/test_adr0051_phase2a.py tests/test_adr0033_phase_b.py tests/test_adr0033_d0_d2_gates.py 对拍文件 -q` → **43 passed**
- `pytest backend/tests/services/test_script_catalog_sync.py -q` → **13 passed**（含本批新增 2 条；testcontainers 隔离库，生产库只读复核 `script` 35 族/212 行、无 `demo` 脏行）
- `python tools/dev/check_tool_manifest.py --self-test` / `check_script_packages.py --self-test` → **均 [OK] 红绿双向**（后者自测已含跳过面新用例）
- `python tools/dev/check_script_packages.py`（真实树）→ **[OK] 35 个族树与 tool_manifest.json 最新登记等价**
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (15 gates)**
- 变异自证 ×2：① 删掉门禁侧反斜杠/盘符分支 ⇒ 对拍 **4 failed**；② 撤掉 `_is_package_sha` 守卫 ⇒ `test_entry_without_package_sha_is_named_not_overwritten` **1 failed**（其余 12 仍绿，说明它钉的是新判据而非既有副作用）。两次均还原。

## Revisit

- `backend/tests/` 不在任何 PR required check 面（`pr-agent-tests` 只跑 `tests/` +
  `backend/agent/tests/`）⇒ 本批两条 sync 回归钉只在夜间全量生效；判据前移属另一条线。
- `_normalized_mode` 的口径是「只保留可执行位」：若将来要保留 group 写位（协作式检出），
  得先改 ADR-0051 的登记值语义，不能只动打包器。
- legacy `scan_aee` / `export_mobilelogs` 源码仍不进包审计（显式例外）；退役闭口归 #735。
