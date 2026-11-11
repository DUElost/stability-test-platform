# B8 G5：交付物版本登记与交付状态判定（影子阶段）

Status: implemented
Class: feature

## Decision

实施范围为 #3929 正文，以及 ADR-0062 v1.1 D9 第 1–5、8、10 条与 #3800 v1.25 §3。
分支从 origin/main 80c9be3c6 起；issue 前检基线 ad430a9 之后只有 Dependabot 合入，
G5 相关文件未变化。正文「判据前检」逐项核对一致：delivery_version 已由 G1 落表且
无交付状态列；representative_selection.evidence_status 只是 COMPLETE/INCOMPLETE；
影子 merge 在 backend/services/shadow_merge.py；scan_round.merge_status 为
OK / NO_INPUT / FAILED / FAILED_FINAL。本单元不新增迁移。

只登记 MTK 影子分区。登记触发有两条路径，二者共用同一实现：
- 主路径：影子 merge 任务在 merge_round 返回终态（OK / NO_INPUT / FAILED_FINAL）后，
  且轮次为 FINAL 或 SUPPLEMENT 时，直接调用 try_register_delivery_version。
- 补偿路径：sweep_shadow_scan_rounds 每个检查周期查询「已有终态 FINAL/SUPPLEMENT、
  且 merged_at 不早于最近交付物版本 registered_at」的分区。首次登记缺失、登记失败
  或之后又有轮次终态时才会再次进入；登记成功后不会每个 tick 重复写库。
  try_register 的失败只记 shadow_delivery_register_failed 并返回 None，不向
  merge 任务或 sweep 抛出；下一次检查周期由补偿查询重试。

登记内容按 D9 第 2 条：round_ids 为 FINAL 加「已关闭且 merge 已终态」的补充轮；
effective_coverage 对 FINAL 期望集中的每个 (host, platform)，检查依据轮次中是否
有 RECEIVED 或 EMPTY，并记录覆盖它的轮次 id；不改写各轮 expected_set。issue_ids
取本分区 representative_selection 的问题；event_ids 取这些问题在本分区 DLE 上的
全部发生关联；software_versions 取发生 build_version 去重，为空时记 ["unknown"]。
pending_items、primary_folders 在影子阶段为空数组；gaps 为缺失覆盖与
evidence_status=INCOMPLETE 的代表。统计导出包含每个问题的签名、次数（按发生去重）、
机器数（按 serial 去重）、代表 DLE、证据完整度、软件版本，以及版本顶层的范围字段。

导出路径经共享 _meta 路径契约的纯函数 delivery_export_path 生成：
_meta/{plan_run_id}/_delivery/{platform}/v{version_no}.json。写入顺序固定为
「先写导出文件（同目录 tmp + fsync + rename + 父目录 fsync），再插入 delivery_version
行」：库失败或事务未提交只会留下可被下次覆盖的孤儿文件，不会出现有登记无文件；
stats_snapshot_version 直接取写入字节的 sha256，rename 保证文件内容与哈希同源。
PlanRun 行锁串行化同一分区的登记，版本号按最新版本 +1 分配；内容比较排除
registered_at，相等时不写库也不写文件，旧版本与其导出文件保持不变。

交付状态由纯函数 evaluate_delivery_status 按 D9 第 5 条顺序判定：最后一轮未关闭、
最近一次 merge 失败或无输入、有效覆盖为空、代表重试中分别返回未就绪；发布状态
映射为 None（影子阶段不适用）时在第 1、2 步之后返回 SCOPE_ONLY，理由
publish_status_not_applicable，不返回就绪。发布状态矩阵完整覆盖 VERIFIED / RETRYING /
FAILED：RETRYING 只计未就绪且不计缺口，只有 FAILED 计缺口；证据不全与覆盖缺失
进入 READY_WITH_GAPS 的 gaps。evaluate_version_status 是读取已登记版本
effective_coverage 的薄适配入口，供 G6/G7 使用。交付状态不持久化。

只改控制面服务层、调度接线、测试与本文；共享路径契约只增加一个纯路径函数，
不改变 Agent 行为。不写 plan_run_artifact，不改 DLE 状态，不触碰 dedup/{run}，
不触发 extract / upload / JiraRun。

## Alternatives

- 只在 sweep 登记：实现更少，但 merge 终态到下一次检查周期之间没有版本，阶段一
  对拍会看到缺口；仍保留 sweep 作为失败与崩溃补偿。
- 新增 SAQ 任务入队登记：需要新的任务类型与重试语义；现有 merge 任务内直接调用
  加 sweep 补偿已覆盖失败重试，未采用。
- 先插库再写文件：进程在提交后、写文件前崩溃会留下有登记无文件，直接违反验收 8；
  未采用。
- 为交付状态增加持久化列：正文明确不持久化，且需要迁移；未采用。
- 把未终态 merge 的补充轮也纳入 round_ids：会让版本包含尚未产出 merge 结果的轮次，
  覆盖与统计成为半成品；本单元只纳入已终态补充轮，待其终态后由补偿路径登记新版本。
- 在调用点拼接 _meta 路径：违反正文路径契约要求；改为在现有 round_report_layout
  中增加纯函数。
- 用时间戳或随机数做版本号：唯一键与「旧版本保留」要求连续版本号；未采用。

## Verification

测试经项目内存保护 runner（cgroup memory.max=6 GiB、swap=0），控制面用
testcontainers PostgreSQL 16。

- 反例先行：在 origin/main（80c9be3c6）上先提交 20 项 G5 用例，全部因
  backend.services.shadow_delivery 不存在失败（20 failed）；实现后
  backend/tests/services/test_shadow_delivery.py 20 passed。
- 验收 1–10 对应覆盖：FINAL OK + RECEIVED/EMPTY 登记 v1 且 scope_only；重复触发
  不新增版本；补充轮补 host 后 v2 且 v1 不变；全 MISSING 返回未就绪（覆盖为空）；
  NO_INPUT / FAILED_FINAL / FAILED 返回未就绪；INCOMPLETE 进入 gaps；发布状态
  VERIFIED/RETRYING/FAILED 矩阵；导出文件存在且 sha256 等于 stats_snapshot_version，
  写文件失败无登记、写库失败只有孤儿文件且重试后哈希一致；开关关闭、PERIODIC、
  非 MTK 均不登记；plan_run_artifact、DLE 状态、dedup/{run} 文件前后不变。
  触发器另覆盖：merge 终态触发真实登记、登记抛错不影响 merge 且留专用日志、
  sweep 补偿只登记一次且连续两个周期不重复写。
- 变异自证三项（每次改后清 __pycache__，恢复后再清）：
  1) 去掉「内容不变不登记」，test_repeat_registration_is_idempotent 变红
     （v1 后又登记 v2）；恢复后绿。
  2) 去掉「重试中→未就绪」并把 RETRYING 计入缺口，发布状态矩阵的 RETRYING 项
     从 NOT_READY 变红为 READY_WITH_GAPS；恢复后绿。
  3) 有效覆盖只取 FINAL 轮，补充轮测试断言 [True, True] 变红为 [True, False]；
     恢复后绿。
- 相关回归：test_shadow_delivery / test_shadow_merge / test_shadow_scan_rounds /
  test_issue_fact_store / test_shadow_scan_send_3875 / test_shadow_scan_settled_3875
  共 105 passed。
- 静态门禁：scripts/run_gates.py check:quick 16 gates 全绿（含 Ruff、环境变量清单、
  控制面 import 边界、函数内 import 棘轮、tool-manifest、transitions）。
- 未运行：完整 backend/tests 与 CI required checks；以 PR 的 required checks 为准。

## Revisit

- G6 接入 S8 回执后，把发布状态映射传入 evaluate_delivery_status，并把 FAILED 代表
  写入新版本 gaps；pending_items 填入重试中的代表。G6 还要补主文件夹命名与
  ARCHIVED 转换（D9 第 6–7、9 条），本单元没有触碰。
- G7 的 API、下载与前端展示需要读取版本与导出文件；届时确认对外理由字段的展示
  形式是否直接使用 reason 代码。
- 补偿查询会长期保留已终态分区；当前每次只是比较内容，不写库。若阶段一规模扩大，
  评估是否需要在 G7 增加「最近一次尝试时间」类持久字段，避免长期读取。
- 本单元把「依据轮次」限定为 FINAL 加已终态补充轮。若 G6/G7 期间出现多个补充轮
  并行待 merge 的现场，需要复核 D9 第 2 条「已关闭的补充轮」是否要求包含未终态轮次；
  当前选择是不登记半成品。
- 导出文件与登记行跨文件系统、跨 NFS 的持久性依赖 tmp+rename 与目录 fsync；部署时
  沿用现有 _meta 日志根，不新增存储族。
- PR 保持 Draft，交独立复核，由 Owner 转 ready；不启用 auto-merge。本单不部署、不激活。
