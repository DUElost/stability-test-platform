# 版本不可变门禁拦「已发布目录新增文件」（#888，R01-F08）

Status: implemented
Class: bug-fix

## Decision

`check-script-version-immutability.py` 的 `_MUTATING_STATUS = {M,D,R,T}` 把
status=`A` 一刀切放行——无法区分「发布全新版本目录」（ADR-0020 指定做法）与
「往已发布 `v*` 目录塞文件」（同样改变该版本的可用文件面；`_` 前缀辅助文件
连 entry sha 都不计，扫描器无从察觉，是比 M 更隐蔽的漂移通道）。

修复：`A` 状态的版本路径按「基线中该 `v*` 目录是否已存在」分流——已存在即
违约（`ADDED_INTO_PUBLISHED`，错误信息带既有新建版本指引）；基线不存在 =
全新目录整树新增，放行。存在性判定用
`git ls-tree --name-only base -- <version_dir>` 非空即已发布（git 不跟踪空
目录，目录含任意层级内容即非空，覆盖「只有子目录没有直接文件」的形态）。

## Alternatives

- **A 全部拒绝**——放弃：会拦死「新建版本目录」这一 ADR-0020 规定的唯一
  正确做法，门禁直接不可用；
- **白名单比对 diff 里的 A 是否与某个新目录前缀一致**——放弃：前缀字符串
  比对正是 #905 修过的坑形态；`git ls-tree` 问基线是权威判定；
- **只拦 `_` 前缀新增**——放弃：缺口在「改变版本文件面」，不限前缀。

## Verification

- `tests/test_script_version_immutability_gate.py` **8 passed**（新增 3 用例：
  已发布目录塞文件被拦且带 `新增入已发布版本` 标记、全新目录含子目录整树
  新增放行、版本目录仅含子目录时塞文件仍被拦）；
- `check:quick` 7 门禁全绿（本单门禁自检亦在其列）。

## Revisit

- 无。门禁为纯静态比对，不引入运行时行为；后续若引入文件名豁免（如
  README）须另立裁决。
