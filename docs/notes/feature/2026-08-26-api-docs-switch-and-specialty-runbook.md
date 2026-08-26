# 2026-08-26 — G22 API 文档开关 + G21 新建专项 Runbook

对应缺口：`docs/reviews/TOOLKIT_INTEGRATION_FEASIBILITY_2026-08-26.md` §2.7
（方向 7：G22 `/docs` 无关闭开关；G21 「新建专项」无操作者视角 runbook）。

## 决定了什么

**G22**（`backend/main.py` + `backend/.env.example` + env 文档表）：

- 新增 `STP_API_DOCS_ENABLED`，控制 `/docs`、`/redoc`、`/openapi.json` 三者
  **同开同关**——schema 与交互式 UI 属同一暴露面，只遮 HTML 不删 openapi.json
  没有意义。
- **缺省保持开启**：Swagger 是对外管理面的一等通道（MTBF 研究 §0.4 的结论、
  `POST /api/v1/auth/token` docstring 自述服务于 Swagger），默认关闭等于把现状
  功能砍掉；开关定位是「公网暴露前的部署侧选择」，不是新默认。
- 解析语义与仓内先例对齐（`core/security.py:67` / `api/routes/metrics.py:23`
  白名单风格）：`1/true/yes/on` 为开，未设置或空串视为未配置 → 缺省开，
  **其余任何值一律关**（文档面宁可不暴露）。

**G21**（`docs/operations/new-specialty-onboarding-runbook.md`）：

- 形态 = 操作者视角的单文件 runbook（登记 → 脚本入库两轨 → 建 Plan →
  试运行 → 上线检查单），设计细节一律链接权威源不复制正文，避免双源漂移。
- 如实标注已知缺口：specialty 字典无 REST 管理路由（只有 `GET /api/v1/plans/specialties`
  查询），新专项 key 目前走 DB 登记——不粉饰成自助流程。

## 放弃的备选

| 备选 | 为什么放弃 |
|------|-----------|
| G22 只在 Nginx 层遮蔽 | 依赖部署侧配置纪律，多机/手工启动即失效；env 方案自包含（审查文档 §2.7 已倾向此项） |
| G22 缺省关 | 砍掉现状功能，违反最小行为变更；外部 agent 工作流（MTBF 管理 face）首当其冲 |
| G22 只关 `/docs` 保留 `/openapi.json` | schema 本身就描述全部端点与鉴权形态，等于没关 |
| G21 把各设计文档细节内联进 runbook | 三处真源必然漂移；runbook 只做步骤编排 + 指针 |
| G21 假装 specialty 可自助创建 | 与代码现状不符，照做会卡死操作者 |

## 如何验证

- 单测 `backend/tests/test_api_docs_switch.py`：解析语义参数化（含空串、空白、
  非法值）+ 缺省开启断言 + 真实 app 上 `/openapi.json`、`/docs` 200。
- 关闭态属进程级配置（FastAPI 构造参数 import 时定格），套件内不起子进程
  uvicorn；部署侧复验命令：
  ```bash
  STP_API_DOCS_ENABLED=0 下重启后：
  curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/docs          # 404
  curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/openapi.json  # 404
  curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health        # 200 对照
  ```
- runbook 中全部端点路径/schema 字段名对照当前代码逐一核实
  （plans/scripts/projects/auth/suites/mtbf 路由文件，2026-08-26 @ main）。

## 何时重议

- 控制面接入统一网关 / SSO 时：`STP_TRUSTED_PROXIES` 与本开关一起纳入网关化决策。
- specialty 管理面立项（方向 7 后续批次）：runbook §3 的「DB 登记」段改写为自助流程。
- 若出现「按项目粒度开关文档面」的需求（现设计是进程级全局），重开小 ADR。
