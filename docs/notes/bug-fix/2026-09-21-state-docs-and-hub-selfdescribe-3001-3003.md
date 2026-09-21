# 状态机/数据模型文档对码 + hub 自述校准（#3001 + #3003 打包）

Status: implemented
Class: bug-fix

## Decision

两单同型（文档声称与代码事实背离）打一批，全部**以代码为准**改写，逐处带
〔#3001〕/〔#3003〕行内标注（#2661 先例的「标注而不抹史」形态）：

**#3001**：
- `05-data-model.md` job status 补 **UNKNOWN** 行——不是加个词：写明它是**围栏
  恢复态**（持 ACTIVE 租约才可入、转出只 `→RUNNING`/`→FAILED`），把 state_machine
  的注释语义搬进数据模型文档；四处单数表名（user/audit_log/notification_channel/
  alert_rule）改正——讽刺点是**同文档的复数例外表早已承认**这些真名，正文在跟自己
  打架；`revoked_refresh_token` 核对后**确实单数**，不动（证据逐条核过再改，不批量
  改形态）；
- ADR-0003 状态图与勘误段的 **`UNKNOWN → COMPLETED`（Agent 补报）是非法边**：
  VALID_TRANSITIONS 只放行 `UNKNOWN→{RUNNING,FAILED}`，迟到补报必经 recovery 复活。
  原勘误段自己就是错的（它把这条错边当「代码允许」列出）——做「勘误之勘误」并引
  file:line；`device_lock.py` 三处引用标**已移除**（lease_manager:11 记录兼容列
  删除；`test_device_lock.py` 实测不存在），段落降为历史语义注记而非删文——ADR 是
  Decision 史书，**修的是「按它办事」的误导，不是抹掉当年决策**。

**#3003**：
- `CLAUDE.md` 三处描述（README/DOC-MAP×2）统一成「symlink → AGENTS.md 薄壳」
  事实形态（#857 收口的是治理，正文自述是本轮补的账）；ADR 摘要的指针改指
  `docs/adr/README.md`（真正有表的地方）；
- 「最后更新」义务写成 DOC-MAP 常驻约定（改正文必同步头部，**做不到就删字段**——
  陈旧日期比没有更坏），并校准两实例（docs/README.md 落后 9 次提交、
  07-execution-protocol 落后 7 次含语义改写）。

## Alternatives

- **给「最后更新」做 git 派生自动化**（如 footer 由 CI 注入）：弃——文档头部是给人
  读的语义物，自动注入喂不读 git 的镜像树；义务条款+可删字段已够，自动化属过度工程；
- **直接删 07-execution-protocol 的头部**：弃——票面建议是「写义务并顺手补」，且该
  文件仍在活跃演进（删了等于放弃义务，留着才测试义务是否被遵守）。

## Verification

- 纯文档批次：`check:quick` 12 门禁绿（gov-surface/S 系全过）；
- `harness-ingest`（check:gov 专属探针）在**主检出同样 FAIL**——main 既有红、非本批
  引入，#3018 家族正在治它，如实上报不复用为本单债务；
- 全部证据行号逐条对 main 复测过（`revoked_refresh_token` 的例外即由此保住）。

## Revisit

- 文档批第一/二批（codex/zcode）已覆盖 #2988-#2999 与 #2995/#3000/#3002/#3004 的
  对码；三批合流后建议把「文档真值对拍」的**残余面**（§10 hub 类自述、表名↔
  `__tablename__` 全量清单）并入 env_inventory 那类静态清点，别再靠人肉审计发现
  ——是否值得建守卫看复发频率。
