# #2743 fake_agent serve --lifetime 改绝对期限：stop 提出闭包 + 到期日志

Status: implemented
Class: bug-fix

## Decision

`tools/dev/fake_agent.py` 的 `cmd_serve` 里，`stop = time.time() + args.lifetime`
放在 `work()` 闭包**内**——`_with_connection` 每次自愈重连都重新进入 `work()`，
期限随之清零。同一函数里 `state`（注入文件 mtime 游标）被刻意提到闭包外跨重连
保留，而 `stop` 没有：两个状态的存活期被不一致地处理，这就是缺陷本身。夹具自身
文档写明「dev 镜像无 websocket-client → 只能 polling，polling 会话约 5 分钟掉
一次」，即重连是**常态**——`--lifetime` 实测形同虚设（issue 实测超期 2.7 倍、
存活 ≈13.5 分钟仍在上报），一台不存在的 host 连同假设备长期 ONLINE 污染共享
dev 栈（claim 正因设备仍 ONLINE 而成功）。

修法（issue 建议的最小方案 1+2）：

1. **`stop` 提到 `work()` 之外**（与 `state` 同级）：`--lifetime` 成为 serve
   启动即定的**绝对期限**，跨重连不重置；
2. **到期显式记一行日志** `serve 到达 lifetime=Ns，主动退出`——issue 取证时
   无法归因退出时机，正因为没有任何一行说它退了；
3. argparse `--lifetime` 补 help 文本，写明绝对期限语义（建议 3 的改名方案
   不采用：默认读法「到点自退」现在成立，无需换名）。

## Alternatives

- **改名为 `--lifetime-per-connection` 并保留逐连接语义**（issue 建议 3）：
  否决。夹具红线就是「到点自清理」，逐连接期限与红线相反；且文档样例
  `serve --lifetime 600` 的默认读法即绝对期限，改语义不如修实现。
- **`--lifetime 0`/负值 = 永久**：不引入。原实现无此语义，issue 也未要求；
  0/负值自然走「立即到期退出」，不特判。

## Verification

- `python -m pytest tests/test_dev_fake_agent.py -q` → **36 passed**。新增
  `test_serve_lifetime_is_absolute_across_reconnects`：可控时钟（sleep 即推进，
  strftime 委托真实 time）+ 伪造 `_with_connection`——首连接消耗 2 个 tick 后
  掉线（模拟 polling 掉线）、重连后断言总时长 ≤ lifetime + 单次 tick、恰走
  一次重连、自退日志在 stdout。
- **变异自证（实跑）**：把 `stop` 临时放回 `work()` 内（还原缺陷形态）→
  该用例在 `assert elapsed <= 300+5+1` 上红（重连后重新起算 300s ⇒ elapsed
  ≈315）；还原修复 → 36 全绿。判别力对准缺陷本身，不是摆设。
- `python -m py_compile tools/dev/fake_agent.py` 通过。

## Revisit

- 宿主上真跑一次 dev 栈到期自退（本轮仅单测级验证，未起 dev compose）——
  issue 的观测口径可用 `/proc/*/cmdline` 扫描（排除观察者自身 pid，dev 镜像
  无 ps）。
- 退出时是否应主动向控制面「下线」（目前依赖心跳超时转 OFFLINE）——夹具
  设计红线是「只注册与回报」，补下线动作会扩执行面，超出本单。
