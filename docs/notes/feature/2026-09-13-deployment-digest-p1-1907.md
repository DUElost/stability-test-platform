# #1907 部署摘要协议 P1 切片：digest 双侧实现 + no-op gate（ADR-0040 D1/D2/D3/D6）

Status: implemented
Class: feature

## Decision

按 ADR-0040 §5.2 P1 最小闭环落地（ADR 已 Accepted v1.0，#1905；P0 已落地
#1904）：

- **D1 身份**：`backend/services/artifact_digest.py`（控制面）与
  `backend/agent/artifact_digest.py`（Agent 镜像）实现 `sha256:<hex>` 摘要，
  输入集 = 载荷文件集的 `(relpath, 可执行位, content sha256)` 规范化序列。
  **输入集与部署输入集由同一份 `host_updater._iter_payload_files()` 生成**——
  tarball 与 digest 共享枚举，契约漂移在共享点消除（Issue 测试计划要求的
  「同一测试守护」由此在结构上成立，另有 tarball 成员反算的契约测试）。
  ADR「resources/mtbf/ 不进任何 artifact」据此同步收紧了 tarball（此前
  mtbf 若出现在源树会被打进包，远端 rsync --exclude 反正不应用）。
- **D2 载体**：远端 `ARTIFACT_DIGEST` 由远端脚本在**探活通过后**经 wrapper
  新子命令 `write-digest`（legacy 主机 sudo tee 等价路径）写入；Agent 启动
  时 `version_info.read_artifact_digest()` 读取、心跳上报 `agent_artifact_digest`
  （与 `script_catalog_version` 同通道同信任模型）；Host 新增显式列
  `agent_artifact_digest`（迁移 `b8c9d0e1f2a3`，additive nullable）；
  控制面 desired digest 现算 + 进程缓存（键 = stat-only 输入集指纹，
  TESTING=1 关缓存，先例 script_catalog_version）。启动读一次即足够新鲜：
  digest 值仅随内容变化，内容变化必然伴随重启（ADR D4）。
- **D3 no-op gate**：`evaluate_convergence(host, force)` desired==current →
  不构建/不传输/不重启/**不占维护窗口与升级门禁**（no-op 不触碰主机），
  返回与 execute_hot_update 同构的 converged 结果。UI/API `?force=true` 与
  `--direct --force` 显式强制（intent/result 审计留痕）。
- **D2 deployed_at 语义修订**：`record_agent_code_deployed` 仅在内容实际
  变更并收敛成功时调用；no-op 不刷新（前端 `HotUpdateResult` 类型同步
  converged/reason/artifact_digest 三可选字段，无 UI 行为改动）。
- **D5 三入口统一**：新增 `finalize_hot_update_outcome()`（agent_version_info），
  UI/API、`--direct`、precheck 回退三处共用同一结果审计（含 converged 留痕）
  + deployed_at 语义 + `stability_hot_update_outcome_total{entry,outcome}` 指标。
  此前 §1.2 事实 4 的分叉（--direct 零审计、precheck 零记录）闭合。
  **precheck 通道按「治愈证据优先」不做 no-op**：它只在轻量脚本推送失败后
  触发（runner.py 唯一调用点），存在内容漂移正证据，digest 相等可能恰是
  §7-3 带外漂移形态；但 digest 仍随部署写入，使另两入口进入 no-op 稳态。
- **D6 可观测**：控制面分段计时（digest/build/connect/upload/remote_total）
  + 远端脚本哨兵（`STP_REMOTE_APPLY_MS`/`STP_RESTART_PROBE_MS`）落审计
  `details.phases`；收敛计数走上述 Prometheus 计数器。
- 批量构建**惰性化**到首个全量部署主机——整批 digest-matched 时零构建零传输
  （P0 的「整批一次构建」在 no-op 稳态下进一步归零；P0 终态出口就此兑现，
  不留双轨）。

## Alternatives

- **tarball 内容直接哈希做身份**（免双侧镜像）：被否——tar 元数据（mtime/
  uid/gid/排序）不稳定，且输入集契约失去「(relpath, exec, sha) 规范化序列」
  的平台无关性，与 ADR D1 定义不符。
- **desired digest 落 host 表**：ADR D2 明确否决（desired 是控制面 artifact
  属性非 host 属性）；进程缓存 + 指纹键已消重复算。
- **precheck 也做 no-op**：见上，治愈证据优先，留给 §7-3 复议触发。
- **远端写 digest 放 restart 前**：探活失败会留下「新内容 + 已写 digest」的
  假收敛态；探活后写保证 digest 只描述健康收敛过的状态。

## Verification

- 新增 `backend/tests/services/test_artifact_digest.py`（8）：双侧 parity
  （含空集/exec 位/元数据与 mtbf 排除/symlink 跳过/增删文件翻转）、
  **tarball 成员反算 digest == 树侧 digest**（输入集==部署集契约）、
  指纹缓存命中/失效、evaluate_convergence 四态。
- `test_host_updater.py`（+6）：远端脚本 wrapper/legacy 双写路径与哨兵次序、
  phase 解析、结果结构扩展、batch 全量路径传 digest、**batch no-op 零构建/
  零 SSH/零门禁**；既有 batch 一次构建测试适配 finalize/digest 断言。
- `test_agent_version_info.py`（+4）：deployed 刷新 / no-op 不刷新 /
  失败不动 / 审计含 digest+phases+entry。
- `test_heartbeat_artifact_digest_1907.py`（2）：落列 + 空值不覆盖。
- `test_hot_update_noop_gate_1907.py`（2）：端点 converged 200（不取凭据/
  不占窗口）+ force 走全量分支。
- `tests/test_agent_priv_write_digest.py`（3）：格式校验/空跳过/受控写入。
- 回归：backend API/services 相关 111 passed；agent 全量 1882 passed、
  23 failed——**与 origin/main 干净基线同红（23/23 同清单，实测复核）**，
  属本底（saq_scan_pipeline/p3_3_multi_instance/legacy_tool_cleanup/cron），
  与本单改动面（heartbeat/version_info/main/digest/host_updater）无交集；
  wrapper 三件 49 passed。
- 迁移（真 PG16 一次性容器 5433 实测）：单头 f3a4b5c6d7e8 → `4c84155b7e59`，
  upgrade 后列在场 → downgrade 回 f3a4b5c6d7e8 列消失 → 复升级，往返全过；
  容器用毕即清。⚠️ 迁移 ID 两轮撞号（b8c9d0e1f2a3/e7f8a9b0c1d2 均已被种子
  迁移占用）——新迁移 ID 必须先对照全量已用 revision 去重，手写模式化 ID
  高危。
- `check:quick`：见 PR（ruff/eslint）。

## Revisit

- 验收中两条需**部署后**复核（issue 5/8 条）：零变更单台 <5s 且不重启、
  批量 converged 比例与 p50——下次 fleet run 以 `duration_ms` + 审计
  `details.phases` 复核（PR 合并不关 issue 的原因，届时评论回填数据）。
- digest 指纹键依赖 mtime_ns/size：mtime 粒度丢失的文件系统（极端）或同
  mtime 同 size 改写属漏判风险面，`--force` 逃生阀兜底；出现实例即按
  ADR §7-3 升级低频校验。
- Agent 侧镜像模块当前仅由 parity test 消费（契约第二锚）+ P2 分层复用；
  若 P2 落地后仍无运行时消费点，按 zombie 判据（#962）重估。
