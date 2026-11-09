# B8 G4：影子轮读取成员关系、发生入库与代表选择

Status: implemented
Class: feature

## Decision

实施范围为 #3877 正文 v2.1，加 #3800 规划者的 v1.19 裁定
[6704981735](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6704981735)
及对拍补充 [6705001572](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6705001572)。
机器数按 [v1.21 / 6705693754](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6705693754)，
v1.20 已作废。issue_occurrence.serial 取对应 DLE.serial，机器数仍按发生的 serial 去重。
非哨兵 DeviceId 与 DLE.serial 不一致时，采用 DLE.serial；专用日志的消息体包含 run、round、
host、DLE、report_device_id、dle_serial，Prometheus 计数器
stability_shadow_merge_device_id_serial_mismatch_total 和轮次摘要计数各增加 1。
哨兵集合及归一函数只在 agent/contracts/scan_device_id.py 定义，Agent 和控制面复用；
issue_fact_store 不依赖 agent/aee/scan_scope.py，也不排除哨兵机器数。

merge 失败按 [6706011658](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6706011658)
改为有界重试。STP_SCAN_ROUND_MERGE_MAX_ATTEMPTS 默认 3，必须 >= 1；每次失败完成后
至少等待 STP_SCAN_ROUND_REPORT_WAIT_SECONDS 才能重试。调度查询和执行入口均检查
持久化的 merge_next_retry_at；merge_attempts 与摘要 attempts 同事务记录，成功后也保留。
达到上限时转为 FAILED_FINAL，消息体记录 run、round、attempts、max_attempts、error_type，
stability_shadow_merge_failed_final_total 只在终态转换时增加一次。终态不再入队，普通结果写入
不能覆盖；人工修复失败原因后，通过 G1 的 reset_failed_final_shadow_merge 显式清零恢复，
调度器和 worker 均不调用该入口。相关字段并入尚未发布的 b8g4a719c203，不增加迁移。

工具格式取 #3877 评论 6704532993 指定的 `start-log-scan/v2.1.1`
（commit `dc5d9c1fe22273dcfc28e41b285e8fdb3bfb6694`）。
开工判定按评论 6704562784 只统计 MTK：1231/.13 为 37/37 组，1199 为 71/71 组，
合计 108/108 组 key 非空且相同。MTK 多余 DLE 占比更正为 4.1%，UNISOC 为 58.9%；
混合平台的 10.9% 不用于 G4 判定。

`scan_membership.py` 集中读取 xls、G3 映射、sidecar，调用发布工具 `-match_issues`。
平台没有签名相似度比较或分支推断。工具输入保留全部未过滤组的原代表及固定顺序。
新问题先在同一工具 new_group 内按代表输入顺序、sidecar member_rows 顺序寻找非空 key
的已映射成员；没有这类成员才取第一个已映射 fail-open 成员。该成员只决定身份锚点，
签名及 branch 仍取该 new_group 第一位代表的 sidecar 输出。
完全不可映射的工具组不建问题、不写发生，行原因归为 unprovable_group，轮次保持 OK。

摘要分开记录未过滤行的 unmapped、全部映射行的 mapping_unmapped、filtered、工具组数、
unprovable_group 组数与 fail-open 锚点数。因此未过滤输入可核对“本轮新增 + 复用发生 +
未映射行”，被过滤的 no_source 也仍有覆盖记账。
发生、全部重复 DLE 关联、轮次结果和选择只经 G1 服务层。已有发生保留原签名，fail-open
按 DLE 复用，已选不改选，失败回滚并重试。候选的单事件证据快照保存到轮次摘要，供最后
一轮选择历史待选发生，且保留 DLE 删除后的跨 run 计数事实。
G4 用事务级 advisory lock 串行化 MTK 写入，再按 PlanRun → scan_round 加锁。

G4 从持久化 CLOSED、merge 未落定的轮次独立发现重试对象；#3875 将 settled 分区移出
扫描候选后，G4 仍能重试，不重新推进该分区的扫描。影子开关关闭时入口不读库、不入队。
只新增 scan_round 五列的一次迁移。Agent 只在影子轮上送 sidecar。MTK 影子轮映射处理同 key
重复 DLE，并写入同目录证据、重复 DLE 列表及 unmapped 原因。证据函数跳过符号链接，
只查一个事件目录内的非空 dbg、mobilelog、已完成 bugreport zip，支持 correlated 布局。

有 sidecar 且 mapping 的 sheet、row 或 path 对接失败时，同一报表同时计入
`mapping_mismatched_reports` 和 `degraded_coverage`，原因是 `mapping_mismatch`。
mapping 行数不符单独计入 `mapping_mismatched_reports`。降级 sidecar 的摘要只保留类型符合
契约的工具、规则和解析器版本。报表降级时轮次可以正常完成为 OK。
[复核裁定 6731915893](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6731915893)

## Alternatives

- 平台复刻 CausedBy、singleton 判断或在缺失 sidecar 时自行比较：违反 ADR-0062，未采用。
- 用 Path、报表行号或代表的空身份造问题键：未采用；按 v1.19 从有身份成员选择锚点。
- 遇到全组无身份便令整轮 FAILED：未采用；独立计 unprovable_group。
- 累加报表 Count/DeviceCount：未采用；G1 按发生身份与 serial 查询。
- FAILED 每个检查周期无限重试：未采用；有界、持久化等待与人工重置限制重复工具调用。

## Verification

测试经内存保护 runner，cgroup 上限 6 GiB、swap=0；控制面测试用 testcontainers。

- main@3e605a1 上先写四种 sidecar 降级、匹配顺序、fail-open 跨轮、已选不改选共 7 项反例，
  实施前因 G4 入口不存在失败，实施后通过。
- v1.19 新增 3 项反例在 f290bc41e 上失败：无身份代表取成员锚点、后续代表的非空 key
  优先于先前 fail-open、全组不可映射仍送工具且轮次 OK。修改后通过；另覆盖 member_rows
  顺序、保留首代表签名、fail-open 成员锚点、filtered 与 unmapped 分开记账。
- “无扫描候选时仍重试 G4”反例修改前 queued=0 而预期为 2，修改后验证持续重试与成功后退出。
- v1.19 受影响集合 91 passed；v1.21 后同一集合 94 passed。
- v1.21 三个反例先在旧代码失败：两台 None 设备误计 1（应 2）；同台 None/真实 serial
  误计 2（应 1）；非哨兵冲突误存报表 serial。修改后均通过，日志消息体和计数器 1 已验证。
- 共享哨兵移动后的 Agent 相关集 57 passed；Agent 边界、测试导入棘轮、函数内导入棘轮
  与载荷根守卫 29 passed；AST 核实哨兵集合只在共享 contracts 中定义一次。
- 本机 Agent 全套历史执行 3086 passed、1 failed；唯一失败是 flash_preflight_v102 的
  test_dialout_absent_and_no_rule_fails，在独立 main@3e605a1 单项复跑同样失败。
  G3/G4 Agent 相关集 51 passed；最终 head 的 pr-agent-tests 以 CI 结果为准。
- 有界重试反例先在 ad797f0 上失败：默认上限 3 时实际调用 8 次，配置上限 2 时调用 6 次；
  第一次失败第二次成功缺少摘要 attempts。修改后 3 项通过，终态日志与计数器各一次。
  另以独立数据库会话验证等待期内不入队、旧队列任务不能提前运行、终态隔天不入队、普通
  结果写不能覆盖终态、人工重置后恢复且次数清零。默认值、env 覆盖和非法配置均验证。
- 本次受影响服务、G1、调度配置和读取契约集合 81 passed；SAQ 任务与 G3/G4 Agent 相关集 86 passed。
- 修改后的同一个迁移在隔离空 Postgres 16 完成 upgrade head → downgrade -1 → upgrade head，
  五列存在/撤销/恢复、merge_attempts 非空默认 0 均核对。若集成 #3896 改父版本则重新验证。
- 早期的上传对照传入预制 mapping，未覆盖映射生成。用复核的固定时钟、XLS 和 DLE，
  经真实 ScanRunner、report_mapping 和 UploadManager 重跑。现行分片的 mapping companion
  恢复为 575 字节，XLS、mapping 和分片记录与 main 的复核基线逐字节相同。
- 在 4af13fb14 复现了三种 mapping 键错配漏记降级，以及 sidecar 主版本 2.0 的数组 meta
  使轮次 FAILED。修复后键错配各记一次 `mapping_mismatch`。数组 meta 只产生
  `sidecar_unusable` 降级，轮次为 OK，无待重试时间。复核回放还验证了 1.99 的扩展字段兼容性。
- 本轮相关套件及复核用例共 171 项通过。补充的现行映射字段断言单项通过，
  `check:quick` 的 16 项门禁通过。
- 本次 check:quick 16 gates passed；环境变量清单与 Ruff 通过。

Run 1193 的 12 份 org.xls 只读副本保留在本机隔离目录。按 #3877 评论 6704816971、
6704830391 的授权，通过 stp_ro 只读导出 72 条 DLE 身份（全部带 key），文件不提交；
source 按每份报表自身的 staging 根构造，不按 isdir 过滤，身份仅取快照。TARGET 4d98d47
与当时 main 的 scan_scope.py 无差异，回放叠加本单重复 DLE 判据。
发布 v2.1.1 的 -dedup_org 12/12 成功，G3/读取层 12/12 通过对接校验；G4 仅写临时 PG16。
原 staging 已回收，回放的 evidence 不用于宣称生产三件齐全；选择行为用独立夹具验证。

v1.19 的两轮真实回放：
- 第 1 轮：工具 11 个问题、33 个未过滤行；平台 11 个问题、32 次发生、1 行 no_source。
- 第 2 轮：工具 14 个问题、110 个未过滤行（128 总行减 18 filtered）；平台 14 个问题，
  新增 38、复用 32、未映射 40，合计 110。全部映射行口径仍保留 56 个 no_source。
- 两轮累计 14 个问题、70 次发生；32 次跨轮重复不增计数。两轮重跑未再次调用工具，结果不变。
- 依 Owner 评论 6705001572，只读分类为首轮 1 个 watcher 副本，第二轮 42 个 watcher 副本
  与 14 个 _temp；都不是别的 run 积压，真正缺 DLE 为 0。分类仅用于对拍，不构造身份。
- v1.21 回放两轮的 device_id_serial_mismatch 都是 0，专用 Prometheus 计数器也是 0。
  本次有界重试改动后重放两轮，均 OK、attempts=1，累计仍为 14 个问题、70 次发生。
  所有非例外问题机器数逐项相等。唯一不相等的问题为 RebootException：每轮工具 0、平台 1，
  对应含未知 DeviceId 哨兵但 DLE 已证明设备的行，符合 v1.21 的 >= 例外。第二轮 Java (JE)
  组也有一个哨兵行，但机器数仍为 23=23，不需要差异例外。

早期 CI 曾发现夹具跨根 tests 的导入不稳定和 xlrd 使用面漏登记，已修复并同步 #739 台账。
根套件 v1.19 全套为 2441 passed、18 skipped；v1.21 改动后相关根守卫 29 passed。
最终提交的测试与 required CI 单独记录，不沿用旧 head 结论。

## Revisit

原两次退回已分别由 6704562784、v1.19 裁定解决，恢复实施。本 PR 保持 Draft，交独立复核，
由 Owner 转 ready；不启用 auto-merge。本单不部署、不激活。
Agent 改动与 #3889 的修复合在同一次 Agent 发布。#3898 的 watcher 双目录问题不阻塞 G4，
阶段二激活前需要规划者结论；阶段一对拍持续披露 no_source 子类。
与 #3896 有相邻模型、上传器、服务及调度重叠；串行集成时后合入者对齐迁移父版本并复跑，
不创建额外 merge revision。G4 待处理轮次独立发现的反例覆盖 settled 后的重试边界。

phase 2 前评估两项规模问题：MTK 全局 advisory lock 让所有 run 的 merge 串行执行，
以及 list_issue_signatures 每轮向工具发送全量历史签名，输入会随历史增长。本次保留影子期
裁定的实现，后续须结合并发量、锁等待和签名规模确定出口。
联合 Agent 发布前必须核实工具脚本同级存在 v2.1.1 的 release.json；规划者评论 6706011658
指出 /mnt/project-files/b8/start-log-scan-release/ 当前缺少该文件，将其列为发布前提。
本机回放使用另一个已具备 release.json 的隔离发布运行时，不替代联合发布的部署核验。
