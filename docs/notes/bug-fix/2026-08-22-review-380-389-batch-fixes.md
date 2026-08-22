# 2026-08-22 变更审查批量修复（#380–#389）

## 背景

对 24h 变更窗口（ADR-0028 Plan A scan→upload→merge 链 + HddSpill 改造 +
backstop 通知）的审查发现 2 高危 / 7 中危 / 若干低危问题，已按主题合并为
7 个 issue（#380 #381 #382 #384 #385 #386 #389）。本 PR 逐项修复。

## 决定了什么 / 放弃了什么

### #380 EventUploader 状态机（H1/H2）

- **in-flight 去重**：`EventUploader._active_ids`（锁保护）保证同一
  event_id 至多一个 job 在队列/在传/退避中；三源（30s 轮询、600s 重试、
  HddSpill）重入队在此收敛。放弃的备选：per-event 文件锁（跨重启语义
  复杂、CIFS 上不可靠）。
- **轮询状态集分工**：30s 快循环只查 `UPLOAD_PENDING`（逃生阀加 LOCAL）；
  `UPLOADING/UPLOAD_FAILED` 归 600s 慢循环。两集合不重叠，`_MAX_RETRIES`
  与退避才真正生效。GET 加 `limit=200`。
- **缺失本地目录 → 终态**：dst 无副本 patch `PULL_FAILED`（不在 pending
  计数集合，merge 等待立即解除）；dst 有副本（prune 后 REMOTE patch
  失败的竞态）patch `REMOTE` 信任远端。放弃新增 `MISSING_LOCAL` 枚举值
  ——要动 enum/前端 types/summary，收益不成比例。
- **有界线程**：dispatch 先拿槽再起线程，线程数 = `_MAX_CONCURRENT` 封顶。

### #381 链完整性（M1/M2）

- **标记范围**：`WHERE state IN (DETECTED,LOCAL,UPLOAD_PENDING,UPLOADING,
  UPLOAD_FAILED)`——拉取中（DETECTED）的事件标记后拉完即待上送，不再依赖
  下一轮增量补标（最终轮没有下一轮）。排除 REMOTE/ARCHIVED/PRUNED（无意义）
  与 PULL_FAILED（无本地数据，标了也会被 Agent 打回）。
- **顺序保证**：`upload_task` 完成标记后写 `run_context.upload_mark`
  （round 级水位线）；`merge_task` 先等水位线（预算 180s）再判 pending，
  消除「标记前 pending==0」的假就绪空报表。放弃 SAQ `depends_on`——
  语义在并发 worker 下不如显式水位线可审计，且链上已有 retry 语义要兼容。
  超时放行（best-effort），缺口由 #300 的 upload_summary 显性化。

### #382 HddSpill 磁盘释放（M3）

- 溢出入队带 `prune_after_upload=True`：上送校验后强制 `_maybe_prune_local`，
  不受 `STP_EVENT_UPLOADER_PRUNE_LOCAL`（默认 0、按机灰度、#217 纪律）约束。
  恢复 #213 改造前「验证拷贝后 rmtree」的磁盘压力阀保证，但收敛到
  EventUploader 单执行者。fleet 灰度开关语义不变（普通事件仍受其约束）。

### #384 backstop 通知（M4/M5/M6 + L-SIGPIPE/L-cancelled）

- PR 过滤：两处 `gh api /issues` 查询加 `select(.pull_request == null)`，
  防 `gh issue close` PATCH `/issues/{n}` 误关带 label 的 PR。
- 空结论门禁：notify-failure 条件改为「非空且非 success/unknown/cancelled」；
  wait 步骤超时路径显式输出 `conclusion=unknown`（infra 错误走 workflow
  自身失败通知，不混进 CI 失败 issue）。
- 失败 run 重派：ensure 步骤只复用「进行中或已成功」的 run，失败/取消的
  次日重新 dispatch——issue 自愈不再依赖外部活动。
- `head -n 1` 换 `--jq '[0].number // ""'`（pipefail 下 SIGPIPE）；
  `cancelled` 不开 issue（人工取消 + 次日重派覆盖）。

### #385 Dependabot ignore 扩全（M7）

- pip ignore 从 5 个封顶包扩到全部 25 个（requirements.txt 里所有带上限的
  包），每个 `versions: [">=上限"]` + `update-types: semver-major`。
  放弃 `increase: "limit"` 全局策略——ignore 清单显式可审计，且与既有
  5 项风格一致。

### #386 dedup_extract（F5/F6）

- 同 basename 多行只把「真正落进 jira 的那一行」（rows[0]）标 ARCHIVED，
  其余保持 REMOTE 并 WARNING + `run_context.extract.same_basename_left_remote`
  计数。放弃内容哈希比对——同名不同内容近乎不可能（时间戳命名），哈希
  成本留给真正需要人工复核的场景。
- 遗留无 DLE 行目录：新增运维 runbook
  `docs/operations/device-log-event-recovery.md`（补行 + 重提取流程），
  不加代码兼容路径（#213 B1 的 DLE-only 是有意边界）。

### #389 低危

- F7：`_validated_remote_path` 更新路径接受本行自己的
  `devices/unassigned/{event_id}/` 旧路径回退（associate 后 Agent 迟到
  patch 不再 400）；他行 unassigned scope 仍 400。
- lock 抬头：恢复 f23218a 丢失的自定义头（py3.11 生成命令 + `--stamp`
  步骤），digest 校验不变。
- LIKE 转义：upload_task 标记模式 `ESCAPE '\'` + `_escape_like`。

## 如何验证

- agent 单测：`backend/agent/tests/test_event_uploader.py`（+6：去重/
  终态释放/缺失目录两分支/spill 强制 prune/轮询状态集）、
  `test_local_disk_monitor.py`（spill 断言 +prune_after_upload）。
- backend 单测：`test_saq_tasks.py`（+4：escape/markable 集合/水位线命中
  与超时）、`test_dedup_extract.py`（+1：同名只标首行）、
  `test_agent_device_log_events.py`（+1：unassigned 回退接受 + 他行 400）。
- `ruff check backend/ tools/ scripts/` 通过；lock digest `--check` 通过。

## 何时重议

- `_UPLOAD_MARK_WAIT_MAX=180s`：若 upload_task 标记在慢 DB 上超预算频繁
  触发 `saq_merge_upload_mark_timeout`，考虑把水位线检查并入 merge 的
  pending 轮询循环（单循环双条件）。
- spill 强制 prune：若灰度中发现「CIFS 事后不可读 + 本地已删」案例，
  回到 #217 的讨论（远端校验加强或延迟删除）。
