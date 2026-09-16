# 脚本退役通道与判据收口（#735 A 批）

Status: implemented
Class: process

2026-09-16 执行 #735 A 批零引用退役（生产库 50 个版本置 `is_active=false`）时，暴露三件文档
没写、或写了但与实现不一致的事。本 note 固化判据与坑，权威口径落回
[`script-versioning.md` §退役与删除](../../development/script-versioning.md)。不取代
[2026-09-10 膨胀终态可行性](../architecture/2026-09-10-script-bloat-endgame-feasibility.md)
与 [2026-09-13 ADR-0039 收窄](../architecture/2026-09-13-script-immutability-narrowing-adr0039.md)，
只交叉链接：前者论证「退役是 P0、零成本」，后者定义「退役 → 冷却 → 删除」终态出口，
本批是前者落地、不触碰后者。

## Decision

1. **退役通道记两条，不再只写 `PUT`**。`DELETE /api/v1/scripts/{id}` 是专用软退役
   （审计 `action=deactivate`），`PUT ... {"is_active": false}` 是同一守卫下的字段更新
   路径（审计 `action=update`）。两者共用 `_ensure_script_can_be_deactivated`（refs>0 → 409
   `SCRIPT_STILL_REFERENCED`）与同一 catalog 缓存失效。批量退役选 `DELETE`：审计语义与动作
   一致，不必在 `update` 事件里解释「其实这次只改了 is_active」。再激活只有 `PUT`，且无守卫。
2. **退役判据 = 工具口径 + 三条收敛**：`is_active` ∧ `plan_step refs=0` ∧ 留存窗口内
   `plan_run` 快照零执行 ∧ **¬同族最新版** ∧ 退役后该族仍有 active。
   冷却期保护的对象是「刚发布、还没人把 Plan 迁过去」的**最新版**（承接面），不是已被同族
   更新版本取代的中间版——后者没有前进路径，留着只膨胀前端可选目录。
   不按 `script.created_at` 冷却：该列是**入库注册时间**，2026-09-13/14 的扫描把 5.0.1、
   gpu_check 1.0.7 等早已发布的旧目录补登记，按它冷却会把最该退役的行留在场上。
3. **退役不进迁移链**。种子迁移的 `upgrade()` 分支显式 `is_active = true`，空库重建会复活
   退役状态；但用迁移表达批量退役会丢掉操作者身份与 `audit_logs`（生产变更的可审计性是
   ADR-0039 把「退出可选面」与「从仓库删除」分成两步的前提）。本批只做数据侧退役并留痕，
   漂移收口归 #2055（seed 治理）与 #735 长效机制。

涉及文件：本文、`docs/development/script-versioning.md`。

## Alternatives

- **只按工具口径退役全部 82 条 active 零引用版本**：多出的 32 条里 21 条是各族最新版
  （含 #2048 修复版 `gpu_setup@1.2.0`/`monkey_launch@5.2.0`）——退役它们等于把「可选目录
  膨胀」换成「无版本可选」，且与在窗 Execution 的最新版语义正面冲突。放弃。
- **按 `created_at` 加 60 天冷却**：见判据 2，注册时间与发布时间是两件事，会漏掉扫描补登记
  的历史版本。放弃。
- **新增一条 forward migration 表达 50 条停用**：收口重建漂移，代价是审计里只有 revision 号、
  没有操作者与 `deactivate` 事件，且与 #2055 的 seed 治理轨道重叠。放弃，改为在文档写明
  漂移边界。
- **把 11 条「留存窗口内有执行事实」的版本一并退役**：判据上不成立（末次执行距今天 5–42 天，
  09-15 重指后 refs 才归零），追溯价值仍在。放弃，改为按「末次执行 + 60 天」到期复评。

## Verification

纯文档变更（`test_impact=none`），无行为改动。留痕与实测依据（均已在 #735 评论）：

- 写前：`check_unreferenced_script_versions --json` 与自跑 SQL 逐行对拍（active ∧ refs=0 = 82）；
  非终态 `plan_run` 快照 ∩ 清单 = 空；逐条 dry-run 校验 `(name, version)` 未漂移、`refs=0`、
  `is_active=true`。
- 写时：`DELETE /api/v1/scripts/{id}` ×50 全成功；`audit_logs` 计 50 条
  `action=deactivate AND resource_type='script'`，操作者 `stp-admin`。
- 写后：active 120 → 70；`active ∧ refs=0` 82 → 32（=21 最新版 + 11 到期前保留，账平）；
  「已停用但仍被引用」= 0；无脚本族失去全部 active；50 个版本目录仍在磁盘且
  `check-script-version-immutability.py --base origin/main` → `OK`；
  `GET /api/v1/scripts?is_active=true` 返回 70 行、不含任一退役版本；48 台 host 的
  `script_catalog_version` 收敛同一指纹；`/health` healthy。
- 本文与目标文档：`python scripts/run_gates.py check:quick`（含 S2 断链、S10 note 头部/四节）。

## Revisit

- 「60 天零引用巡检」守卫（#735 评审追加项）未实现——它需要与本批判据 2 同源的
  「同族最新版豁免」口径；在窗 Execution #2048 正在做「最新版本守卫」，待其合入后接其判据，
  避免两处各写一份豁免。届时存量基线为 32。
- 若 `PLAN_RUN_RETENTION_DAYS` 收紧，`usage` 的执行事实窗口随之变短，判据 2 的「零执行」
  含义要重述（当前实测窗口内最老 run = 2026-08-05）。
- 空库重建复活退役状态：#2055 若给出「迁移只登记不激活、激活归运维」的形态，判据 3 的取舍
  需要重议。
- ADR-0039 的「退役 → 冷却 → 删除」第二步（物理删除零引用退役目录）一旦启动，本批 50 条即
  其候选池；那需要独立的 ADR 实施轨，不在本 note 范围。
