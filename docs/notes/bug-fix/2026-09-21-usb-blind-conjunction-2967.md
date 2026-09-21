# USB 失明 paging 的零权限合取（#2967：tree_empty ∧ device 账上有行）

Status: implemented
Class: bug-fix

## Decision

以**纯规则改动**补上 #2967 的合取，不新增代码/指标：

- 规则 `StabilityHostUsbBlind`（critical，`for: 15m`）=
  `stability_host_health_reason{reason="usb_tree_empty"} >= 1` ∧
  `sum by (host_id)(stability_host_device_adb_state) > 0`——两个 series 都是既有
  gauge（#2902 的 reason / #2754 的账目分桶），第二源即「这台 host 本来应有设备」的
  账本锚；
- **ghost row 担忧在本合取下反转**：票面担心「行数>0 是常态不是证据」——但
  「账上有设备 ∧ 内核零可见」即使行全陈旧，本身就是要上报的**账实不符**形态，
  ghost 该被清账而不是被静音（清账指引写进告警 description 尾句）；
- **锚点不设新鲜度窗**：7d 窗会让 .63 型 11 天长失明在第 7 天自我静音——恰是 #2900
  付过学费的「绿而空」；
- `for: 15m` 是实测需要而非装饰：热更新批量推送中 .61 出过一次「重启 tick 报空树、
  账上仍 20 台」的单心跳瞬态；
- 顺手修 `StabilityUsbKernelLogChannelDark` 描述中的「还需补合取」措辞（已补）；
- **契约解析器补向量匹配形态**（`tests/test_prometheus_alerts_contract.py`）：
  `and on (host_id)` 括号里的标签列表此前会被裸 token 规则误读成「未知指标 host_id」
  ——与本仓 #735（`or` 被当指标名）同一原则：盲区修在解析器，合法 PromQL 不为
  解析器让路。`_VECTOR_MATCH_RE` 先剥离 `on/ignoring/group_left/group_right`，
  自证用例钉三态（修饰符吞掉、左操作数标签、聚合内层指标）。本仓第一条用
  `and on(...)` 的规则。

## Alternatives

- **在 capacity 里上报 `expected_device_count`（agent 侧锚）**——否决：agent 看不到
  DB（票面「控制面回填期望值」选项），回填即引入一个需要维护的第二真值源；device
  表行经 `host_device_adb_state` 已经是拉取期现算的在册事实，直接用它做合取零新代码；
- **per-host 规则吃 `usb_device_count == root_hub_count` 原始计数**——否决：#2966 规则
  注释已钉「不自己发明第二套失明判据」（第二份真值迟早漂），合取只消费 reason 结论；
- **等 #2957 权限裁决后只靠 ControllerDead**——否决：A/B/C 依赖签字可长挂，而 .102
  明天就会再死一次（22-27h 周期，#2971 换线前）；本规则与裁决正交，之后也不撤。

## Verification

- `promtool check rules`：30 条通过；
- `promtool test rules`：**SUCCESS**——三态场景（fire=.102 型回放 30m 亮；两枚 not-fire
  钉=空柜 .91 无账、热更瞬态 .61 被 for 吸收）；改中发现并修掉 YAML 坑：description
  纯量含「见 #2972」时空格+# 被解析成行内注释致期望文本截断，已整体加引号；
- 现势对账（live Prometheus）：合取表达式当前命中 **0 台**（.91 被合取正确排除；
  .61/.90 重启 tick 已清）——满足票面「现网 0 误命中」验收；
- `tests/test_prometheus_alerts_contract.py` + `test_ci_promtool_scenario_gate.py` +
  `test_host_health_reason_surface.py`：**69 passed**（含新解析器自证与「每规则必有场景」钉子）；
- `run_gates.py check:quick` 通过（结果记录于 PR 评论）。

## Revisit

- 部署生效仍需人工重放规则副本 + `POST /-/reload`（§文档线：合并≠生效，ADR-0011
  未落地）；本单合入后由控制面部署轮执行并在 fleet 复跑合取表达式确认 0 误报；
- 若 #2957 选 A/C（内核通道打通），本规则与 ControllerDead 并存——双源互验，不静音
  任何一方；若 #2972 落地自动 rebind，本告警 description 的「不要自动 rebind」措辞
  需同步；
- ghost 清理若长期不做，本规则会把「撤线机柜残留账」持续 paging 成 critical——那是
  #2754/#2962 的清账债，不是给本规则加静音的理由（票面「不做什么」）。
