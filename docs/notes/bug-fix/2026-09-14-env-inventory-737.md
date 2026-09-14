# 环境变量完整读取清单（自动生成 + 漂移门禁，#737 文档切片）

Status: implemented
Class: bug-fix

## Decision

#737 的文档面收口：把「代码到底读了什么环境变量」从黑盒变成**可验证清单**。

- **工具** `tools/dev/env_inventory.py`：扫描 `backend/**/*.py`（不含
  `backend/agent/scripts/**`——版本化脚本目录自管环境契约，ADR-0020）的读取形态，
  渲染确定性表格写入 `docs/development/environment-variables.md` 文末生成块；
  - `--write` 刷新生成块（幂等）；`--check` 漂移即红；`--self-test` 离线自证；
  - 当前清单：**210 个读取名，其中 109 个未在 `.env*.example` 登记**（示例是
    **运维模板**，只承载需要运维改动的子集；附录才是代码侧完整清单）；
- **门禁** `run_gates.py` 新增 `env-inventory`（命令内含 `--self-test && --check`），
  已接入 `check:quick` 与 `check:pr`；
- **口径更新**：文档头部原写「完整清单以 example 文件为准」——现改为
  「运维模板权威源 = 示例；代码侧完整读取清单 = 文末附录（自动生成 + 门禁）」。

### 覆盖形态与两个踩过的坑（复核发现，已修）

扫描形态按**每文件导入语句**动态派生：`os.getenv` / `os.environ.get` /
`os.environ["X"]` / `import os as X` 的 `X.getenv(...)` /
`from os import getenv [as _g]` 的裸调用 / `from os import environ [as _e]` /
项目 helper（`_int_env("X", production_default=300)` 等多行形态）。

1. **只认字面 `os.getenv` 会漏别名**：首版正则用 `os\.getenv\(`，变异验证
   （注入 `import os as _probe` + `_probe.getenv(...)`）**没红** → 改为按导入派生；
2. **放宽容收会造成误报**：改成「任意 `X.getenv(` + 裸 `environ.get(`」后，
   `backend/realtime/socketio_server.py` 的 **WSGI environ**（`environ.get("HTTP_ORIGIN")`）
   被当成环境变量混入清单 → 改为只认 `os` 及其**导入别名**，WSGI 形态不再命中。

## Alternatives

- **把 109 个未登记名全部塞进示例文件**：否决——示例是运维模板，塞入内部旋钮
  （如 `FAKE_TAR_SLEEP`、测试专用键）会稀释模板的信噪比；
- **纯手工补文档**：否决——#737 的成因就是「靠人眼维护」；210 行清单必须有生成器
  与漂移门禁，否则下次照旧腐化；
- **放宽正则覆盖任意 `*.getenv` / 裸 `environ`**：否决——误报 WSGI environ（实测），
  且门禁误报比漏报更伤（会逼人加豁免）；
- **把清单放进 `docs/DOC-MAP.md` 或单独成文**：否决——环境变量参考是该清单的天然
  归属，读者入口唯一。

## Verification

- `pytest tests/test_env_inventory.py tests/test_env_example_parity.py` → **6 passed**
  （含：别名/裸调用形态覆盖、脚本目录跳过、仓库文档与代码同步）；
- **漂移门禁变异矩阵**（对 `--check` 逐例测退出码）：
  | 注入 | 期望 | 实测 |
  |---|---|---|
  | `import os as _probe; _probe.getenv("ZZ_…")` | 红 | exit=1 ✅ |
  | `from os import getenv as _g; _g("ZZ_…")` | 红 | exit=1 ✅ |
  | WSGI `environ.get("HTTP_FAKE_HEADER")` | 绿（不误报） | exit=0 ✅ |
  | `--write` 吸收合法新读取名后 | 绿 | exit=0（211 名）✅ |
  | 清理恢复 | 绿 | exit=0（210 名）✅ |
- `python tools/dev/env_inventory.py --self-test` → OK；
- `pytest tests/` → **326 passed**（含新增 `test_env_inventory.py` 5 例）；
- `run_gates.py check:quick` → **10 gates 全绿**，其中新 `env-inventory` gate 实跑
  `--self-test && --check`（输出「环境变量清单一致（210 个读取名）」）；
- `ruff check tools/dev/env_inventory.py tests/test_env_inventory.py` → All checks passed；
- 治理面 S5x：新门禁已在 `GATE_TO_CI_ANCHOR` 登记 CI 锚点
  （`ci.yml` / 「Run repo-level tests」——由 `tests/test_env_inventory.py` 的同款
  `--check` 承担），`check_governance_surface.py --check` 全绿。

## Revisit

- **生成块之外的人工整理**：附录列出名字/默认/首个读取点/示例登记状态；109 个未登记
  名中，若出现**需要运维调参**的项，应迁入示例并写用途（本单只保证「不再黑盒」）；
- **动态读取形态**：目前覆盖静态字面量；f-string 拼接、字典驱动的键名（如
  `os.getenv(name)`）不在清单内——工具头部已声明；若出现这类用法，需要独立的
  静态可达性分析或运行时采样（届时另立单）；
- **`backend/agent/scripts/**` 的 11 个读取名**（含 `STP_JOB_ID`、`STP_STEP_PARAMS`
  等注入型）目前排除在外；若脚本侧也要清单化，应挂到 ADR-0020 的脚本目录契约下。
