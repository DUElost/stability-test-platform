# 队首告警指纹去重补行尾终止符（#2075）

Status: implemented
Class: bug-fix

## Decision

`scripts/ci/pr-automerge-queue.sh` 的告警去重守卫改用**带行尾终止符**的匹配：

```bash
- if printf '%s' "$existing_body" | grep -qF "queue-blocked-fingerprint: ${fingerprint}"; then
+ if printf '%s' "$existing_body" | grep -qF "queue-blocked-fingerprint: ${fingerprint} -->"; then
```

理由：指纹是 `head=#N failed=<按 REQUIRED 顺序逗号连接>`，而 `grep -qF` 是**子串**
匹配。失败集**收敛**（前几项仍红、后面某项转绿）时，计算值正好是存量值的子串 →
被误判为 unchanged → **不刷新正文**，正文继续列着一个已经通过的 check。

终止符 ` -->` 取自正文渲染处（`:113` 那行以 ` -->` 结尾），加上后：收敛值未命中、
原值仍命中。

## Alternatives

- **`grep -qxF` 整行比较**：正文行是 `<!-- queue-blocked-fingerprint: ... -->`，
  整行比较得把 `<!-- ` 前缀一并写进匹配串——与渲染处耦合更紧、更容易再次写错；
  否决。
- **指纹改带长度/序号（如 `failed_count=N`）**：能从根上消除前缀歧义，但要改正文
  格式，进而影响已存在的告警正文（旧正文的指纹不带计数，会一次性全部不命中 →
  每张存量告警被改写一次）。收益不抵噪声；否决。
- **不做**：影响仅为正文陈旧（多列一个已通过的 check），不误导方向、不影响队列
  行为。但既然 #1655 第 5 条已经把它记成「待处理」，且修法只有一行、有可回归的
  测试，就直接修掉。

## Verification

- `python -m pytest tests/test_automerge_queue_alerts.py -q` → **20 passed**。
- 新增 `test_shrunk_failure_set_still_updates_alert`：存量正文写**更长的**失败集
  （`lint:FAILURE, pr-agent-tests:FAILURE`），当前 checks 让 `pr-agent-tests` 转绿
  （计算值收敛为 `lint:FAILURE`）→ 断言脚本调用 `issue edit` 且 stdout 不含
  `unchanged`。
- **反向验证**：把 `grep` 还原成不带终止符的形式 → 该新测试失败，且**只有它失败**
  （既有 `test_same_fingerprint_writes_none` / `test_alert_body_renders_placeholders_and_self_dedups`
  仍绿，说明「真·同指纹零写入」路径未被破坏）。
- `bash -n scripts/ci/pr-automerge-queue.sh` 通过。

## Revisit

- 若将来告警正文的渲染格式改动（例如指纹行不再以 ` -->` 结尾），这条匹配会**静默
  退化为恒不命中**（表现为每次 reconcile 都改写告警）。真出现时，正确做法是在渲染
  与匹配之间引入一个共用常量（例如由渲染函数产出整行、匹配复用同一函数），而不是
  再手抄一次字面量。
- 本条只解决「前缀歧义」。`required check` 的结论解析（`missing` / 启动窗口等）由
  #1792 一系改动负责，不在本单范围。
