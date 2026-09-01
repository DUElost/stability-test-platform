# 前端架构全量只读审查与未来演进规划

Status: implemented
Class: architecture

## 决定了什么

1. **确立全站前端健康基准**：完成对 `frontend/` 全量代码（React 19 / TanStack Query 5 / Tailwind 4 / React Router 7 / Radix Primitives / Socket.IO）的只读深度审查。TypeScript 类型检查 0 错误、ESLint 0 告警、Knip 0 死代码、Vitest 89 文件 / 652 用例 100% 通过。
2. **沉淀全景审查与演进规划文档**：产出 `docs/reviews/FRONTEND_ARCHITECTURE_REVIEW_AND_ROADMAP_2026-09-01.md`，覆盖架构分层、设计令牌、Page Shell 规范、Socket.IO 失效驱动与多端同步、核心工作台剖析、以及历史审查（H1~H3, B7, A4）闭环验证。
3. **制定未来四维演进路线**：
   - *维度一（业务深化）*：ADR-0030 P3 用例套件可视化编排、#506 脚本运行态 30 天洞察与安全退役辅助、定时调度选机组件复用；
   - *维度二（AI 赋能）*：从独立聊天走向 PlanRun 详情页一键 AI 异常归因与自然语言选机派发；
   - *维度三（交互效能）*：超宽屏/高密设备网格自适应、海量日志流背压与 Worker 线程节流、AEE 调用栈火焰图可视化；
   - *维度四（工程韧性）*：接入 Playwright 核心链路 E2E 门禁、前端可观测性（Web Vitals / 错误监控）、设计系统 Storybook 沉淀。

## 放弃的备选

- **推倒重构或切换技术栈（如引入重型状态机/微前端）**：拒绝。现有基于 TanStack Query 5 + Socket.IO 失效提示 + 语义设计令牌的轻量架构极其稳固，问题在于业务纵深而非技术栈更迭。
- **全局项目上下文选择器（强制跨页跟随）**：维持 ADR-0029 决议，保持页面级筛选（URL 显式参数），避免隐式上下文导致的测试排查偏差。

## 如何验证

- `npm run type-check`
- `npm run lint`
- `npx vitest run`（89 文件 / 652 用例）
- `npm run knip`
- `docs/DOC-MAP.md` 引用完整性核对

## 何时重议

- 当设备规模由千台级扩容至万台级出现前端渲染/通信瓶颈时（重议日志流流控与拓扑网格分屏架构）；
- 当 ADR-0030 P3 用例套件可视化编排进入实质排期时（作为实施输入）。
