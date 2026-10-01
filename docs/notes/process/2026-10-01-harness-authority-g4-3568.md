# G4 权威指针、退役归属与强制力覆盖图（#3568 / #3516）

Status: proposed
Class: process

## Decision

修正 Living 入口的旧 CLAUDE 导入/领域路由叙述、CSRF 路径和 AEE 默认值复制。
覆盖图区分规范、机械 scope 与真实触发；新增凭据读取、共享工作树、auto-merge/main，
按父单冻结的 enforced/partial/structural/residual 描述，不新建状态机或 gate。
低频真实探针不等于恢复已退役常驻 L1；周退役机制单一归属 #3534。

## Alternatives

源码字段/指针可定位事实，不再复制 baseline/burst 数值；旧 checker 不能充当当前强制证据。
不把结构存在、helper 测试、GitHub PR 保护或模型承诺写成全 Harness 实际触发。
本轮只修文档，不为 residual 顺手新增门禁或改 host-local/服务器权限。

## Verification

三层 CLAUDE symlink 实际解析到同目录普通 AGENTS；四个 Cursor frontmatter 与基线逐字
相同；新指针与 Markdown 相对链接目标存在，旧 CSRF/immutability checker 路径确实不存在。
`check_governance_surface.py --check --base origin/main` 与 `--self-test` 通过，
`check:quick` 16 gates 通过（外层 6 GiB、swap=0、env-i），`git diff --check` 通过。
schema-at-head 未配置隔离库，按 gate 条件跳过，不宣称数据库验证；未运行新 pytest。
服务器只读 API 观察：PR 保护配置存在、管理员适用、strict 六项 status checks、禁止 force push，
PR bypass 为空；review count=0，独立复核/Owner ready 尚非服务器机械保证。未真直推验证。
源码核实 AEE 构造器 baseline/burst 配置优先级、CSRF 中间件及现有版本 API 的 422，
FIFO draft 过滤/队首 auto-merge 维护；公开 settings 的 deny 仅四个 Edit，修正文档过度声明，
没有修改 permissions。覆盖图 ASGI/Plan 的结构事实与运行时拒绝分开，源码符号替代漂移行号。
真实 CLI/IDE/hook 触发仍单独 UNVERIFIED，不由文档检查替代。

## Revisit

不删除历史留档或凭零引用删除兼容面；#3534 Unit 4.2 按三角退出判据承接。
G2/G3 在途文档内的 Python 版本叙述与历史预算迁移待串行收口，不能宣告 G4/#3516 完成。
范围外 D6 受控写取源、短 SHA 与私有配置清理不混入本单；未改 permissions/trust/hooksPath/生产。
