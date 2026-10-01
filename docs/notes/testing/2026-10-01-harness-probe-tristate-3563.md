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
