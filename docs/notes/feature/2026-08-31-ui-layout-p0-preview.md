# UI 布局 P0 预览：PlanRun 详情深层 + Plan 执行密度

Status: preview
Class: feature

## Decision

在 **:8081 → `frontend/dist-preview`** 人工验收布局翻新；**不合入 main / 不换 `dist-prod`**，直至确认。

色板保持 STP 蓝系。本批只收密度：

- `PANEL.root` / SectionHeader 实色条（去渐变）
- PlanRun 详情 gutter 收紧；Archive / Dedup / TCR / EventStream / LogEvents 内边距收敛
- 执行页：DispatchCockpit 去 Card 默认大 padding；表舞台去双层边框；矩阵 hover 去 shadow
- **Activity 对齐（续）**：异常来源改为下划线 Tab（默认本次新增）；异常仪表盘页头改为「标题 + 副文 | 时间窗 chip」；PlanRunHero 标题行收束

基础设施：`deploy/control-plane/nginx/stability-platform-preview.conf` + `.gitignore` 的 `dist-preview/`。

预览 Origin 须进控制面 `CORS_ORIGINS`（与 CSRF 白名单同源）：生产 `.env.backend` 须含 `http://<控制面内网IP>:8081`，否则 cookie 写操作会 `403 CSRF check failed`。

## Verification

```bash
# 预览构建（写 dist-preview，不动 dist-prod）
cd frontend && npm run type-check && npm run lint && npx vitest run
npm run build && rm -rf dist-preview && cp -a dist dist-preview
curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8081/
```

人工：对比 `:80`（正式）与 `:8081`（预览）的 PlanRun 详情与执行页。

## Revisit

通过后开 PR 合 main，再按正式流程换 `dist-prod`。驳回则保留分支或丢弃，正式站无感。
