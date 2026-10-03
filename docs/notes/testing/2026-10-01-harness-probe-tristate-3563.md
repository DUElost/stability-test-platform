# Harness 探针三态与 CLI/IDE 矩阵（#3563 / #3516 G2）

Status: proposed
Class: testing

<!-- 2026-10-01 追加（Owner 裁决后）：stderr 证据落盘 + 两形态不可跑处置 -->

## Decision

严格判定有效最终两题答案；删除从未判定的 Q3，不再声称检查重复加载。
非零退出、超时、工具错误、stderr、回显与缺证据均 UNVERIFIED；默认不可验证非零退出。
root/Agent/AEE 独立矩阵，contract 继承验收与 autoload 诊断分开；保留各 CLI，
新增 Cursor IDE，GUI 无实测 actual 为空。人工输入须版本、新会话、HEAD 与证据来源齐全。

## Alternatives

继续搜索日志正则会把题目或工具输出当答复；把 GUI 历史 expected 填进结果会伪造实际证据。
禁止一切读取的 autoload 不能证明 workspace-only IDE 遵循 scoped 祖先协议，故单独保留诊断。
Q3 重复次数随 Harness 注入机制变化，非本轮根继承最小验收必要项；按父单裁决删除。
未增加 CI、Registry 或全仓通用前置门禁；未改个人配置、信任或生产环境。

追加（2026-10-01，Owner 接受实跑发现后的两条处置）：非零退出与 stderr 两类
UNVERIFIED 都把原始 stderr 落到本地 gitignore 目录 `.probe-evidence/`，报表只记
路径不记正文；不加「良性 stderr 豁免」开关——豁免是人工裁决并记 issue，工具自查
自免会毁掉本单三态的全部判别力。`cursor`（无 `--trust` 宿主上恒 exit=1）与
`codex`（本机 stderr 恒有两行）两形态的 UNVERIFIED 记为**接受的终态**，写进操作
手册「已知不可跑形态」，不以参数绕过换绿。

追加二（2026-10-01，Owner 裁决 IDE 退役 + 首轮人工取证）：`cursor-ide` /
`codebuddy-ide` 退出验收矩阵，人工形态收敛为 Zcode 三格（contract 与 autoload 各
一轮）；ADR-0034 附录 A 的历史实测行保留不动，Cursor 的 `.cursor/rules/` 适配面与
CodeBuddy **CLI** 行不受影响。Zcode **3.14.4** contract 三格人工取证已判 **3 PASS**
（root `Q1=是 Q2=否`、agent 与 aee 均 `Q1=是 Q2=是`，`evidence_source` ＝ #3563
评论）；该组数据带已知利益冲突——预期值在取证前已由本 harness 工作面算出并告知
操作者，故不宜作完全独立证据，独立复核宜在未读预期值前提下重跑。autoload 三格与
Hook 激活仍 UNVERIFIED。

追加三（2026-10-01，独立复核返修）：复核发现 `harness-adapters.md` 把 2026-09-07
**属于 Zcode 3.11.2 的 autoload 实证**贴在 3.14.4 行头，而 3.14.4 当前只有 contract
三格证据、且 contract 口径明确不能证明 autoload。返修为分版本证据标注（3.11.2
autoload / 3.14.4 contract 各归各版本），行头不再带单一版本号，并写明「版本证据不可
跨版本搬运、3.14.4 的 autoload 结论目前不存在」。ADR-0034 附录 A 的 3.11.2 行按
「历史不动」原则保留。纯文档返修，不退 Planner，PR 保持 draft。

## Verification

离线回归 52 passed（统一 runner 实测 cgroup memory.max=6 GiB、swap=0）。
暂换为 main 修复前探针：13 个既有接口反例中 10 failed / 3 passed；恢复后 52 passed。
自测、Ruff、quick 16 gates、治理检查（--base origin/main）与 diff whitespace 检查通过；
quick 的 schema-at-head 未配置隔离数据库而跳过，不代表生产数据库验证。
真实 CLI/IDE × cwd 与 hook 触发独立取证；实现测试通过不替代真实激活/继承验收。

追加实测（2026-10-01，zcode 工作面临时 worktree @639a73d9，跑完已删）：
`claude-subdir-plain --cwd agent --mode contract` PASS（约 100s，Q1/Q2 双是）；
`codex --cwd root --mode contract` UNVERIFIED（`stderr diagnostics`，答复本身有效）；
`cursor --cwd agent --mode autoload` UNVERIFIED（`exit=1`）。即正文「Claude 三 cwd
超时」是实施会话当时的现场、非稳定结论。追加改动后离线回归 57 passed（新增 5 项：
两类 UNVERIFIED 均落盘、无目录不落盘、报表只记路径不记正文、目录不可写不抛错、
rc≠0 仍保留 stderr 落盘）。追加二后 58 passed（新增退役断言：两 IDE 不在矩阵、
人工形态仅 Zcode、旧 id 判为未知矩阵格、模板 3 行）。

## Revisit

新 CLI 输出 schema 必须先取证再支持；未知协议、版本缺失、工具错误继续 UNVERIFIED。
标题自报告仅是最小黑盒可见性证据，不能证明所有硬规则已执行；人工证据真实性需独立复核。
IDE 与 CLI 不合并，历史 autoload 漂移由实际版本证据解释，不放宽 contract 的 root 可见性。
G3/G4 与父单终态覆盖图后续实施；本单不关闭 #3516，不启用宿主 hooks。
`.probe-evidence/` 只在本地判读，任何时候不入库；宿主 stderr 噪声基线变化时重取基线
而不是放宽规则；`cursor`/`codex` 两形态若日后可跑，须以新证据改手册而非改判定。

追加四（2026-10-01，Zcode autoload 三格实测后修题）：Zcode 3.14.4 autoload 实测
root / agent PASS、**aee FAIL**。根因不是形态漂移而是题目缺陷——aee 格 Q2 要求
`Agent 侧 scan / upload`（在 `backend/agent/AGENTS.md`）与 `AEE crash detection chain`
（在 `backend/agent/aee/AGENTS.md`）**同时**可见，而 workspace-only IDE 只注入
workspace 那份，正确答案本就是「否」。Owner 裁决走改题不改编望（方案 A）：每格只问
自己那份契约的标记，root 保留为阴性对照。

守卫测试首版**无判别力**——变异（aee 恢复问两个标记）后仍 59 passed，因为断言写成
`mark in prompt` 的单向包含。已改为互斥断言（外来标记不得出现在该格提示词中），
复测变异下 `1 failed / 58 passed`、恢复后 59 passed。**这是本轮唯一的实质教训：
守卫若挡不住它要防的那个变异，等于没写。**

修复合入后 aee 三格须重跑：`root_version` 锚定 HEAD，探针一改整表作废。

追加五（2026-10-02，CI `pr-agent-tests` 红返修）：新守卫里的两条
「读契约文件断言某标记不存在」属源扫描型否定断言，被
`test_source_scan_anchor_ratchet.py` 判为新 offender——这类断言在锚点漂移时会恒真
（#2639 病）。改走 `SourceGuard.of_repo_path(...).anchored(...)` +
`assert_absent/assert_present`：锚点漂移报「用例已过期」、锚点在位而形态不符报
「防线回归」，两者在红侧第一行即可区分。

变异自证：把 `Agent 侧 scan / upload` 塞进 `backend/agent/aee/AGENTS.md` → 守卫以
`FormRegression` 变红（1 failed / 58 passed），恢复后 59 passed。
**本地 `check:quick` 未捕获此红——quick 16 gates 不含 `pr-agent-tests` 的
ratchet 集合，须以 required CI 为准。**

追加六（2026-10-02，#3585 独立复核返修）：复核指出 #3585 首版把「每格只问本层标记」
**同时套用到两种模式**，制造了一个比原缺陷更严重的假 PASS 反例——`aee/AGENTS.md`
自身要求 root → Agent → AEE 逐层加载，而 contract/aee 只问 AEE 标记时，「root + AEE
可见、漏掉中间 Agent 层」的会话会答 `Q1=是 Q2=是` 并被判 PASS，探针反而看不见它要抓的
继承断链。这与 #3563 明确分开的两种实验相悖：autoload 是初始上下文诊断，contract 才是
祖先继承验收。

返修：`q2_marks(cwd, mode)` 让 Q2 按模式取标记——autoload/aee 只问 AEE 层，contract/aee
同时问 Agent + AEE 两层；root/agent 两格两模式不变。两种模式对同一份证据给出相反判定
（workspace-only 观察 `Q1=否 Q2=是` 在 autoload 下 PASS、在 contract 下 FAIL）。

**本轮教训（比代码本身重要）**：修「题目不可满足」时若把新规则无差别套用到所有模式，
会把一个可诊断的 FAIL 换成一个静默的假 PASS——**信号从「响亮的错」退化成「看不见的错」**。
题目按模式分叉不是过度设计，是两种实验语义不同的必然结果。

变异自证：(1) contract 退回单标记 → `1 failed / 60 passed`；(2) 再叠加放宽期望值
（模拟「缺 Agent 也 PASS」的判据）→ `3 failed / 58 passed`。61 passed（探针）+
ratchet 全绿。

追加七（2026-10-03，hook 激活确认后的文档收口）：Owner 确认 Claude hook 与 Codex Stop
均已真实验证并合入——Claude PreToolUse 由 #3595（`7da0c543`）接线 root/Agent/AEE 三入口，
真实 `claude -p` 2.1.284 自然触发 18/18 PASS；Codex Stop 由 #3591（`8d1e4a96`）把
PASS/FAIL/UNVERIFIED 改经 Stop JSON `systemMessage` 投递（stderr + exit 1 会被真实 runtime
丢弃）。此前「Claude hook 仍 UNVERIFIED（本轮模型 API 503）」是 503 那轮的现场表述，随
#3595 合入不再成立。

据此刷新 `harness-adapters.md` 的 Zcode 3.14.4 六格报告（2026-10-02）：
[六个独立会话的实际取证 revision](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5948001601)
为 `af4edf99`。判卷时 main 已到 `bb1f34d9`，`root_version` 不一致使工具先将六格判为
UNVERIFIED；经 Owner 批准核实两 revision 仅两份 deadlock 测试变化、root/Agent/AEE
契约与 `harness_probe.py` 未变后，沿用原答复、不重跑，通过
[人工等价性判断](https://github.com/DUElost/stability-test-platform/issues/3563#issuecomment-5948070189)
将 `bb1f34d9` 作为最终判卷 HEAD，contract 与 autoload 各 3 PASS。agent/aee 两轮 Q1
读数相反（是/否），支持 workspace-only；contract PASS 不等于自动注入。

**独立性边界**：操作者取证前已知 `q2_marks` / `expected_for` 判据，存在已披露利益冲突；
[#3516](https://github.com/DUElost/stability-test-platform/issues/3516) 的独立核实/重验仍 pending。

S6 历史核算更正：`4e55ee24` 的 adapter Git blob 为 **100 行 / 9950 B**，原记 9972 B 有误。
本次返修 adapter 为 **100 行 / 9999 B**，仍在 100 行 / 10000 B 预算内；仅压缩 Zcode 重复表述。
返修验证：Git blob 字节/换行数、两 revision 差异核算、治理结构检查（`--base origin/main`）、
`check:quick` 16 gates 与 diff whitespace 均通过；未配置数据库，schema 对齐跳过。本轮未重跑 Zcode。

**遗留**：hook 实测的原始日志
（`/tmp/stp3516-claude-fixed-8oaw56ny/`）在提交前已不存在，note 如实声明不声称可下载。
