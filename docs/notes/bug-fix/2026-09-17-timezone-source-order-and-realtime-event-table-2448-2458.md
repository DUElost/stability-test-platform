# 时区来源顺序两端同源 + 实时接线守卫补「前端事件表 → 生产者」判据（#2448 / #2458）

Status: implemented
Class: bug-fix

## Decision

两单同属「同一形态漂移的两端各写各的」这一面：一侧是**读时区的来源顺序**，一侧是
**实时通道事件表与生产者**。共同修法都是「把已有的事实变成可执行的对拍判据」，
而不是各改一处了事。

### #2448：时区来源顺序以控制面 `Ops.timezone()` 为参考实现

「控制面 → Agent 时区对齐」链的两个来源（`/etc/timezone` 与 `timedatectl`）此前
**两端优先级相反**：控制面 `tools/site_config/ops.py::LocalOps.timezone()` 是
`/etc/timezone` → `timedatectl`，而 `set_timezone.yml` 的 `tz_controller` 回退链
是 `timedatectl` → `/etc/timezone`（且该处注释写的又是相反顺序，自己与自己矛盾）。
两个来源本身不一致时（例如只手工改了 `/etc/localtime`），Agent 会被对齐到与控制面
判定**不同**的时区，而两端各自的检查都能通过——症状延迟显现（正是 #2265 要消除的
那类错位）。

修法：**以控制面一侧为参考实现**（不改语义，只把偏差方对齐），并把顺序写成可执行
契约——`tests/test_ansible_timezone_2265.py` 新增两侧对拍：

- `test_controller_source_order_matches_control_plane`：断 playbook 表达式里
  `cat /etc/timezone` 先于 `timedatectl`，并断注释与代码同向；
- `test_control_plane_timezone_source_order_is_the_reference`：断参考实现的代码
  顺序不变（**AST 剥掉 docstring 再比对**——否则注释里的同向措辞会让「有人把
  参考实现反过来」的改动假绿）。

选 `/etc/timezone` 优先的理由（写进 `Ops.timezone()` docstring 与 playbook 注释）：
它是 Debian/Ubuntu 的规范位，站点报告的 provenance 已按这条记录；无 system bus 的
目标（容器/精简镜像）只有它会被更新。反过来以 `timedatectl` 优先时，这类目标读不到
便间接落到 `/etc/timezone`，两端行为不可预测。

### #2458：接线守卫补第 4 条判据，并收紧第 1 条的「有人在用」

#2400 的三条判据都在「服务端 emit / 前端订阅」这一侧，**没有一条以
`SOCKET_MESSAGE_TYPES`（前端 `switch (msg.type)` 的判据全集）为全集回头看生产者**，
于是「前端有消费、后端零生产者」的漂移无门禁——现存实例 `DEPLOY_UPDATE`：后端
`grep deploy_update` 为空，且它失效的 `['deployments']` 键在前端也没有任何查询注册
（`invalidateQueries` 本就是 no-op）。本单：

1. **判据 4**：以 `SOCKET_MESSAGE_TYPES` 值域为全集，按归一化名（`DEVICE_UPDATE`
   ↔ `device_update`）要求每个类型在生产面有 `sio.emit("…")` / `schedule_emit("…")`
   对应物，或落在 `_ALLOWED_WITHOUT_PRODUCER`（带理由的豁免表，当前为空）；豁免表
   本身也判过期（类型已有生产者或已不存在 → 报红）。
2. **判据 1 收紧**：`_count_in(text.count)` → **AST 标识符**判定。文本计数会把注释与
   文档字符串里提到的 `broadcast_*` 名字算成「有生产调用方」，据此判绿会掩盖真实
   死角。
3. **`DEPLOY_UPDATE` 终局 = 两端同删**（前端常量 + `useRealtimeDashboard` 的 case），
   沿用 #2400 对 `RUN_UPDATE` / `REPORT_READY` 的同一处置——它没有生产者，也没有
   失效目标，留着只会让事件目录看起来是通的。

## Alternatives

- **#2448：反过来把控制面改成 `timedatectl` 优先**（「对齐 playbook」）：否决。站点
  报告 provenance 已按 `/etc/timezone → timedatectl` 记录，`preflight` / `stages` /
  文档都按它写；且这会让无 system bus 的下游目标行为不可预测。偏差方是 playbook，
  改它成本最小、影响面最窄。
- **#2448：在 ansible 侧引用「同一处常量」**：不可行——playbook 无法 import
  Python。故以两侧对拍守卫充当唯一定义的**可执行契约**，并在两侧注释互相指认
  参考实现（这已是本仓库对 ansible/控制面共享口径的既有手法，见
  `test_ansible_config_channel_2218.py`）。
- **#2458：判据 4 用 emit 载荷里的 `"type": "X"` 作探针**：否决。载荷 type 只是
  「构造了这个字典」，而 emit 名才是「真的发出去了」的直接证据；且 emit 名判据对
  `schedule_emit` / `sio.emit` 两种调用形态都能覆盖。
- **#2458：给 `DEPLOY_UPDATE` 登记豁免保留分支**：否决。它既无生产者、失效的
  query key 也不存在（双死代码）；豁免表留给「确实有意保留但暂无生产者」的类型，
  不用于给历史残留兜底。
- **#2458：顺带把判据 2（订阅描述符）也改成注释无关的解析**：本单不做（见 Revisit）。

## Verification

反例构造（先证伪再采信，逐条恢复后复绿）：

| 反例 | 期望 |
|---|---|
| playbook 来源顺序改回 `timedatectl` 优先 | `test_controller_source_order_matches_control_plane` **FAILED** |
| `ops.py` 参考实现顺序反转（`timedatectl` 优先） | `test_control_plane_timezone_source_order_is_the_reference` **FAILED** |
| 把 `DEPLOY_UPDATE` 加回前端事件表 | `test_every_message_type_has_a_server_producer` **FAILED** |
| 把 `broadcast_watcher_signal` 唯一调用点改成注释（仅剩 import 与注释提及） | `test_every_broadcast_has_production_caller` **FAILED**；同一状态下旧文本计数命中 **3** 次（= 假绿） |

实测命令与结果：

- `TESTING=1 python -m pytest tests/test_realtime_wiring_contract.py
  tests/test_ansible_timezone_2265.py -q` → **17 passed**（含 4 条新/改动判据）；
- `npx vitest run src/hooks/parseSubscription.test.ts src/pages/Dashboard.test.tsx`
  （前端受事件表改动影响的两处）→ **13 passed**；
- `python -m ruff check`（改动文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**；
- 根目录 `tests/` 全量 → **1349 passed, 1 failed**：失败项
  `test_script_seed_governance.py::test_new_seed_migrations_deactivating_versions_check_references`
  （`e5f6a7b8c9d0_repair_flash_firmware_seed_identity_2399.py` 反激活版本无引用检查）
  **与本单无关**——已在干净 `origin/main` 的一次性 worktree 上复现同一失败，属主线
  既有红灯（另有 `#2471` 记同工具的相邻缺陷）。

## Revisit

- **判据 2 仍是文本计数**（订阅描述符名出现在前端注释里同样会假绿）。本轮未动它：
  该判据的 needle 是 TS 文件，注释剥离需要比 Python AST 更重的机制（`//` 会出现在
  字符串里）；若将来出现同类假绿现场，按 `strip-comments` 的轻量 TS 词法处理。
- **agent → server 方向的「有 emit 无 handler」仍无门禁**：`step_update` 是现存实例
  （agent 侧仍 `_emit`、服务端已无 `on_step_update`），本判据 4 只覆盖
  `SOCKET_MESSAGE_TYPES` 一侧，该项仍在 `#2400` 的备注里。
- **`DEPLOY_UPDATE` 若将来真的需要**：正确姿势是两端一起接——服务端 emit + 前端
  常量 + 消费分支 + 真实存在的 query key，判据 4 自动放行。
- 根目录 `tests/` 的 seed 治理红灯属主线既有，未在本单处理；若需要修复应另立单
  （涉及已合入迁移的引用检查与 `#2258` 的不可变 revision 纪律）。
