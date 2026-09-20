# 重复 merge 的产物语义：允许覆盖 + 登记行同步（#2888）

Status: implemented
Class: bug-fix

## Decision

现象（逐行核实）：中心发布用**固定路径** + `copytree(dirs_exist_ok=True)`
（`dedup_scan.py` 的 `_publish_merge_to_center`），产物名稳定
（`Result_MergeFiles*.xls`）⇒ 同一 run+platform 的第二次 merge **覆盖**中心同名文件；
而登记侧 `_register_merge_artifacts` 的幂等键是 `(plan_run_id, storage_uri)`，URI 不变
即 `continue` ⇒ **磁盘内容已换、`size_bytes` 停在旧值**（「DB 说 X、盘上 Y」，按 size
对账会得到错结论）。

裁决（2026-09-20，owner 选定方向）：**允许覆盖**，并把语义显式化——

1. **同一 URI = 该 run+platform 的最新 merge 结果**（不再隐含「一份历史」）；
2. 命中既有行时按**当前文件**重新取尺寸，`size_bytes` 随之刷新；旧值进
   `merge_artifact_overwritten` warning 留痕（`uri` + `old_size` + `new_size`）；
   表内无「上一版尺寸」列，日志即留痕（未加列，避免为一个 P3 上迁移）；
3. 幂等键与唯一约束**不变**（不产生重复行）；返回值仍是**新增行数**（调用方只用于
   日志，语义不变）；
4. 尺寸未变（幂等重跑）时**不写库、不告警**；
5. 顺带覆盖历史 `size_bytes IS NULL` 的行（同一条刷新路径，此前永远补不上）。

语义写进权威文档 `docs/design/2026-scan-upload-merge-contract.md` 的新节
「重复 merge 的产物语义（#2888 裁决）」。

## Alternatives

- **(a) 拒绝二次 merge（fail-closed）**：语义最明确，但「修完输入想重跑 merge」这类
  正常操作会变成失败，需要另设逃生门（force 参数或人工清理），失败信息还得能指导操作者
  ——改动面比 (c) 大而收益是「更严格」，与本单的 P3 定位不符。
- **(b) 中心路径版本化（`merge/{ts}/`）**：根治「同一 URI 两份内容」并保留历史，
  `storage_uri` 天然唯一 ⇒ 现有判重逻辑自动正确。代价是中心存储布局变更，外溢到
  jira 附件路径解析、`dedup_extract` 读 merge xls、运维对账与手册——属方向级，需要 ADR
  与运维确认；本轮不做，Revisit 留名。
- **不改**：DB 与磁盘的静默不一致长期化，任何按 size 的对账/审计都会读出错结论。
- **顺带加内容指纹（sha256）列**：能回答「内容是否真的变了」，但要迁移 + 回填历史行，
  超出本单；Revisit 记。

## Verification

- **反例优先（先证伪再采信）**：把 `dedup_scan.py` 退回 `HEAD` 后跑新增用例 →
  `test_merge_artifact_register_refreshes_size_on_overwrite`（size 停 100）与
  `test_merge_artifact_register_backfills_null_size`（None 不补）**双双 FAILED**；
  `test_merge_artifact_register_noop_when_size_unchanged` 两版都过（幂等语义未变）。
  恢复后全绿。
- 新增三条用例：① 覆盖刷新（含 warning 文案 `old_size=100 new_size=250` 与「不产生
  重复行」断言）；② 尺寸未变 ⇒ 不告警；③ 历史 NULL 尺寸回填。
- `TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest
  backend/tests/services/test_dedup_scan_merge.py -q` → **59 passed**（含既有回归）
- `python scripts/run_gates.py check:quick` → 见 PR。

## Revisit

- **历史并存的出口**是 (b) 版本化路径：真要做时按 ADR 走，并同时核 jira 附件与
  `dedup_extract` 的读取面。
- **内容指纹**：目前只对账尺寸，尺寸相同而内容不同的覆盖不会被发现（`storage_uri`
  语义已声明为「最新」，故不算错，但审计上无法分辨）。若要更强，加 `content_sha256`
  列 + 回填。
- 留痕只在 journal（`merge_artifact_overwritten`）：日志轮转后旧值不可追；若这类覆盖
  日后需要长期审计，应升级为表内历史列或事件表。
- 本单只改登记路径，**不动**中心文件命名与目录布局——`jira/{run_id}/`、`devices/…`
  等引用方零改动。
