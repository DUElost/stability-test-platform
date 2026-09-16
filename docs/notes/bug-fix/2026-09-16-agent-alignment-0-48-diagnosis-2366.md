# 「Agent 已对齐 0/48」只读分诊：判据未坏，读数反映观测时点的真实落后（#2366）

Status: implemented
Class: bug-fix

## Decision

**结论：不是口径 bug。** 只读实测（生产控制面只读 SELECT + 控制面本地 desired digest 现算，
全程未改任何状态）：

| 项 | 值 |
|---|---|
| 未退役 host | 48（全部 ONLINE） |
| 上报 digest == **当前** desired（`sha256:1d55f939…`） | **14 台** |
| 上报 digest == 更早一版（`sha256:eb1c96a1…`） | 34 台 |
| 热更新台账 | 14 台 = 09-16 10:13 `b7917bc6`；33 台 = 09-15 12:49 `566f4f3d`；1 台 = 09-15 13:06 `143be22a` |

判据链逐段核对：前端只统计「ONLINE 且 `agent_installed`」中
`agent_code_sync_status === 'matched'` 的台数（`ExpandableHostTable.tsx`），后端用
`host.agent_artifact_digest` 与**列表请求现算**的 desired digest 比对
（`hosts.py::_list_desired_artifact_digest`，#2320 起读失败降级 `unknown`）；判据是
**digest 而非 revision**（ADR-0040 v1.1）✓；分母 48 ✓。因此**观测时点的 0/48 是真值**
（那批 10:13 热更新之前，没有任何主机持有当时的 desired digest），**同一页面今天的正确读数
是 14/48**。

**展示面收口（本单按 issue 指引「转为展示问题」）**：只给「已对齐 N/M」时，全队落后一版
与「判据坏了」在界面上一模一样 ✗。故在主机页汇总行把落后台数一并渲染：
`Agent 已对齐 14/48 · 34 台待热更新`。`unknown`（未上报 digest）**不计入**「待热更新」——
它的运维动作是「等一次心跳 / 首次 `--force` 迁移」（与 `drift` 不同，ADR-0040 v1.1 已有
明文区分）。

**现场行动项（不在本单，供运维排期）**：34 台仍停在 09-15 那一版，需要排一次 fleet 热更新
才会转 `matched`。

## Alternatives

- **A. 只回结论、不改展示**：可行（本单的主体是分诊 ✓），但 issue 自己写了「若属真实状态则
  本单转为文档/展示问题（0/48 的观感易误报）」——把「待热更新」显式渲染是最小且对症的收口。
- **B. 把 `unknown` 也算进「待热更新」**：否决。`unknown` = 未上报 digest（#1907 前部署 /
  新装未心跳），运维动作是「等一次心跳」而不是「推一版」；混在一起会让计数失真（#2155 已把
  这组语义钉在前端）。
- **C. 改判据（例如把 revision 也纳入比对）**：否决。ADR-0040 v1.1 明文「revision 不参与
  判等」——任何不动 `backend/agent/**` 的提交都会让 revision 前进而 digest 不变（#2057 的
  假 drift 根因）。
- **D. 顺手把 34 台热更新掉**：不做（属运维动作，且热更新会重启服务、受维护窗口/活跃作业
  约束；本单是只读分诊）。

## Verification

- **只读事实**（per `production-diagnostics.md` SOP：DSN 仅经环境变量、全程 `SELECT`、
  输出不含连接串）：
  - `SELECT agent_artifact_digest, count(*) ... GROUP BY 1` → 14 / 34 两组；
  - `SELECT extra->>'agent_code_deployed', max(..._at), count(*) ...` → 与两组一一对应；
  - `status` 全为 ONLINE ✓（页面的分母 `ONLINE && agent_installed` = 48 ✓）。
- **本地 desired digest**：在同一工作树（生产 `WorkingDirectory`）算得 `sha256:1d55f939…`，
  与 14 台的上报值逐字符相等 ✓。
- **前端**：`vitest run src/components/network` → **27 passed**（新增 1 条：`matched/drift/unknown`
  三态混排时汇总为 `已对齐 1/3 · 1 台待热更新`）；`tsc --noEmit` 通过；`eslint --max-warnings 0`
  通过。
- **未做**：未调用管理 API 读页面响应（本机只读通道走 DB + 本地现算，避免取用管理员凭据）——
  结论建立在「判据链逐段核对 + 两侧数值实测」上，已足够区分「真实状态」与「口径 bug」。
- **未跑**：`check:quick` 全档（改动面为前端一个组件 + 其测试，交 CI）。

## Revisit

- **34 台的收敛**：本单只诊断，不做热更新。若下次再看该页面仍显示「N 台待热更新」且台账
  显示已推过——那就说明推动作没生效（回到热更新链排障，而不是显示面）。
- **「已对齐」的分母**：目前是「ONLINE 且 `agent_installed`」。若将来出现「装了但从未上报
  digest」的长期形态（`unknown` 恒久），要不要把它单列一类（例如「N 台待心跳」）取决于现场
  是否真出现——现在不预先加。
- **同类分诊的取证姿势**：本单的「只读 SELECT + 本地现算 desired 对拍」可用于任何「页面读数
  与台账不符」的问题；注意不要为了读页面响应去取管理员凭据（本机有只读通道）。
