# #2540 与 #2534 重叠：读侧清单注册以 main 为准

Status: implemented
Class: feature

## Decision

`fix/2475-manifest-read-side`（PR #2540 / Closes #2475）与已合入的
`fix/2188-unit3-manifest-reader`（PR #2534，merge `7ff691b8`，~2026-09-17T08:04:14Z）
同为实现 #2188 单3 / 设计稿 #2466 §3/§4/§7 的读侧清单优先 + per-host legacy 降级。

合并 `origin/main` 进本分支时，唯一内容冲突在 `backend/services/dedup_scan.py`。
两边行为同构（清单聚合 → registerable 注册 → 缺分片 host 走 legacy glob +
`legacy_glob_register_total`；`expected_hosts=None` 保持全目录 legacy），实现细节
不同（helper 命名、分片展平形态、是否经 `_insert_artifact_row`）。按「非语义冲突
优先已合入实现」：

- **保留** main/#2534：`_read_manifest_shards` / `_register_manifest_artifacts` /
  `_sync_scan_artifacts` 与 `backend/tests/services/test_scan_manifest_reader_2188.py`
  及 `saq_tasks.py` 的 `expected_hosts=triggered` 调用点；
- **丢弃** #2540 侧：`_load_meta_shards` / `_register_scan_artifacts_from_meta` /
  `_register_legacy_for_host` 及挂在 `test_dedup_scan_merge.py` 末尾的 6 例重复契约测试。

本 note 原描述 #2540 独有实现；现改为记录本次冲突裁决，避免留下与 main 事实不符
的「第二套」设计叙述。

## Alternatives

- 保留 #2540 helper 并改写 #2534 测试：会发明第三套变体，且推翻刚合入的实现。
- 手工拼合两边 helper：无行为收益，只增加漂移面。
- 直接关闭 #2540 不修冲突：`mergeable_state=dirty` 会一直挡队列与人工审阅。

## Verification

- `git diff origin/main -- backend/services/dedup_scan.py backend/tasks/saq_tasks.py
  backend/tests/services/test_dedup_scan_merge.py` 为空（实现与重复测试均已对齐 main）
- `./scripts/run_pytest.sh backend/tests/services/test_scan_manifest_reader_2188.py -q`
- `python scripts/run_gates.py check:quick`

## Revisit

若确认 #2540 相对 main 仅剩本 note 或空 diff，可关闭 PR（功能已由 #2534 覆盖，
#2475 随 #2534 落地）。勿再对读侧清单注册做平行实现。
