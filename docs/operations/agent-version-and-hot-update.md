# Agent 版本、Code Revision 与热更新

> **最后更新**：2026-07-15  
> 安装总览：[../../backend/agent/DEPLOY.md](../../backend/agent/DEPLOY.md) · Ansible：[../linux-agent-ansible-runbook.md](../linux-agent-ansible-runbook.md)

---

## 1. 两个「版本」概念

| 字段 | 来源 | 用途 |
|------|------|------|
| **协议版本** `agent_version` / `agent_protocol_version` | Agent 包 `__version__`，经 heartbeat / claim 上报 | 可选 claim 门禁（`STP_AGENT_MIN_VERSION`） |
| **代码修订** `agent_code_revision` | 热更新写入的 `agent/VERSION`（git short SHA 等） | **纯溯源展示**（ADR-0040 v1.1）——**不参与 drift 判定** |
| **部署摘要** `agent_artifact_digest` | 远端 `agent/ARTIFACT_DIGEST` 经心跳上报（ADR-0040 D1） | 与控制面现算 desired digest 对比——**drift / matched 的唯一判据** |

> **判据唯一性（ADR-0040 v1.1，#2057）**：`agent_code_sync_status` 只由 digest 产生。
> `expected_code_revision` 取的是**仓库 HEAD**，任何不动 `backend/agent/**` 的提交都会让
> revision 前进而 digest 不变；按 revision 判等会让全 fleet 假 drift，且热更新回
> `converged(digest-matched)` 也不会消掉徽章（唯一出口只有 `--force`）。

Host UI（`ExpandableHostTable`）展示协议版本、部署摘要、code sync 徽章与相对心跳时间。

---

## 2. 滚动升级顺序（强制建议）

1. **先**热更新 / Ansible 推 Agent（含 `pipeline_schema.json`、`VERSION`）。  
2. 主机页确认 `agent_code_sync_status` 多为 `matched`；`unknown` = 未上报 digest（#1907 前部署 / 新装未心跳），需一次 `--force` 迁移写入身份文件。  
3. **再**在控制面设置 `STP_AGENT_MIN_VERSION`（未设置时门控关闭，旧 Agent 仍可 claim）。  

错误顺序：先升控制面并写死较高 `STP_AGENT_MIN_VERSION` → 旧 Agent claim **426**，PENDING 积压。

> **⚠️ 下发文件 ≠ 生效（2026-08-04 实测）**：`pipeline_schema.json` 与脚本目录的更新
> **必须重启 Agent 进程**才生效 —— `pipeline_validator._schema_cache` 是进程内缓存，
> `reload_config` 不重载它；脚本目录通知（#112 的 catalog digest 对比）只覆盖脚本
> 目录，**不覆盖** `schemas/`。2026-08-03 验证轮因此全量失败过一次
> （`stall_seconds` 被旧 schema 拒）。热更新部署后务必 `systemctl restart stability-test-agent`。
> 同理，手动 scp 下发 schema/脚本后也要重启，不能只发文件。

---

## 3. 热更新内容

`host_updater._build_tarball` 打包：

- Agent 源码树  
- `stp_schemas/pipeline_schema.json`（安装到 `$INSTALL_DIR/schemas/`）  
- 成功后可写 `agent/VERSION`；`host.extra.agent_code_deployed*` 记部署修订  

**`.env` 白名单合并**（每次热更新自动执行，不全量覆盖）：

**不同步（每台机器独有，热更新绝不改写）**：`HOST_ID`、`API_URL`、`ANDROID_ADB_SERVER_PORT`、`ADB_PATH`、`MOUNT_POINTS`、`STP_AEE_LOCAL_ROOT`（主机本地 L1 路径：NVMe+HDD 与纯 SSD 机型不同）、`AGENT_SECRET`（仅 `sync_agent_secret=true` 时单独更新）等。完整列表见 `backend/services/agent_env_sync.py` 的 `PROTECTED_ENV_KEYS`。

**批量同步**：

| 类别 | 键 | 值来源 |
|------|-----|--------|
| 安装布局 | `AGENT_INSTALL_DIR`、`AIMONKEY_RESOURCE_DIR`、`LOG_DIR`、`PYTHONPATH` | `$INSTALL_DIR` 派生 |
| 舰队默认（两边同值） | `STP_AEE_NFS_ROOT`、`STP_DEDUP_SCAN_TAG`、`STP_DEDUP_AUTO_SCAN`、`LOG_LEVEL`、`STP_WATCHER_ENABLED`、`STP_DEVICE_LOG_EVENT_ENABLED` | 控制面进程环境非空时原样下发 |
| Agent 映射键 | Agent 的 `STP_DEDUP_SCAN_PYTHON` / `STP_DEDUP_SCAN_SCRIPT`、`PIP_INDEX_URL` | 分别来自控制面 `STP_AGENT_DEDUP_SCAN_*`、`STP_AGENT_PIP_INDEX_URL`（控制面本机路径**不**原样下发） |

实现：`backend/services/agent_env_sync.py`（allowlist + 行级 merge）。  
响应字段 `env_keys_synced` 列出本次已对齐的键。

**部署摘要协议（ADR-0040，P1 #1907）**：部署单元身份 = 内容摘要
`sha256:<hex>`（输入集 = 载荷文件集的 `(relpath, 可执行位, content sha256)`
规范化序列，与 tarball 共享同一枚举）。收敛流程：

1. 控制面现算 desired digest（进程缓存，键 = 输入集指纹）；
2. 与 host.agent_artifact_digest（心跳上报的远端 current）比对——相等即
   **no-op**：不构建、不传输、不重启，审计 outcome=`converged`，
   `agent_code_deployed_at` 不刷新；
3. 不等则全量部署；远端探活通过后经提权 wrapper `write-digest`（legacy 主机
   走等价 sudo tee）受控写入 `agent/ARTIFACT_DIGEST`，Agent 启动时读取并经
   心跳上报。

三入口（UI/API、`batch_hot_update.py --direct`、precheck 回退）共用同一判定
与同一记录通道（`finalize_hot_update_outcome`：审计 + deployed_at 语义 +
`stability_hot_update_outcome_total{entry,outcome}` 指标）。差异：precheck
回退**不做 no-op**（仅在轻量脚本推送失败后触发，治愈证据优先，§7-3）；Ansible
通道本切片不变（P2 归位）。`--force`（API `?force=true` / CLI `--force`）跳过
判定强制全量。`resources/mtbf/` 永属主机本地，不进载荷与身份。

**顺序（#218，避免 Wave 3 竞态）**：

1. 远端脚本**先**行级合并 `$INSTALL_DIR/.env`，**再** `systemctl restart` —— 一次成功的 hot-update 重启后进程已读到新 flag，无需再 `reload_config`。  
2. 若只改 `.env`、不走 hot-update：先确认文件已写入，再发 `reload_config`。  
3. **不要**在 hot-update 尚未返回成功时抢先 `reload_config`（曾出现 restart 后立刻 reload 读到旧/空 flag → `event_uploader_configured enabled=False`）。  

UI：主机管理页单机「热更新」；浮动批量栏支持多选主机的**安全批量热更新**（受控并发 2，完成后汇总成功/跳过/失败），批量安装同样支持多台。

CLI：`backend/scripts/batch_hot_update.py`、`tools/ansible/playbooks/update_agent.yml`。

**Ansible 通道身份簿记（ADR-0040 §5-3，#1997）**：`update_agent.yml` 的 rsync
覆盖两层载荷（代码树 + schema + `resources/**` 除 `mtbf/`），health 验证通过
后经 `tools/ansible/compute_deploy_digest.py`（stdlib-only，复用 Agent 镜像
算法）现算双身份并写入远端 `ARTIFACT_DIGEST` / `ARTIFACT_DIGEST_RESOURCES`
（resources 分区为空时跳过写）。排除集契约与部署 digest 输入集对齐
（`test_*.py` 宽模式、`venv//logs/`、双身份文件 exclude+protect）。

**升级门禁与维护窗口（#960 / #1249，所有入口统一）**：升级前必须经控制面
`POST /api/v1/agent/hosts/{id}/upgrade-gate` 申请门禁——该 host 有活跃 Job 时默认 **409**；
`abort_running_jobs=true` 先排空再进。结束后 `POST .../upgrade-gate/release` 按 holder
释放（幂等、不误清他人窗口）。窗口内该 host 不派发新 Job、不被 claim；控制面不可达时
**fail-closed** 拒绝升级。UI / CLI（`batch_hot_update.py`）/ Ansible（`update_agent.yml`）
三条入口共用 `backend/services/host_upgrade_gate.py` 单一实现，审计 `upgrade_gate_acquire/release`。

---

## 4. 排障

| 现象 | 检查 |
|------|------|
| claim 426 `AGENT_UPGRADE_REQUIRED` | Agent 协议版本 vs `STP_AGENT_MIN_VERSION`；临时可清空该 env 恢复放行 |
| 心跳正常无任务 | `HOST_ID`、host ONLINE、容量/lease、Agent 是否被门禁 |
| 升级被拒（409 / 门禁不可达） | 该 host 是否有活跃 Job（需 `abort_running_jobs=true` 排空）；控制面是否可达（不可达 fail-closed）；维护窗口 `host.maintenance_until/holder` 是否被他人持有 |
| UI 显示「内容漂移」 | 判据是 digest：`host.agent_artifact_digest` ≠ 控制面现算 desired（ADR-0040 v1.1）。检查远端 `agent/ARTIFACT_DIGEST` 是否写入并随心跳上报；**revision 不等不再构成 drift**（期望修订取仓库 HEAD，见 §1） |
| UI 显示「未知」 | 主机从未上报 digest（#1907 前部署 / 新装未心跳）→ 等一次心跳，或首次 `--force` 迁移一次写入身份文件；**不是**待更新 |
| 每次热更新都全量（不 no-op） | 远端 `agent/ARTIFACT_DIGEST` 是否存在且被心跳上报（`host.agent_artifact_digest` 非空）；digest 判定见 ADR-0040；带外改文件属信任模型例外（§7-3） |
| Ansible 更新后仍 drift 一轮 | `update_agent.yml` 是否跑到了「Write agent ARTIFACT_DIGEST(_RESOURCES)」任务（health 通过后才写）；`compute_deploy_digest.py` 是否与控制面同 checkout 现算；旧 playbook（< #1997）不写身份文件 |
| 校验 / schema 不一致 | 热更新是否带上 `pipeline_schema.json`（见 2026-07 host-update 修复） |

环境变量细节：[../development/environment-variables.md](../development/environment-variables.md)。

---

## 5. 回滚与演练记录（2026-08-26 裁决）

- **回滚形态**：对目标 host 重跑指向旧 code revision 的热更新（同一流程），`.env`
  键值不动。回滚路径与本文件 §2 是同一条路——它就是「最被练过的路径」。
- **回滚执行前置检查**（2026-08-27 就绪性审计结论，详见
  [`2026-08-27-agent-rollback-readiness-audit.md`](./2026-08-27-agent-rollback-readiness-audit.md)）：
  ① host 侧五项就绪（VERSION 可读 / `.env` 存在 / schemas 存在 / 服务可重启 /
  磁盘充足——34/34 已验）；② 目标 revision 在 git 历史可得；③ **控制面 agent
  源码树先切到目标 revision**（`_build_tarball` 打包当前 HEAD，无一键入口——
  此步骤是回滚链唯一非原子环节）。
- **演练策略**：部署是偶发手工动作而非持续交付，不设主动演练排期；**每次真实
  回滚完成后在本节末尾追加一行记录**（日期 / 触发原因 / 波及 host 数 / 耗时 /
  是否一次成功）。历史积累即演练库；出现「连续两次回滚不顺」再升级为正式演练
  排期。

| 日期 | 原因 | hosts | 耗时 | 一次成功 |
|------|------|-------|------|----------|
| （暂无——首次真实回滚后填写） | | | | |
