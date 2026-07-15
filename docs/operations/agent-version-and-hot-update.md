# Agent 版本、Code Revision 与热更新

> **最后更新**：2026-07-15  
> 安装总览：[../../backend/agent/DEPLOY.md](../../backend/agent/DEPLOY.md) · Ansible：[../linux-agent-ansible-runbook.md](../linux-agent-ansible-runbook.md)

---

## 1. 两个「版本」概念

| 字段 | 来源 | 用途 |
|------|------|------|
| **协议版本** `agent_version` / `agent_protocol_version` | Agent 包 `__version__`，经 heartbeat / claim 上报 | 可选 claim 门禁（`STP_AGENT_MIN_VERSION`） |
| **代码修订** `agent_code_revision` | 热更新写入的 `agent/VERSION`（git short SHA 等） | 与控制面期望对比，展示 drift / matched / pending |

Host UI（`ExpandableHostTable`）展示协议版本、code sync 徽章与相对心跳时间。

---

## 2. 滚动升级顺序（强制建议）

1. **先**热更新 / Ansible 推 Agent（含 `pipeline_schema.json`、`VERSION`）。  
2. 主机页确认 `agent_code_sync_status` 多为 `matched`（或至少已上报 revision）。  
3. **再**在控制面设置 `STP_AGENT_MIN_VERSION`（未设置时门控关闭，旧 Agent 仍可 claim）。  

错误顺序：先升控制面并写死较高 `STP_AGENT_MIN_VERSION` → 旧 Agent claim **426**，PENDING 积压。

---

## 3. 热更新内容

`host_updater._build_tarball` 打包：

- Agent 源码树  
- `stp_schemas/pipeline_schema.json`（安装到 `$INSTALL_DIR/schemas/`）  
- 成功后可写 `agent/VERSION`；`host.extra.agent_code_deployed*` 记部署修订  

UI：主机管理页单机「热更新」；浮动批量栏仅允许 **选中一台 ONLINE** 主机触发热更新（批量安装仍支持多台）。  
CLI：`backend/scripts/batch_hot_update.py`、`tools/ansible/playbooks/update_agent.yml`。

---

## 4. 排障

| 现象 | 检查 |
|------|------|
| claim 426 `AGENT_UPGRADE_REQUIRED` | Agent 协议版本 vs `STP_AGENT_MIN_VERSION`；临时可清空该 env 恢复放行 |
| 心跳正常无任务 | `HOST_ID`、host ONLINE、容量/lease、Agent 是否被门禁 |
| UI 显示 drift | Agent 未上报新 revision；热更新是否写 VERSION；控制面 `get_agent_code_version()` 期望是否刷新 |
| 校验 / schema 不一致 | 热更新是否带上 `pipeline_schema.json`（见 2026-07 host-update 修复） |

环境变量细节：[../development/environment-variables.md](../development/environment-variables.md)。
