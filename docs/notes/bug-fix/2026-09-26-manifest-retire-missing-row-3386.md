# manifest_retire_from_db 行缺失误判退役收口（#3386）

Status: implemented
Class: bug-fix

关联：[#3386](https://github.com/DUElost/stability-test-platform/issues/3386)（每日审计
2026-09-26 立单，人工复核）、
[#3262](https://github.com/DUElost/stability-test-platform/pull/3262)（引入该判据的
ADR-0051 Phase 4b 单）、ADR-0051 D5/Phase 4b、
[#3349](https://github.com/DUElost/stability-test-platform/issues/3349)（发布侧 retired
引用守卫，相邻面不受影响）。

## Decision

- **判据核心反转：行缺失 ≠ 已退役**。`classify_script_entries` 抽成纯函数，flip 判据
  从「不在 active 且不在 refs」改为「**库中存在该行** 且 `is_active=false` 且未被引用」；
  库中无行的条目单列 `missing_rows` 仅报告——缺行可能是尚未 scan 的新站库，「没扫到」
  不构成退役证据。
- **--apply 健全性下限**（`apply_sanity_problems` + `--force` 逃生阀）：script 表为空 /
  active 集为空 / 拟 flip 超过非退役 script 条目半数（`MAX_FLIP_RATIO=0.5`）时拒绝写盘
  （exit 2），`--force` 显式越过并在 stderr 留痕。下限只闸写盘，dry-run 恒可用。
- **无候选不落盘**：flips 为空时不再重写 manifest（旧实现 `--apply` 恒写，json 重 dump
  即使字节等价也是无意义动作面）。
- 规避旧实现的两处隐患顺带收口：写盘循环用 `str(entry["version"])` 与分类标签同口径
  （JSON 数字版本号不再漏翻）；分母排除已 retired 条目。

## Alternatives

- **只加 --force 不改判据**：弃——判据本身错误（缺行不是证据），下限只是第二道闸；
  空库场景下限与判据双保险，但「部分 scan 的库」只有判据能救。
- **missing_rows 视为可 flip + 阈值拦截**：弃——把灾难拦截寄托在调参上，判据语义仍是错的；
  owner 预期行为明写「库中缺行应单列报告、不动作」。
- **测试直接调 main() 连真实 postgres**：弃——文件 sqlite 已覆盖全部判定路径，
  CI 的 postgres 服务容器留给真正需要 PG 语义的用例。

## Verification

- `pytest tests/test_manifest_retire_from_db_3386.py` → 12 passed：行缺失单列不 flip（核心
  验收①的纯函数面）/ inactive+未引用 flip / 被引用拒动 / active·已退役·kind=tool 跳过 /
  三条健全性下限与通过态 / 端到端文件 sqlite——**空库 --apply exit 2 且 manifest 字节不变**
  （验收标准①）、混合库只翻证据充分的行、全量 inactive 需 --force、缺行库 --force 也无可写变更；
- `python scripts/run_gates.py check:quick` → 16 gates 全绿（worktree 需 symlink 主树
  node_modules 跑 eslint、占位 DATABASE_URL 跑 root 测试导入——既有环境依赖，非本单引入）；
- 本地复跑旧版判据对照：旧代码对「空库」场景 flips=全量 script 条目（#3386 现象），
  新代码 flips=0。

## Revisit

- `MAX_FLIP_RATIO=0.5` 是经验值：合法全站收敛批（行都在、大面积 inactive，如 #3262 形态）
  若超半数会被拦，`--force` 越过即可；若该形态常态化，再评估按站调参。
- `missing_rows` 当前只打印；若新站 bootstrap 常态化，可考虑接 #3289 的数据验收面一起报告。
