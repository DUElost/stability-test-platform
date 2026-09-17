# 中心存储 D 步：`_meta/` 写侧登记清单设计定稿（#2188 / zcode）

Status: proposed
Class: architecture

## Decision

1. 本稿把已裁决的 D 步方案（Owner 拍板 marker `dstep-w2-owner-verdict-0916`；评审一稿
   [`REVIEW_CENTER_STORAGE_DSTEP_2026-09-16_5aba0`](../../reviews/REVIEW_CENTER_STORAGE_DSTEP_2026-09-16_5aba0.md)
   的 R-1…R-5）具体化为**可实现的设计**：schema、写侧失败语义、读侧注册、过渡降级与终态出口、purge 桶、契约测试、拆单。
2. 本稿是 D 落地的**设计权威**：实现 PR 不得另立设计或偏离本稿语义；偏离必须先改本稿（评审同款纪律）。
3. 本稿不改代码、不动 R1/R2 现行行为；各落地单各自附 Agent Note + `check:quick`。
4. 基线：`5b79dc46`（2026-09-17 的 `origin/main`，含裁决记录 PR #2449）。行号为该基点亲读。

## 1. 布局与 schema（v1）

- 路径：`{center_root}/_meta/{plan_run_id}/{host_id}.json`。`_meta/` 是**新顶层族**，
  不嵌进 `dedup/`（A 步要拆该族，清单不能跟着迁移——评审未决④）；`_meta` 命名在
  `backend/` 无占用（2026-09-17 grep 复核）。
- 分片文件（每 host 一个，唯一写者 = 该 host 的 Agent）：

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema_version` | int = 1 | 演进锚点（评审未决②） |
| `host_id` / `plan_run_id` | str / int | 自述，供聚合读者校验落点 |
| `updated_at` | UTC ISO8601 | 每次重写刷新 |
| `artifacts[]` | list | **仅 R1 scan 产物账**（载荷收窄，评审 R-1） |

- `artifacts[]` 条目：`file_key`（相对 `dedup/{plan_run_id}/` 的键，含 platform 前缀，
  如 `mtk/{host}_Result_x_org.xls`——**不含 family 名**，A 步拆族只动控制面拼装一处）、
  `platform`（`""` / `mtk` / `unisoc`）、`size_bytes`、`registerable`（见 §2 等价性）。
- 集合语义：按 `file_key` 幂等去重、重扫合并；`updated_at` 只增。

## 2. 写侧（Agent，单 2）

- **挂点 = `UploadManager.upload_scan_report`**（`backend/agent/upload_manager.py:64`）——
  它是 scan 产物上送的**唯一**动作（`scan_runner.py:314-316`、`unisoc_scan_runner` 同用法），
  在此挂钩即覆盖全部调用方，不动 Runner。
- 动作序：copy 成功 → 合并条目 → 原子重写分片（同目录 tmp + `os.replace`）。每次调用重写
  一次分片（每 host 每轮仅数个文件，重写成本可忽略）。
- **失败语义（评审未决①的核心）**：分片写失败必须 **raise**，不得吞成 `return None`——
  现状 `ScanRunner` 不接返回值（`scan_runner.py:314`），吞掉 = 「文件已落、清单未写」的静默半交付，
  正是评审点名「本仓反复吃过的形态」。raise 后异常沿 `scan_now` 控制通道传播 → 控制面该步失败 →
  下轮整段重试；copy 是覆盖写、分片是幂等重写，重试安全。
- **legacy 等价性谓词**：legacy R1 只注册匹配 `*_org.xls` / `*_org_*.xls` 的文件
  （`dedup_scan.py:78`）；第二个产物 `dedup_org` 的文件名由外部工具决定
  （`scan_runner.py:561-563` 取工具 stdout），仓库内不可证其必匹配。故写侧在条目上落
  `registerable: bool`（按现行两 glob 谓词在**写侧**冻结求值），读侧零解析。这保证
  分片注册面 ≡ legacy 注册面；「把不匹配的产物也注册进来」是行为扩大，**不属 D**，另议。
- 并发：每分片唯一写者（§2.3.3 的 lock-free 显式化）；控制面只读。

## 3. 读侧（控制面，单 3）

- `run_scan_sync`（`dedup_scan.py:100`）优先走清单：读 `_meta/{run}/*.json` 聚合，逐条
  `registerable` 条目注册 `plan_run_artifact`；`storage_uri` 由 `file_key` 拼装为
  **与 legacy 完全同形**的字符串（现形态 = `str(文件路径)`，`dedup_scan.py:84-90`），故过渡期
  两路注册共享幂等键 `(plan_run_id, storage_uri)`，互斥重复。
- `host_id` / `platform` 来自分片字段——**不再解析文件名**（`_HOST_PREFIX_RE` 退出主路径，R-4）；
  `scan_round_id` 沿既有参数（注册时归属轮次，写侧不感知轮次，与现行为一致）。
- 新增可选参 `expected_hosts`（`scan_task` 已持 per-host 触发清单，`saq_tasks.py:409-412`），
  供 §4 的 per-host 降级；缺省 None 时行为 = 纯清单注册（向后兼容既有调用点）。

## 4. 过渡降级与终态出口（评审未决②）

- **分片存在性即 per-host 信号**，不建版本注册表、不新增 env 键（ADR-0033 §5.4）、不动
  realtime 控制通道：逐 expected host——分片在 → 清单注册；分片缺 → **仅该 host** 走 legacy
  glob 注册（host 过滤，不再是全目录粗扫）并 `legacy_glob_register_total` 计数 +1（日志字段，
  随单 3 落）。
- 「还没写」vs「这一版不写」：无分片且 glob 无该 host 文件 = 还没写（轮询预算内继续等，现行为）；
  有文件 = 旧 Agent（legacy 注册）。混编 run 天然按 host 分治。
- **终态出口**：计数连续 30 天为 0 → 单 5 删除 legacy 路径 + 谓词 shim（§2），R-4 契约测试
  同步收紧为「无任何中心盘遍历」。出口未达成前 legacy 不删（显式过渡，不留无期限双轨）。

## 5. R2 → DLE（评审 R-1；前提已复核成立）

- `_resolve_center_event_path` 的跨 run glob（`dedup_scan.py:976`）替换为 DLE 查询：
  `remote_path LIKE '%/devices/{event_dir}/%'` 取候选（同口径 `adopt_unassigned` /
  #2262 的字符串判形态），`isdir` 校验后取最新；本 run 直查分支保留在先。
- 前提数据（2026-09-17 只读探针）：盘上 1,144 事件目录、DLE 反例 **0**、行有盘无 2,310
  （先文件后行正常瞬态——行存续期 ⊇ 文件存续期，DB 查询不漏可修复形态）。
- 单 4 载体；`_HOST_PREFIX_RE` 与 glob 自该单起全面退出主路径。

## 6. purge 桶（R-2，单 1，**先于写侧合入**）

- `cron_scheduler.py:289` 的 `("devices", "dedup", "jira")` → 四族（+ `_meta`）；
  先文件后行顺序不变；`backend/tests/scheduler/test_retention_cleanup.py` 补
  `_meta/{plan_run_id}` 用例（含「DB 行删后不可回溯」同构断言）。
- 顺序硬约束：单 2 合入即有 `_meta/` 落盘——purge 未就绪的窗口期会产生永不清的残留（E-2 必挂）。

## 7. R-4 契约测试（结构判据）

1. `run_scan_sync` 在分片齐全的 run 上**零中心盘遍历**（mock 文件系统断言无 glob/readdir 触达 `dedup/`）；
2. 注册主路径无 `_HOST_PREFIX_RE` 引用（host/platform 均来自分片字段）；
3. `_resolve_center_event_path` 无 `devices/*` glob；
4. `upload_scan_report` 分片写失败 → 异常上抛（非 None）。

## 8. 拆单（落地序 = merge 序）

| 单 | 面 | 内容 | 判据/评审项 |
|---|---|---|---|
| 单1 | 控制面 | §6 purge 桶 + 测试 | R-2 / E-2 |
| 单2 | Agent | §2 分片写（含 raise 语义、registerable shim）+ 测试 | 未决①失败语义 |
| 单3 | 控制面 | §3 读侧注册 + §4 per-host 降级 + 计数 + 契约测试 1/2 | 结构判据 / R-4 |
| 单4 | 控制面 | §5 R2→DLE + 删 `devices/*` glob + 契约测试 3 | R-1 / 结构判据 |
| 单5（后续） | 控制面 | §4 终态出口：删 legacy 路径与谓词 shim + 契约测试收紧 | 终态出口 |

每单独立可回滚；单 1/单 2 之间是唯一的顺序耦合（§6）。拆单 issue 待本稿合入后开
（引本稿 §，含各自验收判据），落地期以 #2188 为唯一跟踪单。

## 9. 验收映射（对 Owner 拍板行）

- R-1 → §1/§5；R-2 → §6；R-3/R-5 → A 步（不在 D 范围，阈值仍按 D-4「先采数再定」）；
  R-4 → §7；评审未决①②③④ → §2/§4/§1/§1。
- 「较原方案有较大优化」对 D 的口径 = **结构判据**（§7 全绿 + legacy 计数归零），非字节数（评审 §2.3.4）。

## Alternatives

- **版本注册表检测**（比 `agent_artifact_digest`）——弃：digest→能力映射脆弱、多一维护面；
  分片存在性信号零新增通道、混编 run 天然 per-host。
- **scan_now ack 载荷携带清单字段**——弃：动 realtime 控制通道契约，代价 > 收益；本设计不需要。
- **分片放 `dedup/{run}/_meta/`**——弃：A 步拆族会迫使清单迁移；顶层独立族才满足未决④。
- **读侧无条件注册所有分片条目**——弃：`dedup_org` 产物名不可证匹配 legacy 谓词，会静默扩大
  注册面；用 `registerable` 冻结等价性，行为扩大另议。

## Verification

- 本稿为设计：未改代码；引用行号 2026-09-17 亲读（基线 `5b79dc46`）：
  写侧挂点 `upload_manager.py:64`（copy 语义）、失败现状 `scan_runner.py:314`（返回值被忽略）、
  幂等键 `dedup_scan.py:84-90`、glob 谓词 `:78`、R2 glob `:976`、触发清单 `saq_tasks.py:409-412`、
  purge 桶 `cron_scheduler.py:289`。
- R-1 前提探针（只读）与 `registerable` 等价性推理见 §5/§2；后者在单 2 落地时以
  「分片注册集 ≡ legacy 注册集」的对照测试实证。

## Revisit

- 单 3 遥测显示旧 Agent 长尾不消 → 组织升级冲刺收敛，不延长 legacy 默认窗口（出口判据不动）。
- 增量轮若需分轮归因进清单（`scan_round_hint`），走 `schema_version` = 2，v1 不预埋。
- `_resolve_center_event_path` 的 DLE 查询若在生产出现候选爆炸（同名事件跨 run 数量大），
  加 run 倒序 + LIMIT 截断即可，不改语义。
