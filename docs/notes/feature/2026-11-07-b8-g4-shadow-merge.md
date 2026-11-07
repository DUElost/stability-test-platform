# B8 G4：影子轮读取成员关系、发生入库与代表选择

Status: implemented
Class: feature

## Decision

实施范围为 #3877 正文 v2.1，加 #3800 规划者的 v1.19 裁定
[6704981735](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6704981735)
及对拍补充 [6705001572](https://github.com/DUElost/stability-test-platform/issues/3800#issuecomment-6705001572)。
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
只新增 scan_round 三列的一次迁移。Agent 只在影子轮上送 sidecar；MTK 映射增加同目录
证据、重复 DLE 列表及 unmapped 原因，保留 #3720 拒绝判定。证据函数跳过符号链接，
只查一个事件目录内的非空 dbg、mobilelog、已完成 bugreport zip，支持 correlated 布局。

## Alternatives

- 平台复刻 CausedBy、singleton 判断或在缺失 sidecar 时自行比较：违反 ADR-0062，未采用。
- 用 Path、报表行号或代表的空身份造问题键：未采用；按 v1.19 从有身份成员选择锚点。
- 遇到全组无身份便令整轮 FAILED：未采用；独立计 unprovable_group。
- 累加报表 Count/DeviceCount：未采用；G1 按发生身份与 serial 查询。

## Verification

测试经内存保护 runner，cgroup 上限 6 GiB、swap=0；控制面测试用 testcontainers。

- main@3e605a1 上先写四种 sidecar 降级、匹配顺序、fail-open 跨轮、已选不改选共 7 项反例，
  实施前因 G4 入口不存在失败，实施后通过。
- v1.19 新增 3 项反例在 f290bc41e 上失败：无身份代表取成员锚点、后续代表的非空 key
  优先于先前 fail-open、全组不可映射仍送工具且轮次 OK。修改后通过；另覆盖 member_rows
  顺序、保留首代表签名、fail-open 成员锚点、filtered 与 unmapped 分开记账。
- “无扫描候选时仍重试 G4”反例修改前 queued=0 而预期为 2，修改后验证持续重试与成功后退出。
- v1.19 受影响集合（G1/G2/G4/SAQ/读取契约）：91 passed。
- 本机 Agent 全套历史执行 3086 passed、1 failed；唯一失败是 flash_preflight_v102 的
  test_dialout_absent_and_no_rule_fails，在独立 main@3e605a1 单项复跑同样失败。
  G3/G4 Agent 相关集 51 passed；最终 head 的 pr-agent-tests 以 CI 结果为准。
- 隔离空 Postgres 16：upgrade head → downgrade -1 → upgrade head 成功，三列
  存在/撤销/恢复均核对。该迁移此后未修改；若集成 #3896 改父版本则重新验证。
- 固定时钟和输入的 UploadManager 离线对照：现行报表、映射、分片 SHA-256 与 main 逐字节相同。
- check:quick 的最终结果写入 PR；v1.19 修改后已通过一次 16 gates。

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
- 机器数存在一个既有输入口径差异：未知 DeviceId 为字符串 None，工具机器数为 0，
  依据原规格取 DeviceId 并由 G1 按 serial 去重则为 1；两轮同一问题均如此。PR 披露该观察，
  未自行修改 G1 机器数定义。

早期 CI 曾发现夹具跨根 tests 的导入不稳定和 xlrd 使用面漏登记，已修复并同步 #739 台账。
根套件历史全套 2439 passed、18 skipped、2 failed；两项失败归位后及相关集复跑 16 passed。
最终提交的测试与 required CI 单独记录，不沿用旧 head 结论。

## Revisit

原两次退回已分别由 6704562784、v1.19 裁定解决，恢复实施。本 PR 保持 Draft，交独立复核，
由 Owner 转 ready；不启用 auto-merge。本单不部署、不激活。
Agent 改动与 #3889 的修复合在同一次 Agent 发布。#3898 的 watcher 双目录问题不阻塞 G4，
阶段二激活前需要规划者结论；阶段一对拍持续披露 no_source 子类。
与 #3896 有相邻模型、上传器、服务及调度重叠；串行集成时后合入者对齐迁移父版本并复跑，
不创建额外 merge revision。G4 待处理轮次独立发现的反例覆盖 settled 后的重试边界。
