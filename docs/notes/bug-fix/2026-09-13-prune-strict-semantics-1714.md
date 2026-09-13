# #1714 修 `--prune --yes --strict` 退出码与复核报告语义

Status: implemented
Class: bug-fix

## Decision

修 `tools/dev/check_test_containers.py` 的 `--prune --yes` 路径两处语义缺陷
（#1655 引入 `--prune`/复核逻辑时遗留），并补 4 例回归测试（15 → 19）。

**缺陷 1：`--strict` 用删除前的 `stale` 判定**。原 `if args.strict and stale: return 1`
用的是清理**前**的列表，故 `--prune --yes --strict` 即使把残留全清干净也返回 1，
与 `--strict`「存在疑似残留时退出码 1」的语义不符。修法：引入 `remaining_stale`
（清理后复核结果）并据此判定。

**缺陷 2：复核异常被吞并谎报**。原 `except Exception: remaining_stale = []` 把
「复核失败、结果不可知」伪装成「已全部清理」，随后打印「仍有 0 个未清理」——
**把未验证报成已验证**。修法：异常时置 `remaining_stale = None`，输出改为
「已执行 N 个删除；复核失败：清理结果不可知（无法再次列举容器）」；strict 下
结果不可知时**保守返回 1**。

**`None` 与 `[]` 的区分是本次修复的核心**：二者都「空」，但语义相反——
`[]` 是「复核过了，确实没有残留」（可安全返回 0），`None` 是「没复核成，不知道」。
用同一个值表达两者，正是缺陷 2 的根因。

**为什么不在 rm 路径上做参数校验（`--strict` 需 `--prune --yes`）**：二者正交——
`--strict` 单独使用（只巡检、不清理、有残留即 1）是既有且合理的用法
（`--strict --min-age-minutes 60` 见于脚本抬头示例）。限制组合会破坏该用法。

## Alternatives

- **缺陷 1 改为「`--prune --yes` 时忽略 `--strict`」** → 否决：静默忽略一个显式
  传入的 flag 会让人以为它在生效；语义应是「strict 看最终状态」，而非「strict 在某些
  模式下失效」。
- **缺陷 2 保持 `[]` 但改文案** → 否决：文案改了，`remaining_stale` 仍会参与
  `len(stale) - len(remaining_stale)` 的算术与 strict 判定，谎报会从打印蔓延到退出码。
  必须让**值本身**承载「不可知」。
- **复核失败时重试若干次再放弃** → 否决（本单范围）：重试不改变「最终仍可能不可知」
  这一事实，只是降低概率；且会给一个巡检工具引入等待语义。诚实报告不可知已足够。
- **复核失败时直接 `return 2`（与其他 docker 故障一致）** → 部分采纳其精神但否决实现：
  清理**动作已执行**，把它报成「巡检失败（2）」会掩盖「删除已发生」这一事实；
  故保留 1（strict 下的保守非零），并在文案中明确「已执行 N 个删除」。

## Verification

- `python -m pytest tests/test_check_test_containers.py -q` → **19 passed**；
- **缺陷复现（修复前，按当前 label 判据构造 stub）**：
  ① 清干净后 `--prune --yes --strict` → **rc=1**（应为 0）；
  ② 复核失败 → 仍打印「仍有 **0** 个未清理」，把不可知报成已验证；
- **红绿双向**：新测试在旧实现上 → **2 failed**（`test_clean_prune_with_strict_exits_zero` /
  `test_recheck_failure_is_reported_as_unknown_not_clean`）；在修复后 → **19 passed**；
- **修复后四场景实测**：清干净+strict → **0**；复核失败+strict → **1**（不可知保守，
  且文案为「复核失败：清理结果不可知」）；rm 失败容器仍在+strict → **1**；
- 全量离线子集：`python -m pytest tests/ -q --ignore=tests/test_alembic_upgrade.py`
  → **236 passed**；
- `python tools/dev/check_governance_surface.py --check` → S1–S13、S5x 全绿；
- `ruff check` 两文件 → All checks passed。

## Revisit

- **`--prune` 与 `--strict` 的交互语义**已由本单钉住（看最终状态、不可知保守为 1）；
  若日后引入 `--json` 或机器可读输出，应把 `remaining_stale=None` 映射为显式的
  `"recheck": "unknown"` 字段，而非空数组——避免消费方重犯「空即干净」的推断。
- **rm 失败与复核失败的区分**：当前两者都可能有残留、都返回 1，但文案不同
  （「rm 调用失败 N 个」vs「复核失败：结果不可知」）。若将来需要更细的退出码分级，
  应独立裁决，不在本单扩面。
