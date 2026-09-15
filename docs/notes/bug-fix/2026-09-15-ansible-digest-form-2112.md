# Ansible digest 簿记修复：提取规范化 + 写完即验（#2112）

Status: implemented
Class: bug-fix

## Decision

**① 提取规范化**（`update_agent.yml` Extract 任务）：`regex_search('CODE_DIGEST=(\S+)', '\1')`
在多行 stdout 下返回 **list**，`copy content` 因此把 marker 写成 `["sha256:…"]`（agent
`^sha256:[0-9a-f]{64}$` 判非法 → 上报空 → 列保留旧值 → 门禁可能误判收敛）。改为语义
确定的 **`regex_findall(...) | first`**（`or ['']` 兜空匹配），code/resources 两处同改。

**② 写完即验（fail-closed）**：两个写入任务之后新增
「`slurp` 回读 → `assert`（形态 `^sha256:[0-9a-f]{64}$` **且** 取值等于本次计算值）」，
`fail_msg` 打印 got/want。本类缺陷（写入成功但形态错）此前会静默通过写入任务，只有
回读断言能当场拦住——**这是与 #2011/#2091 同族的第三次「写入端做了、读取端认不出」**，
故按通则补上"写完即验"。

## Alternatives

- **只给 `regex_search` 加 `| first`**：依赖其多行返回 list、单行返回 str 的实现细节，
  单行场景下 `first` 会取**首字符** → 更脆；`regex_findall` 语义确定（捕获组列表）→ 采用。
- **只改 `compute_deploy_digest.py` 的输出格式**（如加 JSON 包装）：治不了模板层的类型
  问题，且把契约绑死在工具输出上 → 否决。
- **放宽 agent 侧校验**（接受列表形态）：放弃 fail-fast、掩盖写入端缺陷 → 否决。
- **只在 CI 静态断言表达式**：拦不住真实类型问题（本轮实测正是静态看很合理、跑起来才
  暴露）→ 静态契约 + 真实回读断言**两条都要**。

## Verification

- 新增 `tests/test_ansible_digest_bookkeeping.py`（PR 路径执行）→ **3 passed**：
  提取必须用 `regex_findall`/首元素/空兜底且不得回退 `regex_search`；回读+断言任务存在且
  位于写入之后、覆盖两个 marker、断言同时校验形态与取值；**形态正则与 agent
  `_ARTIFACT_DIGEST_RE` 逐字一致**；
- `ansible-playbook --syntax-check` 通过；
- **单台实跑（`.87`，探针树触发写入）**：`ok=53 changed=6 failed=0`；
  `Write ARTIFACT_DIGEST`→changed、`Read back deployed artifact digests (#2112)`→ok、
  `Assert deployed digests are well-formed and match computed values (#2112)`→ok；
  主机 marker 为**纯值** `sha256:0d94b31a…`，且**DB 列在 ≤15s（1 个心跳）内反映该值**
  ——修复前该值永远到不了列（#2112 时序实验）；
- **负向验证**：同一断言对列表形态 `["sha256:0…0"]` 判 `evaluated_to=false`（能拦住 #2112
  形态，铺开当场 fail 而非静默）；
- `python scripts/run_gates.py check:quick` → **[OK] check:quick (10 gates)**；
- 现场恢复：`.87` 用 `?force=true` 热更新写回期望值并删除探针文件，收尾 `PROBE_GONE`。

## Revisit

- 本修复**随下次 playbook 运行生效**（无需重启控制面）；下次铺开时留意
  `Read back`/`Assert` 任务是否出现在 recap 且为 ok；
- 同族第三例（#2011 argparse 接线 / #2091 install 边界 / #2112 模板类型）——建议后续在
  新增"部署态元数据写入"时，一律按本 Note 的"写完即验"落地（wrapper `write-digest` 侧
  已有格式校验，playbook 侧本轮补齐）；
- 若 ansible 版本升级导致 `regex_findall` 行为差异，本单的静态断言 + 实跑回读断言会同时变红。
