---
name: ui-preview-review
description: 前端 UI 改动与筛选工作台的隔离预览与实机自检 SOP。触发时机：改动列表页筛选栏、工作台布局、下拉或搜索控件，或需要启动 stp-ui-review 预览并做 Playwright 截图核验时。
---

# 前端 UI 预览与实机自检 SOP

## 1. 隔离分支与前置单测

1. 从 `origin/main` 创建独立 worktree；不得直接在共享工作区 `main` 上构建预览。
2. 针对筛选联动改动编写 Vitest 用例时，必须包含**互斥造数**（例如仅存在于离线或非就绪设备的版本/型号）。
3. 在同一组用例中，必须断言切换状态（「空闲」「仅显示就绪」）后非当前范围的选项不出现在下拉中，并断言往返切换（「全部 → 需关注 → 全部」）可以正常切回。
4. 运行 `npx tsc --noEmit`、`npx eslint <touched-files> --max-warnings 0` 与相关 `vitest run`；涉及筛选守卫时建议同时运行 `pytest tests/test_frontend_filter_workbench_guard.py`。

## 2. 启动隔离预览环境（`stp-ui-review`）

> 警告：预览构建与启动必须只针对 `stp-ui-review` 隔离栈执行，不得改动生产控制面 `127.0.0.1:8000` 或生产 `frontend/dist-prod`。

1. 提交改动到工作分支并记录 `<SHA>`。
2. 运行 `.venv/bin/python tools/dev/ui_review_env.py up --sha <SHA>` 启动 `http://stp-ui-review.localhost:15179/ui-review/`。

## 3. 实机 Playwright 巡检与截图核验

1. 运行 `node tools/dev/ui_review_probe.mjs --out-dir /tmp/ui-review-probe`（或针对新页面扩展检查），核验以下三项：
   - `1440×900` 与 `1280×800` 双视口均满足 `docOverflow === 0`；
   - 同排主筛选控件（`FilterSearchInput`、`SearchableSelect`）高度差 `<= 2px`；
   - 设备页 `全部视角 → 需关注 → 全部视角` 往返切换成功。
2. 必须通过 `read_file` 人工审阅关键状态截图（**空闲态**、**多条件激活态**、**下拉展开态**），确认激活 `重置筛选` 或 chips 后不发生意外折行或遮挡。

## 4. 收尾清理

1. Owner 确认预览效果后，运行 `.venv/bin/python tools/dev/ui_review_env.py down` 销毁预览栈，不得遗留运行中的预览容器。
2. 按 `AGENTS.md` 附带 Agent Note 提交 PR，合入并部署生产前端后再回收临时 worktree。
