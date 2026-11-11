# B8 G5：交付物版本登记与交付状态判定（影子阶段）

Status: implemented
Class: feature

## Decision

实施范围为 #3929 正文，以及 ADR-0062 v1.1 D9 第 1–5、8、10 条与 #3800 v1.25 §3。
分支从 origin/main 80c9be3c6 起；issue 前检基线 ad430a9 之后只有 Dependabot 合入，
G5 相关文件未变化。正文「判据前检」逐项核对一致：delivery_version 已由 G1 落表且
无交付状态列；representative_selection.evidence_status 只是 COMPLETE/INCOMPLETE；
影子 merge 在 backend/services/shadow_merge.py；scan_round.merge_status 为
OK / NO_INPUT / FAILED / FAILED_FINAL。本单元新增一次加列迁移
b8g5c0a1b2c3：scan_round.merge_generation 与 delivery_version.basis_rounds，
只加列、不回填，可回退。

只登记 MTK 的影子分区。分区模式必须是 CURRENT（没有模式记录的存量分区按现行
解释）；DEDUP_FIRST 分区不登记。影子开关关闭时入口不读库。登记触发有两条路径，
二者共用同一实现：
- 主路径：影子 merge 任务在 merge_round 返回终态（OK / NO_INPUT / FAILED_FINAL）后，
  且轮次为 FINAL 或 SUPPLEMENT 时，直接调用 try_register_delivery_version。
- 补偿路径：sweep_shadow_scan_rounds 每个检查周期查询「存在依据轮次（已关闭、
  merge 已终态、FINAL 或补充轮），其 (round_id, merge_generation) 不在最新版本
  basis_rounds 里」的 CURRENT 分区；没有版本的分区也入选。同时排除非 CURRENT、
  FINAL 未关闭/merge 未终态、PlanRun 已不存在的分区。代次单调、与墙钟无关：
  新轮次或同一轮重新进入终态都会改变 (id, 代次)，登记后清单收敛。
  try_register 的失败只记 shadow_delivery_register_failed 并返回 None，不向
  merge 任务或 sweep 抛出；下一次检查周期由补偿查询重试。补偿发现本身单独使用
  一个会话，并以会话级 lock_timeout=2000ms 执行（auto_archive_poll_interval_seconds
  默认 120s，2s 最多迟滞当轮现行归档 2s）；发现失败只记
  shadow_delivery_discovery_failed、跳过当轮补偿，不中断当轮影子 scan/merge 入队，
  下一周期重新发现。

登记内容按 D9 第 2 条：round_ids 为 FINAL 加「已关闭且 merge 已终态」的补充轮；
basis_rounds 记录这些轮的 (round_id, merge_generation)，按 round_id 升序，并纳入
内容签名；round_ids 保留不变。effective_coverage 对 FINAL 期望集中的每个 (host, platform)，检查依据轮次中是否
有 RECEIVED 或 EMPTY，并记录覆盖它的轮次 id；不改写各轮 expected_set。issue_ids
取本分区 representative_selection 的问题；event_ids 取这些问题在本分区 DLE 上的
全部发生关联；software_versions 取发生 build_version 去重，为空时记 ["unknown"]。
pending_items、primary_folders 在影子阶段为空数组；gaps 为缺失覆盖与
evidence_status=INCOMPLETE 的代表。统计导出包含每个问题的签名、次数（按发生去重）、
机器数（按 serial 去重）、代表 DLE、证据完整度、软件版本，以及版本顶层的范围字段。

导出路径由控制面模块 shadow_delivery.delivery_export_path 生成，复用共享
round_report_layout 的 _round_id / _component 校验：
_meta/{plan_run_id}/_delivery/{platform}/v{version_no}.json。backend/agent/
零 diff，Agent 代码载荷 digest 与 main 相同。写入顺序固定为
「先写导出文件（同目录 tmp + fsync + rename + 父目录 fsync），再插入 delivery_version
行」：库失败或事务未提交只会留下可被下次覆盖的孤儿文件，不会出现有登记无文件；
stats_snapshot_version 直接取写入字节的 sha256，rename 保证文件内容与哈希同源。
PlanRun 行锁串行化同一分区的登记，版本号按最新版本 +1 分配；内容比较排除
registered_at，相等时不写库也不写文件。basis_rounds 的代次已纳入签名，重新
merge 后签名必定不同，因此即使内容相同也会写新版本；旧版本与其导出文件保持不变。
registered_at 为登记实时时间，仅作记录，不参与任何判断。

迁移 b8g5c0a1b2c3 只加两列、不回填：scan_round.merge_generation（int，非空，
server_default 0）与 delivery_version.basis_rounds（JSONB，非空，
server_default '[]'）。存量轮次代次为 0，第一次登记按 0 记录；G5 尚未部署，没有
存量版本行。G5 发布因此是控制面部署 + 迁移，按 control-plane-deploy SOP 先备份 DB。

交付状态由纯函数 evaluate_delivery_status 按 D9 第 5 条顺序判定：最后一轮未关闭、
最近一次 merge 失败或无输入、有效覆盖为空、代表重试中分别返回未就绪；发布状态
映射为 None（影子阶段不适用）时在第 1、2 步之后返回 SCOPE_ONLY，理由
publish_status_not_applicable，不返回就绪。发布状态矩阵完整覆盖 VERIFIED / RETRYING /
FAILED：RETRYING 只计未就绪且不计缺口，只有 FAILED 计缺口；证据不全与覆盖缺失
进入 READY_WITH_GAPS 的 gaps。evaluate_version_status 是读取已登记版本
effective_coverage 的薄适配入口，供 G6/G7 使用。交付状态不持久化。

只改控制面服务层、调度接线、测试与本文；backend/agent/ 零 diff，不改变 Agent
行为或载荷。不写 plan_run_artifact，不改 DLE 状态，不触碰 dedup/{run}，
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
- 在调用点拼接 _meta 路径：违反正文路径契约要求；改为控制面模块中的纯函数，
  复用契约层现有校验函数，同时保持 backend/agent/ 零 diff。
- 把 delivery_export_path 加在 backend/agent/contracts：会把 Agent 代码载荷
  digest 从 main 的 61256050… 改为 61f1a02b…，导致 77 台机队 drift；未采用。
- 用 merged_at 与 registered_at 比较判断重新 merge：依赖墙钟单调，回拨时会漏登记；
  改为单调 merge_generation + basis_rounds，未采用时间判据。
- 保留 forced 分支或 registered_at 取 max(merged_at)：代次入签名后可删，未采用。
- 用时间戳或随机数做版本号：唯一键与「旧版本保留」要求连续版本号；未采用。

## Verification

测试经项目内存保护 runner（cgroup memory.max=6 GiB、swap=0），控制面用
testcontainers PostgreSQL 16。

- 反例先行：实现前在 origin/main（80c9be3c6）上先写 20 项验收用例，全部因
  backend.services.shadow_delivery 不存在失败（20 failed）；实现后通过。最终 30 项
  在 origin/main 的独立 worktree 上复跑仍全部报错（30 errors，模块不存在），在实现
  分支 backend/tests/services/test_shadow_delivery.py 为 30 passed；本轮覆盖墙钟
  回拨两种、代次单调、重新 merge 竞态与 PlanRun 删除。
- 验收 1–10 对应覆盖：FINAL OK + RECEIVED/EMPTY 登记 v1 且 scope_only；重复触发
  不新增版本；补充轮补 host 后 v2 且 v1 不变；全 MISSING 返回未就绪（覆盖为空）；
  NO_INPUT / FAILED_FINAL / FAILED 返回未就绪；INCOMPLETE 进入 gaps；发布状态
  VERIFIED/RETRYING/FAILED 矩阵；导出文件存在且 sha256 等于 stats_snapshot_version，
  写文件失败无登记、写库失败只有孤儿文件且重试后哈希一致；开关关闭、PERIODIC、
  非 MTK、partition_mode=DEDUP_FIRST 均不登记；plan_run_artifact、DLE 状态、
  dedup/{run} 文件前后不变。
  触发器另覆盖：merge 终态触发真实登记、登记抛错不影响 merge 且留专用日志、
  sweep 补偿只登记一次且连续两个周期不重复写、NO_INPUT/FAILED_FINAL 仍登记但判定未就绪、
  首周期登记失败后下一周期由补偿查询重试成功。
- 变异自证三项（每次改后清 __pycache__，恢复后再清）：
  1) 去掉「内容不变不登记」，test_repeat_registration_is_idempotent 变红
     （v1 后又登记 v2）；恢复后绿。
  2) 去掉「重试中→未就绪」并把 RETRYING 计入缺口，发布状态矩阵的 RETRYING 项
     从 NOT_READY 变红为 READY_WITH_GAPS；恢复后绿。
  3) 有效覆盖只取 FINAL 轮，补充轮测试断言 [True, True] 变红为 [True, False]；
     恢复后绿。
- 墙钟回拨 12a（内容变化 + 首次登记失败）：merge 路径登记注入失败，补偿清单
  发现该 run，下一周期真实 sweep 写 v2。
- 墙钟回拨 12b（内容不变 + 登记无故障）：第 3 轮复核指出旧用例直接调用 _register，
  未覆盖补偿路径。用例已改为先断言补偿清单包含该 run，再调用真实
  sweep_shadow_scan_rounds 写 v2；v2 的 round_ids、覆盖、问题、事件、软件版本与
  gaps 与 v1 相同，仅 basis_rounds 代次 1→2 与登记时间/哈希变化；之后不再入选，
  后续 sweep 不产生 v3。
- 代次单调：FAILED→OK 加 1；已是终态重复写入不变；FAILED_FINAL 后 reset 不减，
  重新 OK 再加 1。
- 本轮定向变异（每次改后清 __pycache__，恢复后再清）：
  1) 补偿条件去掉 merge_generation（只比 round_id）：墙钟回拨且首次登记失败的
     测试变红；
  2) 两份内容签名都去掉 basis_rounds：墙钟回拨且内容不变的测试只返回 v1，变红；
  3) record_scan_round_merge 不加代次：代次单调测试变红；
  4) 迁移 downgrade 不删列：迁移往返测试变红。
  恢复后四条均绿；DEDUP_FIRST、未纳入轮次、PlanRun 删除、merge 晚于 sweep 起点
  测试继续通过。
- 迁移往返：隔离 PostgreSQL 16 上 upgrade head → downgrade b83875a11c02 →
  upgrade head 通过；tools/dev/check_pr_migrate.py（空库迁移 + schema 比对 + seed
  身份）通过。
- Agent 载荷等值：git diff origin/main...HEAD -- backend/agent/ 为空；
  collect_artifact_entries('backend/agent') + digest_entries 在 origin/main
  80c9be3c6 与 HEAD 均为
  sha256:61256050146487c5571cacd85ae7ac7bb235f0694207773d7766ca11dc1e50dd。
  修复前（路径函数在契约模块）为
  sha256:61f1a02bf53e6062dd7ce03fafc1fd3e65f08b09353dd10a1d92efa57ba5e36e。
- 第 3 轮返修新增 B1 回归 test_delivery_discovery_lock_timeout_keeps_pending_merge_enqueued：
  对 delivery_version 持 ACCESS EXCLUSIVE，并把补偿发现会话的 lock_timeout 常量
  调为 100ms；断言当轮待处理影子 merge 仍入队、未写 v2，锁释放后的下一周期补偿
  写 v2 且清单收敛。变异「去掉补偿发现的异常边界」时该用例红（LockNotAvailable
  冒出），恢复后绿。
- 第 3 轮 B2 变异：sweep 不消费补偿清单（从候选并集去掉 delivery_run_ids）时，
  重写后的 12b 用例红（只写 v1）；恢复后绿。
- 相关回归：test_shadow_delivery / test_shadow_merge / test_shadow_scan_rounds /
  test_issue_fact_store / test_shadow_scan_send_3875 / test_shadow_scan_settled_3875 /
  test_delivery_generation_migration 共 117 passed（含迁移往返与新增 B1 用例）。
- 静态门禁：scripts/run_gates.py check:quick 16 gates 全绿（含 Ruff、环境变量清单、
  控制面 import 边界、函数内 import 棘轮、tool-manifest、transitions）。
- 未运行：完整 backend/tests 与 CI required checks；以 PR 的 required checks 为准。

## Revisit

- G6 接入 S8 回执后，把发布状态映射传入 evaluate_delivery_status，并把 FAILED 代表
  写入新版本 gaps；pending_items 填入重试中的代表。G6 还要补主文件夹命名与
  ARCHIVED 转换（D9 第 6–7、9 条），本单元没有触碰。
- G7 的 API、下载与前端展示需要读取版本与导出文件；届时确认对外理由字段的展示
  形式是否直接使用 reason 代码。
- 补偿查询会长期保留已终态分区；当前每次只比较 basis_rounds，不写库。若阶段一
  规模扩大，评估是否需要在 G7 增加「最近一次尝试时间」类持久字段，避免长期读取。
- 补偿发现的 2000ms 锁等待常量按 auto_archive_poll_interval_seconds 默认 120s
  取值；若后续调整该调度周期或 sweep 改为异步，重新评估该取值。
- 本单元把「依据轮次」限定为 FINAL 加已终态补充轮。若 G6/G7 期间出现多个补充轮
  并行待 merge 的现场，需要复核 D9 第 2 条「已关闭的补充轮」是否要求包含未终态轮次；
  当前选择是不登记半成品。
- 导出文件与登记行跨文件系统、跨 NFS 的持久性依赖 tmp+rename 与目录 fsync；部署时
  沿用现有 _meta 日志根，不新增存储族。
- 第 2 项（run 级回收删除 _meta 导出文件但保留登记行）按 #3800 6786461443 登记到
  ADR-0062 D11 留存回收单元，本 PR 不处理；启用 retention 前必须先完成该项。
- G5 发布需按 control-plane-deploy SOP 先备份 DB 再执行迁移 b8g5c0a1b2c3；本单
  不部署、不激活。PR 保持 Draft，交独立复核，由 Owner 转 ready；不启用 auto-merge。
