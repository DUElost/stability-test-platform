# 稳定项目 Python 入口与门禁边界（#3566 / #3516 G3）

Status: proposed
Class: testing

## Decision

shell 入口定位脚本所属 checkout，使用该 checkout 的 `.venv/bin/python`，切根后执行。
不猜系统 Python、激活环境或另一 worktree 的依赖；环境初始化/链接仍是明确前提，缺失可见失败。
根契约只链接操作细节，保留八条硬不变量/S11 与预算。quick、check:pr 和 required CI
的覆盖关系只在 dependencies-and-quality 定义；本地跳过或成功不代表 required CI 通过。

## Alternatives

裸 python 命中 PATH/ambient 环境不稳定；自动跨 worktree 找解释器会掩盖依赖初始化状态。
入口不安装依赖、不读取配置文件，也不新增门禁；pytest 继续经已合入的内存保护 runner。
不修改 G2 在途文件、不迁出根硬不变量或改变 Registry/批次执行语义。

## Verification

13 项真实 shell/linked worktree/root/深 cwd、引号空格路径、argv/stdin/退出码、缺环境
不回退、不读取 env 的回归通过；统一 runner 实测 6 GiB / swap=0。
可逆变异：取消切根 4 failed / 2 passed；改为系统 Python 3 failed；恢复后 13 passed。
当前项目 AEE 深 cwd 实跑入口，cwd 与解释器均属于当前 worktree。
Ruff、治理（--base origin/main）、quick 16 gates 与 whitespace 检查通过；根契约
79 行 / 6695 字节，八条硬不变量与 S11 未变。schema-at-head 未配置隔离数据库，明确跳过。
初次 quick 发现 CodeQL 链接无对应仓库文件；回查 GitHub workflow API 确认为动态托管，
改为真实 workflow URL 后治理和 quick 重跑通过。

## Revisit

此 shell 入口面向已初始化的 POSIX 开发环境；Windows 工作面须取证后提供等价入口，未声明验收。
它是解释器启动器，不是新的权限或 pytest 沙箱；不允许用它绕过 scripts/run_pytest.py。
G4 继续处理历史版本、失效路径和漂移；真实 Harness/IDE 行为由 G2 矩阵单独验收。
