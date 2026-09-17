# #2188 D 步 单3：run_scan_sync 读侧清单注册 + per-host 过渡降级

Status: implemented
Class: feature

## Decision

`backend/services/dedup_scan.py` 的 `run_scan_sync` 由「全目录 legacy glob」改为
**清单优先 + per-host 降级**：

1. **新增 `_load_meta_shards`**：读 `{nfs_root}/_meta/{plan_run_id}/*.json` → `{host_id: shard}`，
   容忍坏分片（非 JSON / 结构不符 / schema 版本不符 → 跳过 + WARNING）；
2. **新增 `_register_scan_artifacts_from_meta`**：对 `registerable` 条目注册，
   `host_id` **取自分片字段**（不再解析文件名）、`storage_uri` 由 `file_key` 拼装为
   **与 legacy `str(路径)` 完全同形** → 过渡期两路共享幂等键 `(plan_run_id, storage_uri)`；
3. **新增 `_register_legacy_for_host`**：按 `{host_id}_` 前缀**只注册该 host** 的 glob 命中；
4. **新增可选参 `expected_hosts`**（`Collection[str] | None = None`）：
   - 分片存在 → 清单注册；
   - **分片缺失且其 glob 有文件** → 该 host 走 legacy 注册 + `legacy_glob_register_total` 日志；
   - **分片缺失且 glob 无文件** → 「还没写」，不计数（轮询预算内继续等，现行为）；
   - **缺省 `None` + 无分片** → 保持原全目录 glob（向后兼容既有调用点）。

`backend/tasks/saq_tasks.py` 两处 `run_scan_sync` 调用点补 `expected_hosts=triggered`
（`scan_task` 已持 per-host 触发清单，`triggered = [host_id for host_id, _ in triggered_rows]`）。

## 与设计稿的对应

| 设计稿 | 落点 |
|---|---|
| §3 清单聚合读 + `file_key` 拼 `storage_uri` 同形 | `_load_meta_shards` / `_scan_storage_uri` |
| §3 host/platform 来自分片字段，`_HOST_PREFIX_RE` 退出主路径 | `_register_scan_artifacts_from_meta`（实测该正则仅存于 legacy 过渡路径） |
| §3 新增 `expected_hosts` | 签名第 4 参 |
| §4 per-host 降级 + `legacy_glob_register_total` | `_register_legacy_for_host` + 计数日志 |
| §4「还没写」vs「这一版不写」 | 分片缺 + glob 命中与否，见上 ③/④ |
| §7 R-4 ①（零 glob）/ ②（无 `_HOST_PREFIX_RE`） | 契约测试 6 例 |

## 一处**超出设计稿但必要**的补充：缺省 `None` 的兼容分支

设计稿 §3 写「缺省 None 时行为 = 纯清单注册（向后兼容既有调用点）」。
我实现后发现该表述会**窄化既有覆盖面**：既有调用点（如单测、或尚未传
`expected_hosts` 的路径）在**清单尚未落地**的 run 上将注册到 **0 个产物**——
而修复前它们靠全目录 glob 是能注册到的。

故补一条：**`shards` 为空 且 `expected_hosts is None` → 保持原全目录 glob**。
理由：过渡期的原则是「**不得因新路径未就绪而丢失原有覆盖面**」（与 #2269
「让错误状态不产生」同向）。`expected_hosts` 由 `scan_task` 显式传入时
（生产路径）才启用 per-host 语义，故不影响设计稿意图。

## Alternatives

- **不读分片，继续全目录 glob** → 否决：正是本单要消除的（`_HOST_PREFIX_RE`
  解析文件名、每轮全目录扫描）。
- **读分片，但降级时仍全目录粗扫** → 否决：设计稿 §4 明确「仅该 host」；
  全目录粗扫会让**旧 Agent 的 host 掩盖新 host 的缺失**（无法定位降级对象）。
- **缺省 None 时也走纯清单**（严格照设计稿字面）→ 否决：见上「超出设计稿但必要」，
  会窄化既有覆盖面。已在 Agent Note 显式记录该偏离及理由。
- **新增 env 键做版本开关** → 否决：设计稿 §4 与 ADR-0033 §5.4 明确
  「不建版本注册表、不新增 env 键」。

## Verification

- `./scripts/run_pytest.sh backend/tests/services/test_dedup_scan_merge.py -q`
  → **62 passed**（含新增 6 例契约测试）；
- **红绿双向**：把 `run_scan_sync` 还原为「纯 legacy glob」→
  **4 例红灯**（`meta_shard_registration…` / `meta_shard_path_does_not_glob…` /
  `missing_shard_falls_back…` / `non_registerable_entries…`）；
  另 2 例（`no_manifest_and_no_expected_hosts…` / `broken_shard_is_skipped…`）
  断言的是**向后兼容行为**，修复前也成立，故预期仍绿；
- **R-4 ①**：`test_meta_shard_path_does_not_glob_dedup_dir` 以 spy 记录对 `dedup/`
  的 `Path.glob` 调用，断言 **0 次**；
- **R-4 ②**：实测 `_HOST_PREFIX_RE` 仅出现在 legacy 注册函数内，清单主路径无引用；
- **主机归属正确性**：`test_meta_shard_registration_uses_shard_fields_not_filename`
  故意让**文件名前缀与分片 host_id 不一致**，断言注册的 `host_id` 取自分片
  ——若读侧仍解析文件名即失败；
- **降级隔离**：`test_missing_shard_falls_back_to_per_host_legacy_glob` 断言
  「只有缺分片的 host 经 glob 注册」，另一 host 的文件**不得**被注册；
- `ruff check` 三文件 → All checks passed。

## Revisit

- **终态出口**（设计稿 §4）：`legacy_glob_register_total` 计数**连续 30 天为 0** →
  单 5 删除 legacy 路径 + 谓词 shim，R-4 契约测试同步收紧为「无任何中心盘遍历」。
  本单只落**计数与降级**，未删 legacy（显式过渡，不留无期限双轨）。
- **`expected_hosts` 的生产覆盖面**：本单只改了 `saq_tasks.py` 的两处调用点。
  若还有其它 `run_scan_sync` 调用点（如手动 API），它们仍走缺省 `None`
  （全目录 glob）——这是**有意**的保守选择。触发条件：若发现某调用点需要
  per-host 语义，再按需传入。
- **坏分片的可观测性**：当前只 WARNING + 跳过。若现场出现**持续**坏分片
  （如写侧中断导致），应评估是否升级为指标（当前判断：写侧原子替换已消除该形态，
  坏分片只可能来自外部损坏，WARNING 足够）。
- **未做真实环境验证**：本单为**代码 + 单测**层面落地，未在真实中心存储上验证
  新旧 Agent 混编 run 的降级行为（需真实旧版 Agent）。触发条件：混编 run 出现时
  核对 `legacy_glob_register_total` 是否符合预期。
