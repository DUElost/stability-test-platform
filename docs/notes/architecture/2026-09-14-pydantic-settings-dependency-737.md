# 引入 pydantic-settings 依赖（ADR-0042 落地第一步，#737）

Status: implemented
Class: architecture

## Decision

按 [ADR-0042](../../adr/ADR-0042-settings-convergence-and-bare-read-boundary.md) v1.0 裁决，
在 `backend/requirements.txt` 引入 **`pydantic-settings>=2.10,<3.0`**（锁定 2.15.0），
并同步重生成两份 lock：

- `backend/requirements.lock`：新增 `pydantic-settings==2.15.0`（+ digest 行），**无其它 pin 变动**；
- `backend/requirements-dev.lock`：同上（dev 经 `-r requirements.txt` 继承）。

**不做的事**（本轮边界）：不建任何 Settings 类、不改任何读取点——本轮只把依赖与
lock 就位，供 D6 门禁扩展与 P1 试点使用。ADR 的两条硬约束（`env_file=None`、
D6 前置）写在源文件注释里，作为落实时的现场提醒。

### 供应链口径（新增依赖的最小核对）

| 项 | 结论 |
|---|---|
| 维护者 | pydantic 核心团队（Samuel Colvin / Eric Jolibois / Hasan Ramezani），与主依赖 `pydantic` 同源 |
| 许可证 | MIT |
| 依赖树 | `pydantic>=2.7`（已有 2.13.x）、`python-dotenv>=0.21`（已在 lock）、`typing-inspection>=0.4`（已在 lock）——引入后 **净新增包数 = 1** |
| extras | aws/azure/gcp/toml/yaml 均为可选 extras，本仓不启用（`--strip-extras`，未请求 extras） |
| requires_python | >=3.10（本仓解析面 py3.11，兼容） |

## Alternatives

- **不引入、改用薄封装 accessor（ADR 方案 A）**：v1.0 裁决已否决（校验/字段文档拿不到）；
- **引入但只进 dev lock、不进生产**：否决——P1 试点的域（scheduler/agent）运行在生产镜像内，
  只进 dev 会让生产镜像缺依赖；
- **顺手加 `pydantic-settings[all]` 或预置 extras**：否决——本仓 `env_file=None`
  且不接任何 secrets manager，extras 全是无谓攻击面。

## Verification

- `python -m pytest tests/test_requirements_lock.py tests/test_requirements_dev_lock.py` → **34 passed**
  （source/lock 同步与 digest 校验）；
- `uv pip install --dry-run --require-hashes -r backend/requirements.lock` → **exit=0**
  （hash 可解析、依赖可满足；计划中含 `pydantic-settings==2.15.0`）；
- lock diff 审查：两份 lock 各 `+8 -1` 行（1 个新包 + digest 行），无既有 pin 漂移；
- 生成方式：`PATH=.venv/bin:$PATH bash scripts/ci/regenerate-lock.sh <req> <lock>`
  （仓库官方脚本；未加 `--upgrade`，沿用既有 pin）。

## Revisit

- **P1 试点**落地后：在 ADR 版本记录回填实际使用结论（含 agent 侧 reload 与
  「Settings 不读 `.env`」负向用例结果）；
- **D6 完成后**：`env_inventory` 能解析 Settings 字段，届时确认锁定的 `pydantic-settings`
  版本行为（`validation_alias` 取值优先级）与扫描口径一致；
- 若上游发布 v3：复评约束区间与 `SettingsConfigDict` 写法（本仓 AGENTS.md 要求 Pydantic v2 API）。
