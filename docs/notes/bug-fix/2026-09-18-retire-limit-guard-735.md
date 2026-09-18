# 退役执行器 `--limit` 非正数判据洞（#735）

Status: implemented
Class: bug-fix

## Decision

`tools/dev/retire_script_versions.py` 的 `--limit` 从裸 `type=int` 改为
`type=_positive_int`（parse 期拒绝 0 与负数），`_apply` 内截断判据从真值判定
`if args.limit:` 改为 `if args.limit is not None:`。

洞的形态（2026-09-18 24h 审计发现，tip `f1179f95`）：`--limit 0` 是假值 →
不截断 → 配合 `--yes` 等于**一次退全量**；`--limit -3` 则切成 `items[:-3]` =
「去掉尾部 3 条」继续执行。两者都是不可逆生产写（退役方向）上的静默扩大半径，
与该参数「分批推进用」的设计意图正好相反。

判据放在 argparse `type=` 而不是 `_apply` 内的理由：写入口有两个
（execute/reactivate），parse 期一处校验天然双入口共用，且错误信息在 usage 上下文里
对操作者更可执行；`_apply` 的 `is not None` 保留为纵深防御——该函数可被 import 直调，
判据本身必须正确，不依赖调用方先经过 parse。

## Alternatives

- **`--limit 0` 语义化为「处理 0 条」**：技术上自洽，但「分批推进」场景里显式传 0
  几乎必然是脚本参数拼接事故而非意图，报错比静默空跑更早暴露问题；拒绝语义也不
  关闭任何真实用例（想空跑有 dry-run 缺省形态）。
- **只在 `_apply` 内加 `if 0 < args.limit <= len(items)` 一类守卫**：修得了截断、
  修不了「负数静默改语义」的可发现性——parse 期报错直接告诉操作者参数非法，
  运行期守卫只能事后少做事。

## Verification

- 反例先行：新增 3 条测试在修复前跑红——
  `test_limit_zero_and_negative_rejected_at_parse_time`（parse 不拒绝，直通执行面）、
  `test_apply_limit_zero_processes_nothing_even_bypassing_parse`（`limit=0` 直调
  `_apply` 时构造了客户端并处理了条目，即洞本体）；修复后同文件 **26 passed**。
- `test_positive_limit_truncates_batch` 钉住正路语义不变（前 N 条截断）。

## Revisit

- 本修复只关「执行器参数洞」；#735 的主体（47 个零引用版本批量退役）按 manifest
  另行执行，进度在 issue 评论留痕。
- `plan` 子命令的 `--cooldown-days` 已是 `is not None` 形态（只读路径），无同族洞；
  若未来再加数值型写路径参数，应默认走 `_positive_int`（或等价的显式校验 type）。
