# #2470 #2471 devx 可跑性批：假 Agent 补入站 ack、同步 engine 取串归一化

Status: implemented
Class: bug-fix

两条同面缺陷：都发生在「隔离 dev 栈里按文档操作」这条路上，都表现为**工具自陈可用、
实际跑不出结论**，且都没把失败显式化（一个静默卡在派发队列，一个崩栈与判红同为 exit 1）。

## Decision

### #2470 夹具的 `serve` 从不注册入站 handler

`PUSH_EVENTS` 常量自 #2402 就在文件里，但全仓无人消费它：夹具内没有任何事件注册点。
服务端派发门禁 `verify_one_host()` 走 `call_agent_rpc()` 等 ack，夹具不应答即超时，
plan-run 停在队列里一行 `job_instance` 都不产生，而日志里连一条 `verify_scripts` 痕迹
都没有——「Agent 看得见、答不出」。
修法三条：入站事件统一走 `push_ack()`，其中 `verify_scripts` 委派生产侧的只读实现
`verify_scripts_ack()`（夹具因此在派发门禁面前与真 Agent 等价），其余推送事件只回 ok 字典，
红线 1「只回 ack、绝不执行」不变；`register_push_handlers()` 在 agent 命名空间逐个注册，
形态与生产侧 `socketio_client.py` 一致，handler 以默认参数绑定事件名，以免闭包晚绑；
接线位置由 `_with_connection()` 的新开关 `with_push_handlers` 决定，只有常驻的 `serve` 打开
它——短命令 heartbeat / claim / step 发完即走，挂上入站 handler 只会攒一堆无人消费的 ack。
**接线位置本身也是被测对象**：#2470 是在 14 项夹具测试全绿的情况下整体不可用的——那些测试
只断言 `PUSH_EVENTS` 的常量成员，不覆盖注册。新测试因此用假 client 走完 `cmd_serve()` 一轮，
断言每个推送事件都挂上了可调用 handler，并断言短命令路径确实不挂。

### #2470 第 3 项：容器内寻址走 `--in-container`，不给越线开关让路

按 docstring 在容器里跑必然 Connection refused：compose 把 18000 映射在宿主侧，容器内 server
监听 8000。文档用法只能靠 `--allow-non-dev-target` 才跑得通，等于把越红线开关变成日常参数，
守卫失去区分能力——它分辨不出「容器内的 8000 是 dev」与「宿主上的 8000 是生产控制面」，
而后者正是它要防的那条。裁决：

- 新增 `--in-container`，只在地址是 loopback 的 8000 **且**命中真实容器指纹（`/.dockerenv`、
  `/run/.containerenv`、`/proc/1/cgroup` 三者之一）时放行；宿主上给同一地址照旧拒；
- **不新增环境变量**：issue 建议的 `STP_DEV_IN_CONTAINER=1` 未采纳。新 `STP_*` 名要过 #737
  那轮的 env 清单门禁（8 个 `.env*.example` 加奇偶性测试），而这里要的是一次「我在哪」的显式
  声明，不是配置面；CLI 开关同样显式，还额外带指纹校验，比 env 更难长期悬空；
- 两个开关语义不合并：越红线的唯一通道仍是 `--allow-non-dev-target`；`--in-container` 下非
  loopback 的 8000 依然拒。docstring 改成容器内实际可跑形态，并保留宿主侧那条默认写法。

## Decision：#2471 同步 engine 取串未归一化

`backend/scripts/check_seed_identity.py` 把 `resolve_database_url()` 的结果原样交给同步
`create_engine()`，而该函数返回的是**异步驱动**串（dev compose 与控制面 `.env.backend` 均为
`postgresql+asyncpg://`）——首次取连接即炸 `MissingGreenlet`。CI 侧看不出来，因为
`tools/dev/check_pr_migrate.py` 给它注入的是 psycopg 串；人按脚本自己 docstring 里写的取串
顺序在控制机上手工跑，拿到的却是崩栈。最坏的一面是**崩栈与判红同为 exit 1**：读输出的人会
以为「门禁发现了问题」，实际根本没比——这条守卫刚被 #2399 用过，误读代价不低。

1. 取串处改为 `create_engine(normalize_sync_database_url(url), ...)`，与同仓先例
   `check_unreferenced_script_versions.py`（#735 §1.3 同族修复）同一写法、同一条注释口径；
   docstring 里补一句「异步驱动串亦可直跑」，让人按文档操作时得到结论而不是栈。
2. 立静态守卫 `tests/test_sync_database_url_scripts.py`，判据形状与治理面 S5x 同：**登记表
   强制回答**。凡同文件里既取 `resolve_database_url()` 又建同步 engine 的模块，必须二选一：
   用共享写法，或在例外表里留下**可核验证据串**（证据本身被断言，防豁免退化成一句注释）。
   表里的项若从仓库里消失同样报红——覆盖面既不无声扩大、也不长期悬空。

## Alternatives

- **只修 `check_seed_identity.py` 一处**：否决。同族已第三次（#735 §1.3 两处、#2471 一处），
  前两次都靠注释提醒，注释不拦新脚本。守卫必须机械可执行。
- **守卫写成「create_engine 的第一个参数必须是函数调用」的文本规则**：否决。
  `check_schema_sync.py` 先 `re.sub` 再传变量、`backend/alembic/env.py` 在模块级 replace，
  两者都是**正确的自有归一化**，纯形状规则会误红；而允许「传变量」就等于放回本缺陷的形态。
  登记表把这类文件显式列出并校验其证据，才既不误伤也不放过新脚本。
- **让 `resolve_database_url()` 直接返回同步串**：否决。它是 async engine 的同一个取串入口，
  改返回值会波及 `backend/core/database.py` 的引擎构造与部署链。
- **容器内改用 env 开关 / 直接放行 8000**：见上节，均未采纳。

## Verification

- `python -m pytest tests/test_dev_fake_agent.py -q` → 22 passed（原 14 项 + 入站接线 3 项
  + 容器寻址 3 项）；`python -m pytest tests/test_sync_database_url_scripts.py -q` → 3 passed。
- 变异自证（每条只改一处，跑完从备份还原，末次全量复跑绿）：
  1. 去掉 `_with_connection()` 里的注册调用 → 仅 `serve_wires...` 一条红（证明测试盯的是接线）；
  2. `push_ack()` 不再委派哈希器、一律回 ok → 仅 `inbound_handlers_ack` 一条红；
  3. 让 `in_container` 分支永不生效 → 容器寻址用例红；
  4. 摘掉容器指纹校验 → 指纹用例红（`--in-container` 退化成随手开关时会被抓到）；
  5. handler 改成闭包晚绑（不用默认参数）→ 入站用例红（`verify_scripts` 拿到最后事件的应答）；
  6. 把 `check_seed_identity.py` 退回原始串 → 2 条红（通用规则 + 本单锚点用例）；
  7. 抹掉豁免文件 `check_schema_sync.py` 的自有归一化证据 → 证据用例红，且该文件 diff 已还原。
- `python -m ruff check` 覆盖四个改动文件（`tools/dev/fake_agent.py`、
  `backend/scripts/check_seed_identity.py`、两份 `tests/`）→ All checks passed。
- **行为对照（#2471 本尊，离线，不触任何真实库）**：同一个 `postgresql+asyncpg://…` 串交给
  同步 engine 去 connect → `MissingGreenlet: greenlet_spawn has not been called`（与 issue
  记录的崩栈同一条）；过 `normalize_sync_database_url()` 后 → `OperationalError: connection
  failed`（端口 59999 无监听，即驱动不匹配已消失，剩下的是真实连接错误）。psycopg 串走新代码
  路径不变：`check:pr` 的 `pr-migrate` 里 `check-seed-identity` 照常 PASS（47 行身份一致）。
- `python scripts/run_gates.py check:pr` → 19 gates 全绿；`tools/dev/check_governance_surface.py
  --check` → 阻塞项全绿（S1–S14、S5x）。前端三项需 `frontend/node_modules`，本地以软链提供。

## Revisit

- `PUSH_EVENTS` 里的 `execute_job` / `run_job` / `dispatch` / `job_command` 今天服务端并不推
  （生产 Agent 只注册 `control` 与 `verify_scripts`）。夹具保留「全部只回 ok」是有意的：这些
  名字一旦真被服务端推起来，夹具会照旧「应答但不执行」。若确认长期不推，应连同常量一起删，
  而不是让登记表继续背着它们。
- 派发链路的**端到端**验证仍需一个真 dev 栈（本单只到「handler 已注册、ack 形状正确」这一层，
  不起服务）。#2402 的验收目标——job/step 级实时面在隔离环境可回归——要等下一次在真 compose
  里跑 `serve` 才算闭环，届时若仍卡 precheck，先看这里三条用例是否还绿。
- 守卫的例外表是**手工登记表**：新增取串点必须先回答，否则红。若将来 `resolve_database_url()`
  改成返回成对的 (async, sync) 串，本守卫与两处 `normalize_sync_database_url` 调用一起退役。
