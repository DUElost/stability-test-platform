# Harness 探针三态与 CLI/IDE 矩阵（#3563 / #3516 G2）

Status: proposed
Class: testing

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

## Verification

离线回归 52 passed（统一 runner 实测 cgroup memory.max=6 GiB、swap=0）。
暂换为 main 修复前探针：13 个既有接口反例中 10 failed / 3 passed；恢复后 52 passed。
自测、Ruff、quick 16 gates、治理检查（--base origin/main）与 diff whitespace 检查通过；
quick 的 schema-at-head 未配置隔离数据库而跳过，不代表生产数据库验证。
真实 CLI/IDE × cwd 与 hook 触发独立取证；实现测试通过不替代真实激活/继承验收。

## Revisit

新 CLI 输出 schema 必须先取证再支持；未知协议、版本缺失、工具错误继续 UNVERIFIED。
标题自报告仅是最小黑盒可见性证据，不能证明所有硬规则已执行；人工证据真实性需独立复核。
IDE 与 CLI 不合并，历史 autoload 漂移由实际版本证据解释，不放宽 contract 的 root 可见性。
G3/G4 与父单终态覆盖图后续实施；本单不关闭 #3516，不启用宿主 hooks。
