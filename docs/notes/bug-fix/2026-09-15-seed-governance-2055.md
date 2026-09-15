# #2055 两个 seed 迁移不合规：无引用检查即停用 / 缺激活分支 + downgrade DELETE

Status: implemented
Class: bug-fix

## Decision

**1. `y0z1a2b3c4d5_seed_gpu_setup_v110.py` 无引用检查就停用 1.0.9**
该迁移直接 `UPDATE script SET is_active = false` 下线 `gpu_setup 1.0.9`，而
`docs/development/script-versioning.md` 的「种子迁移治理（#942 裁决 A）」明确禁止
「无引用检查的 `UPDATE script SET is_active = false`」；同窗口的兄弟 seed
（`a3b2c1d0e9f8` 等）都带 `_raise_if_any_version_referenced`。后果：仍被 `plan_step`
引用的版本被静默下线，相关 plan 到 precheck 才失败（`precheck/scripts.py` 只取
`is_active`）。
**改**：内嵌同款 `_raise_if_any_version_referenced`（#942 约定：迁移自包含、不 import
服务层）并在停用前调用。

**2. `z1a2b3c4d5e6_seed_monkey_launch_v502.py` 缺 else 激活分支 + downgrade 用 DELETE**
升级只有 `if row is None: INSERT … is_active=true`，**缺 `else: UPDATE … is_active = true`**
（兄弟 seed 与模板都有）→ 目标行已存在且为 inactive 时，本次升级会把 5.0.1 停用却
没有任何 active 版本。downgrade 用 `DELETE FROM script`（**不是** upgrade 的逆操作，
会删掉并非本迁移创建的行，兄弟 seed 只翻转 `is_active`）。
**改**：补 else 激活分支；downgrade 改为翻转 `is_active`。

## 顺带发现（影响面比原单更大，已按前向守卫收口）

手工核对时写了源码级守卫，一跑就暴露**30 个历史 seed** 同样缺引用检查
（`a7b8c9d0e1f2` … `w4x3y2z1a0b9`，2026-08-25 ~ 2026-09-12 新增；带检查的从
2026-09-13 的 `a3b2c1d0e9f8` 起）。**不追溯改造**：这些迁移早已在生产应用，往里加
`raise` 会让「引用存在时的全新安装」直接中止部署——风险大于收益。
故守卫口径 = **前向**：新增 seed 必须带检查，存量以显式
`_LEGACY_SEEDS_WITHOUT_REF_CHECK`（30 条）豁免，并附一条「豁免表不得失效/扩大」的自检。
终态出口：随零引用版本退役（#735）自然收敛。

## Alternatives

- **追溯给 30 个历史 seed 补检查**：见上，会改变已应用迁移在全新安装上的行为
  （引用存在即中止部署）——否决，改为可见的存量豁免 + 前向守卫。
- **豁免用「文件名日期 ≤ 2026-09-12」表达**：测试不依赖 git（CI 可能是浅克隆），
  且日期规则易被绕过；显式 revision 列表可审计、可自检失效。
- **只改两个文件、不加守卫**：服务层治理单测覆盖不到迁移文件本身，同类缺陷会继续进来
  （#2055 就是这么发生的）。

## Verification

- `pytest tests/test_script_seed_governance.py -q` → **6 passed**（新增 3 条：前向守卫、
  豁免表无失效条目、downgrade 不得 DELETE）
- **反事实验证**：还原两个 seed 文件 → `test_new_seed_migrations_deactivating_versions_check_references`
  与 `test_seed_migrations_do_not_delete_script_rows_on_downgrade` **FAILED**（2 failed / 4 passed）；
  恢复后 6 passed
- **全新库迁移**（`pr-migrate-empty-db` 口径）：`pytest tests/test_alembic_upgrade.py -q` → **1 passed**
- 根 `tests/`：**602 passed**；`python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**

## Revisit

- **30 个 legacy seed 的可见债务**：豁免表把「哪些迁移缺治理」固化成可读清单。若 owner 要
  清理，正确做法是**新发一个 seed** 做补偿（而不是改历史迁移），或随 #735 的零引用退役收敛。
- **#2055 的 seed 编号命名歧义**：`y0z1a2b3c4d5_seed_gpu_setup_v110.py` 实际 seed 的是
  **v1.0.10**（不是 v1.1.0）——`v110` 这个后缀在文件名里同时可能指 v1.1.0 / v1.0.10，是
  #2048 里「守卫测试认错目录」的同源陷阱。重命名已应用迁移不可行（revision 与文件名的
  关联已成历史），建议后续 seed 文件名用 `v<major>_<minor>_<patch>` 全展开形式。
