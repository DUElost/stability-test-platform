# ADR-0050：audit_logs 保留期与 ADR-0044 D3 安装证据的对齐

- 状态：**Proposed** v0.1（2026-09-19 起草，三选一待 owner 裁决；裁决记录回填 #2789）
- 优先级：P3（读数优雅退化、无数据完整性风险；但两条 Accepted ADR 间存在未成文耦合，先立此防漂移）
- 目标里程碑：M7
- 日期：2026-09-19
- 决策者：owner（DUElost）
- 标签：audit_logs, retention, install-evidence, host, #2789, #2741, #2694
- 关联：[#2789](https://github.com/DUElost/stability-test-platform/issues/2789)
  （发现与证据）/ [ADR-0049](./ADR-0049-audit-log-retention-layering.md)（分层保留期裁决）/
  [ADR-0044](./ADR-0044-agent-install-execution-ownership.md) D3（审计 = 安装状态的持久证据）

## 1. 背景与问题

ADR-0049 落地的分层裁剪（`backend/scheduler/audit_log_cleanup.py`）把
`install_agent` / `install_agent_request` 落入 **business 默认桶（90d）**——两个
显式层（`SESSION_ACTIONS` / `SECURITY_ACTIONS`）均不含它们。而 ADR-0044 D3
裁定安装状态「落库 = 审计，不是 host.extra」（起因：内存注册表重启即失、
`host.extra` 曾被心跳重建覆盖），`backend/api/routes/hosts.py` 的
`_latest_install_audits` 正以这两个 action 派生「最近一次安装运行」状态。

两条 ADR 各自成立、合在一起有一个未定义点：**ADR-0044 D3 的「持久证据」没有
定义保留视界**，而 ADR-0049 给了它 90d 的默认视界。净效果：装机超过 90 天且
未重装的主机，其最近一次安装运行状态不可回溯（`has_run=False` → 读作
"idle"）。ADR-0049 D2 的登记义务只覆盖「新增**安全**相关 action」，不覆盖
「有读取方依赖其保留」的 action——这个盲区就是本 ADR 的对象。

**影响面的事实边界**：`out.agent_installed`（装没装上的布尔事实）走
`host.extra.agent_installed[_at]`（心跳 keep-list，不依赖审计），**不受影响**；
受影响的只是「最近一次安装运行」这一面（成功/失败/丢失的可见性）。

## 2. 决策（三选一，待 owner 裁决）

### 丙：明示接受 90d 视界（推荐）

ADR-0044 D3 补注「持久 = 审计保留期视界内（business 默认 90d，env 可调）」；
`hosts.py` 安装状态派生函数的 docstring 标注同一视界。零迁移、零行为变更。

理由：

1. 安装**运行状态**的取证价值天然短程——它回答「最近一次装没装成」；
   跨季度的运行明细不是现有任何消费方的读法（全仓 grep 无其它读取方）；
2. 布尔事实（装没装上）已在 `host.extra` 无界保留，安装与否的长期事实不丢；
3. 退化方向是优雅的：视界外读作 "idle"（无运行），不是误报失败；
4. business 层的 90d 本就是运维旋钮（`AUDIT_LOG_BUSINESS_RETENTION_DAYS`），
   有更长需求改配置即可，不需结构变更。

### 甲：`install_agent*` 登记进 security 层

`SECURITY_ACTIONS` 加两个 action，一行改动，视界升到 180d。代价：install
事件不是「账号/凭据安全事件链」，塞进 security 层稀释 D1 的语义轴；且 180d
仍是有限视界，问题只是被推迟、未定义点依旧存在。

### 乙：安装运行事实源迁移专表（console 落库）

新建安装运行表（console 终态写库），修订 ADR-0044 D3 为「审计 = 短视界事实，
专表 = 无界安装历史」。结构上最正确：历史无界、可结构化查询、与保留期解耦。
成本：新表 + 迁移 + console 写点 + 读路径切换。ADR-0044 §5 已论证「真需要
『安装历史』应当由审计承载」——在产品提出安装历史查询需求之前，现在付这个
成本属于超前建设。

## 3. Consequences（按推荐项丙）

- 正面：跨 ADR 耦合成文（D3 的「持久」有了明确视界定义）；#2789 的盲区关闭；
  零迁移风险。
- 负面/代价：>90d 的安装运行状态不可查（同 ADR-0049 §4 对 business 层的一般
  性代价，此处只是把它显式化到安装面）。
- 中性：若未来需要更长视界，先调 env；需要结构化安装历史时，乙回归为正解。

## 4. Revisit

- owner 裁决后：把选定项回填为本 ADR 的 Accepted 决策，并同步 ADR-0044 /
  ADR-0049 的关联行与实现（甲/乙各有对应代码面，丙仅 docstring）。
- 产品提出「安装历史」查询/报表需求 ⇒ 乙升级为正解，届时连同 console 写点
  一起设计。
- `SECURITY_ACTIONS` / 读取方新增依赖审计无界保留的 action 时 ⇒ 先回到本
  ADR 的「视界定义」检查一遍，而不是默认「持久 = 永久」。
