# ADR-0042 P2（#1 安全与会话域）：core/security + cors 迁移到 AuthSessionSettings（#737）

Status: implemented
Class: architecture

## Decision

按 D2 候选清单的第一优先项，把**安全与会话域**迁移到
`backend/core/settings/security.py::AuthSessionSettings`（10 个字段 + 4 个派生属性）：

| 迁移内容 | 说明 |
|---|---|
| cookie 会话（5） | `AUTH_ACCESS_COOKIE_NAME` / `AUTH_REFRESH_COOKIE_NAME` / `AUTH_COOKIE_PATH` / `AUTH_COOKIE_SECURE` / `AUTH_COOKIE_SAMESITE` |
| 开关（2） | `STP_ALLOW_REGISTER` / `STP_CSRF_ENABLED` |
| CORS（3） | `CORS_ORIGINS` / `CORS_ALLOW_METHODS` / `CORS_ALLOW_HEADERS`（默认值从 `cors.py` 的 `DEFAULT_CORS_*` 随迁，外部零引用） |

**形态要点**：

1. **字符串语义原样保留**（等价性优先）：`AUTH_COOKIE_SECURE`（仅 `"1"` 为开）、
   `STP_ALLOW_REGISTER`（空=按环境）、`SAMESITE`（非法值在读取侧回落 `lax`、
   在 guard 侧按**原始值**报错）、`STP_CSRF_ENABLED`（`0/false/no/off` 为关、未知值默认开）——
   迁移只做「换来源」，**不引入新类型/新校验**（把「静默回落」变「启动报错」属行为变更，
   留给 Revisit 的独立裁决）；
2. **cookie 名/路径的既有导入语义不变**：`backend/core/security.py` 用**模块级
   `__getattr__`（PEP 562）** 惰性解析 `ACCESS_COOKIE_NAME` / `REFRESH_COOKIE_NAME` /
   `AUTH_COOKIE_PATH`——`from backend.core.security import ACCESS_COOKIE_NAME` 照常可用
   （导入方在自己的 import 时点取值，与迁移前一致），且模块本身不在 import 期读 env（D4）。
   模块**内部**的 6 处使用改为直接读 Settings 字段（`__getattr__` 只覆盖跨模块属性访问）；
3. **凭据与跨域开关不入表（C2）**：`JWT_SECRET_KEY`、`ENV`、`TESTING` 保持裸读
   （前两者分别在 security.py 顶部与 `is_production_like_env`）。
4. 生产 guard `validate_production_auth_cookie_settings()` 与 `cors.get_cors_config()`
   的**校验逻辑与错误文案逐字不变**，只把取值来源换成 Settings（单一来源）。

## Alternatives

- **把 guard 搬成 `model_validator`**：否决（本单）——校验失败形态会从 `RuntimeError`
  变为 `ValidationError`（调用点与测试断言都变），且 `ENV` 不在本域；「校验进模型」
  应作为独立裁决（ADR Revisit 已记）；
- **cookie 名直接改成 `get_auth_session_settings().auth_access_cookie_name` 逐点替换**
  （20 处调用 + 4 个测试文件的 import）：可行但把 diff 扩到 8 个文件，收益仅是「少一层
  `__getattr__`」；PEP 562 代理同样满足「不在 import 期读 env」，故取之；
- **`AUTH_COOKIE_SECURE` 改 bool / `CSRF` 改 bool 字段**：否决——会改变取值边界行为
  （如 `AUTH_COOKIE_SECURE=true` 从「静默关」变「启动报错」）。

## Verification

- `pytest tests/test_settings_auth_session.py`（新增 **7 例**）：默认值逐一对照迁移前 ·
  派生语义（secure 仅 "1"、samesite 原始/归一、csrf 未知值默认开）· env 覆盖需 reset ·
  **模块属性惰性**（含未清缓存读旧值的断言）· `.env` 文件不生效（负向）·
  **生产 guard 四分支**（production 未开 secure / samesite 非法 / csrf 关 / 全合规）·
  **CORS 三态**（默认白名单 / 通配符拒绝 / 空 origins 拒绝）；
- `pytest backend/tests/…（auth 相关 7 文件）` → **70 passed**；
- `pytest backend/tests/`（全量）→ **2681 passed**（9m55s）；
- `pytest tests/`（根）→ **569 passed**（env_inventory 211 名一致）；
- 测试随迁：4 个测试文件的 10 个 env 键打桩改走新 fixture `auth_env`（`.set/.unset`
  自动清缓存；`backend/tests/conftest.py`），未使用的注入参数已清理；
- `check:quick` → **10 gates 全绿**；`ruff`（gate 范围）与 `gov-surface`（S1–S13、S5x）全绿。

## Revisit

- **校验进模型**：把 samesite 合法性/CSRF 开关等做成 `model_validator`，让「静默回落 +
  启动期 guard」变成「加载即报错」——需裁决（失败形态与调用点都会变）；
- **`ENV`/`TESTING` 的归属**：它们仍散落裸读；若将来立「部署模式域」，应考虑统一
  （届时 `is_production_like_env` 一并迁移）；
- **`STP_METRICS_AUTH_REQUIRED`（`api/routes/metrics.py`）**：属「API 访问控制」子域，
  本单未纳入；若立该域应一并迁移；
- P2 其余候选：agent 磁盘/归档、agent 心跳/协调/注册、realtime 多实例、通知与报告
  （按 D2 清单顺序推进）。
