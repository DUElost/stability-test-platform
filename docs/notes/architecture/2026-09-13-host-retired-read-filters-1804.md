# ADR-0038 ③：退役主机在读面的过滤（#1804）

Status: implemented
Class: architecture

## Decision

按 ADR-0038 v0.2（D5 不变量 1）把「退役 = 不再是容量」落到各读面；依赖 ① 的四列
（本分支堆叠在 ② 之上，② 又堆叠在 ① 上）。

| 读面 | 处理 | 位置 |
|---|---|---|
| `GET /api/v1/hosts` | **默认排除**退役；新增 `include_retired=true` 才显示；`total` 与列表同口径 | `routes/hosts.py` `list_hosts` |
| host 详情 `GET /hosts/{id}` | **保留可见**（含 D6 身份字段）——退役是历史终态，要能查痕迹 | 同上（不改，测试锁定） |
| file-server「活跃 Agent」 | 排除（在线 ∧ 心跳新鲜 ∧ 未退役） | `routes/stats.py` file-server |
| 仪表板容量（raw SQL） | 排除（`WHERE retired_at IS NULL`）——分布/资源均值不受退役主机污染 | `routes/stats.py` dashboard-summary |
| 历史失败率 KPI | **保留**（裁决 D-5）：历史事实 ≠ 当前容量；代码注释写明口径差异 | `routes/stats.py` host-failure-rate |
| Prometheus 舰队 gauge | 排除退役（选「明确排除」而非新增标签）：`stability_host_online` 在退役时刻出现台阶，告警按规模阈值判断会正常触发「规模下降」；影响写在 `_FLEET_GAUGES` 注释 | `routes/metrics.py` |
| AI 助手读面 | `_q_platform_health` 计数与 `_q_hosts` 列表均排除（助手无 `include_retired` 开关，遍历读面不列退役） | `services/ai_assistant/tools.py` |

`_FLEET_GAUGES` 从三元组扩为四元组（+可选 filter），设备侧传 `None` 保持原行为。

## Alternatives

- **Prometheus 侧新增 `retired` 维度标签而非排除**：弃——`stability_host_online`
  的既有 PromQL/仪表板都按 `status` 聚合，加标签会让所有查询需要重写；「退役不是
  容量」用排除语义更直接，台阶效应已写入注释；
- **`GET /hosts` 用 `status` 或 `include_retired` 之外的做法（如 `retired` 过滤器）**：
  弃——issue 明确 `include_retired` 契约；
- **历史失败率也排除退役**：弃——裁决 D-5 定「历史 KPI 保留」，排除会让退役变相
  抹掉历史运维事实；
- **AI 助手加 `include_retired` 参数**：弃——工具面越简单越安全（助手工具的参数
  白名单已有校验成本），退役痕迹走详情页/审计；
- **file-server 端点用「退役后 Agent 仍在心跳」的例外口径**：弃——D5 明列「不计入
  在线容量与库存统计」，而 file-server 的活跃 Agent 正是容量合规面。

## Verification

- **新增 10 例**（`backend/tests/api/test_host_retirement_read_filters_1804.py`）：
  默认隐藏 / `include_retired` 显式显示（含退役字段回传）/ 分页 `total` 同口径 /
  详情保留可见 / file-server 活跃集捕获断言 / 仪表板**具体计数**（total=2、online=1、
  offline=1）/ 历史失败率保留退役主机 / Prometheus gauge 值=1 /
  AI 健康计数（`'ONLINE': 1`）/ AI 主机列表隐藏退役；
- **反例实证逐面**（临时移除五处过滤）：**7 failed**——列表默认隐藏、分页 total、
  file-server、仪表板计数、gauge、AI 计数、AI 列表各转红；恢复后 **10 passed**；
- **回归**：`test_stats.py` + `test_metrics_fleet_gauges.py` + `test_ai_tools.py` +
  `test_hosts.py` → **75 passed**；
- `ruff` → All checks passed；`check:quick` → **7 gates OK**。

未做：后端「派发/claim/控制面动作」的过滤属 ④；前端缓存穿透与徽标属 ⑥。

## Revisit

- **PromQL 台阶**：退役瞬间 `stability_host_online` 下降会出现在任何按 fleet 规模
  告警的规则里（预期为「规模下降」而非故障）。若运维希望区分「退役」与「掉线」，
  需要 ④ 之后评估新增 `retired` 维度或单独 gauge（属观测面增强，独立裁决）；
- **`include_retired` 的分页语义**：当前 `total` 随过滤变化（与列表一致），前端若
  需要「含退役的总数 + 仅活跃的列表」需另加字段（⑥ 视 UI 需求再定）；
- **AI 助手工具描述**：`_q_hosts` 的 tool description 未提及「不含退役主机」——
  若模型据此判断容量口径产生歧义，可在 ⑥ 或独立小单补描述（工具面文案变更影响
  LLM 行为，宜单独观察）；
- **历史 KPI 的口径注释位置**：口径差异现在写在 `stats.py` 代码里，若后续有专门
  的指标口径文档（如 `docs/design` 下的统计口径页），应把该差异上收为文档条目。
