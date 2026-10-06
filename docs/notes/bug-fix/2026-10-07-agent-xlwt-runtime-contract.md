# Agent 安装必须提供 Scan-Result-GT 的实际解释器依赖

## Decision

将 `xlwt==1.3.0` 加入 Agent runtime requirements，并让安装自检在验证 Pipeline schema 后，使用同一解释器导入 xlwt、初始化 Workbook。缺失或无法初始化时返回非零状态及明确的 `INSTALL_SELFCHECK_FAIL`，避免一台能够执行任务却不能导出归档报表的主机被判为安装成功。

触发证据：2026-10-06 满盘恢复核验中，48 台 Agent venv 都缺 xlwt；4 台样本系统 Python 已有该库，但 `Scan-Result-GT/2026.09.23` 的 `python=null` 以 Agent 的 `sys.executable` 执行，实际仍报 `ModuleNotFoundError`。现场经授权固定 wheel 的单机验证和逐台补齐已恢复导出依赖；本变更固化新安装/后续 requirements 更新的契约。onboard SOP 同步要求检查实际工具解释器。

本变更保持 ADR-0051 已发布包和 manifest 不可变，不修改任何既有工具版本。系统 Python 的安装状态不能替代 Agent venv 验证。

## Alternatives

- 继续只安装系统 `python3-xlwt`：Agent venv 默认隔离，样本已证明系统有库仍失败。
- 修改已发布工具的 `python` 字段或其包内内容：违反发布不可变边界，需另发工具版本。
- 只在现场手动 pip 安装：本轮止血有效，但新主机或重装仍会复发。
- 只加 requirements、不加自检：实际安装环境漂移或依赖不可用时，仍会把问题推迟到真实归档阶段。

## Verification

- 经项目内存保护入口运行 `backend/agent/tests/test_install_selfcheck.py` 和 `backend/agent/tests/test_install_agent_host_id.py`：8 passed。包含实际已装依赖的成功路径，以及缺失 xlwt、Workbook 初始化失败的非零失败出口。
- 对变更 Python 文件执行项目入口 `-m ruff check`：通过。
- `check:quick`：16 个 gate 通过。未配置 DATABASE_URL，schema-at-head 按入口约定提示跳过；没有连接生产数据库。required CI 另行核验，不能以本地结果替代。
- 现场单机离线日志解析/去重/导出已通过（原始 3 行、去重 2 行），48/48 实际解释器导入和版本复核通过；这证明手动补齐，不是本 PR 已部署。
- 原生新轮次的完整 scan/upload/merge/extract 另行验收；不能用安装自检冒充全部平台证据完整。

## Revisit

工具需要另一版本解释器或依赖时，通过新包版本及其依赖契约表达。未来若工具提供独立环境，再评估从 Agent runtime requirements 移除 xlwt；在此之前保持实际执行解释器的依赖自检。

MTK 零结果仅输出 summary、而 Agent 仅接受 org.xls 的契约差异，以及大目录 bundle 的 512 MiB 下载上限，均不在本次安装依赖修复范围内。
