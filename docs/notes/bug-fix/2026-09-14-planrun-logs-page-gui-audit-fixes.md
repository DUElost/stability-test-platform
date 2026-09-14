# PlanRun 日志页 GUI 评测修复批（弹窗取消回归 / 对比度 / 空态 / 深链回跳 / 搜索导出 / a11y）

Status: implemented
Class: bug-fix

## Decision

2026-09-14 对 `http://172.21.x.x/execution/plan-runs/375/logs` 做黑盒 GUI 评测（截图与
像素取证存 `gui-test-screenshots/`，主工作树未跟踪），据评测结论逐项修复：

1. **归档弹窗「暂不归档」无法取消（评测 P1-1）→ 降级为回归测试**。线上复现实验证明：
   清净点击下 radix `AlertDialogCancel` 正常关闭（MutationObserver 记录到 removed），
   评测当时的「无法关闭」是自动化点击被 DOM 重渲染打断的伪象，代码路径无缺陷。真实缺口
   是 #780 测试只覆盖「弹出」不覆盖「取消」——补 `点击暂不归档关闭弹窗且不触发 extract`
   回归测试钉住行为。
2. **次要文字对比度（P1-2）**：根因有二——light 主题 `--muted-foreground` 亮度 46.9%
   （白底 4.53:1、bg-muted 上 4.15:1 双双贴地）与透明度变体（`/70` 白底仅 2.64:1）。
   修复：token 亮度 46.9%→42%（白底 5.42:1、bg-muted 上 4.97:1，WCAG AA 达标，仅降
   亮度不改色相），并移除时间戳/设备行/计数徽标/空态副文案上的 `/70`、`/60` 透明度。
3. **空态（P2-3）**：改为容器内垂直居中 + 图标 + 副文案提及搜索关键字 + 仅在有激活筛选
   时渲染「清除全部筛选」一键复位三筛选。
4. **「最后更新」（P2-4）**：改「数据更新于 + 完整日期时间」，与 run 内事件时间明确区分。
5. **滚动条横移（P2-5）**：事件列表容器加 `[scrollbar-gutter:stable]`，滚动条出现/消失
   不再引起右列 15px 横移。
6. **登录深链回跳（P2-1）**：**真凶是 `main.tsx` 注册的 auth 失败处理器**——401 会话终态
   时 `window.location.href='/login'` 硬跳转，router state 必丢（client.ts 兜底分支与
   router 的 `<Navigate state>` 均不在此路径上，GUI 实测 state=null）。修复：硬跳转带
   `?next=`；新增 `utils/authRedirect.ts#resolvePostLoginTarget` 统一解析，优先级
   state.from → ?next= → '/'，站内校验（`/` 开头且拒绝 `//` 协议相对，open-redirect
   防护）；ProtectedRoute/AdminRoute 软跳路径补 state。端到端实测：深链 →
   `/login?next=%2Fexecution%2Fplan-runs%2F375%2Flogs` → 登录 → 精确落地原页。
7. **a11y（P3）**：事件详情 div→原生 button（自带键盘激活）+ `aria-expanded`；筛选芯片
   补 `aria-pressed`；登录表单补 `autocomplete="username"/"current-password"`。
8. **搜索与导出（P2-2）**：后端 `/plan-runs/{id}/events` 加 `search` 参数（大小写不敏感
   匹配 title/description/device_serial，facets 仍基于未过滤全集，与 stage/severity
   同口径）；前端搜索框 300ms 防抖接入 query key；「导出 CSV」按后端单页上限 500 分块
   拉取当前筛选+搜索全集（20000 行硬上限），前端拼 CSV（BOM + 引号转义）下载。

## Alternatives

- **全局加深 `--primary`（按钮白字 3.68:1 < 4.5:1）**：不采纳——色板曾获用户明确确认
  （index.css「恢复 Phase 1 前 STP 蓝系」），改蓝 500→600 属品牌级决策，留 Revisit。
- **归档弹窗改为非阻断 banner**：不采纳——#780 刚按模态收口，评测未证伪其交互设计，
  只补取消路径的回归测试。
- **事件导出走后端流式端点**：不采纳——现有端点 + 客户端分块已满足一次性导出需求，
  避免新增后端面（鉴权/流式/文件名头）。
- **搜索在客户端过滤已加载页**：不满足「5433 条里找一台设备」的本质需求，必须后端过滤。

## Verification

- 后端：`backend/tests/api/test_plan_run_aggregation_endpoints.py` 61 passed（含新增
  search 命中 title/serial/无命中、与 severity 组合 4 例）；ruff clean。
- 前端：vitest 全量 807 passed（新增：弹窗取消回归 1、EventStream 新特性 7、LoginPage
  回跳矩阵 5→6）；`tsc --noEmit`、`eslint src` 零输出。
- 对比度：脚本实测新 token 白底 5.42:1 / bg-muted 4.97:1（修复前 4.53/4.15，透明度变体
  2.64）；GUI 实测时间戳 computed color rgb(90,104,125) 与预期一致。
- GUI 端到端（vite dev 连生产后端，Origin 走 CORS 白名单 `localhost:5173`）：搜索请求
  确认携带 `search=monkey`（生产后端未部署该参数故结果未过滤，属预期）；空态清除按钮
  复位三筛选；导出触发 11 个 `limit=500` 分块请求（5433/500 上取整，与循环终止条件
  吻合）后按钮状态正常回落；深链回跳全链路通过。
- 未验证（如实记录）：IAB 运行时输入管线中途损坏 + 不发 download 事件，CSV 文件本体
  落盘与明暗两主题的整页视觉复核未做（逻辑由单测覆盖）。

## Revisit

- `--primary`（light）白字 3.68:1 不达 AA：待设计裁决是否蓝 500→600（`217 91% 48%`，
  可得 5.16:1），连带 `--ring`/`--sidebar-primary` 一致性。
- facets 在搜索过滤下仍显示全集计数（与 stage/severity 口径一致，刻意保留）；若用户
  反馈「搜索后徽标数不变」困惑，再议是否加「命中数」徽标。
- 生产部署后需在真实数据上复测搜索过滤与 CSV 落盘（本次受限于生产后端未含新参数）。
