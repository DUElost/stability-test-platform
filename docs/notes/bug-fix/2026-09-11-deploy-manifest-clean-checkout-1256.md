# 最小部署清单不能从干净 checkout 直接复现（#1256）

Status: implemented
Class: bug-fix

## Decision

三处「清单写的」与「仓库里跑的」不一致被收敛成各自唯一口径：

1. **部署根唯一（`<deploy-root>` 占位符）**：`deploy/control-plane/{systemd,nginx,logrotate}`
   全部模板不再硬编码 `/home/debian13/stability-test-platform`，改为 `<deploy-root>`
   （与既有 `<deploy-user>` 同族）。清单 §3.2 定义一次 `STP_DEPLOY_ROOT` +
   `render_template()`，以下所有路径都由它派生；演练 runbook 用既有 `$CONTROL_DIR`
   代入同一函数。干净机示例根 `/opt/stability-test-platform`，现有生产控制面主机仍可
   渲染成 `/home/debian13/stability-test-platform`——换根只改一个变量，模板不再与清单分叉。
2. **internal env 模板进版本控制**：`deploy/control-plane/env/.env.backend.internal.example`
   此前只以未跟踪文件形式存在于生产主机（`.env*` 忽略），清单却直接引用它——干净
   checkout 必然缺文件。现纳入版本控制并脱敏（示例 IP 泛化），`.gitignore` 对该专用
   模板文件显式放行；运行期真实 `.env.backend.internal` 仍被忽略。
3. **构建产物与 Nginx root 对齐**：Nginx root 是 `frontend/dist-prod`，而 `npm run build`
   只产出 `dist/`（全仓无任何脚本产出 dist-prod）。新增
   `npm run build:prod`（`--outDir dist-prod`）与 `npm run build:preview`
   （`--outDir dist-preview`），清单/runbook 改用它；生产换包仍走干净 worktree +
   同盘双 rename 原子切换（SKILL §1.5），不鼓励在部署根内就地构建。

防漂移：「现有模板检查只核对字符串」，看不见上述分叉。`tools/verify_control_plane_templates.py`
（preflight 自检会调用）新增**语义**不变量——全部控制面模板必须含 `<deploy-root>` 且不得
出现 `/opt/`、`/home/` 硬编码部署根；`backend/tests/test_deployment_files.py` 固定该
verifier，并断言每个 Nginx `root` 的 `frontend/dist-*` 都有 `--outDir` 构建脚本、清单与
runbook 不得原样 `cp` 控制面模板（否则占位符会落进 `/etc`）。

## Alternatives

- **把清单里的 `/opt/stability-test-platform` 全改成 `/home/debian13/stability-test-platform`**：
  与现有生产主机一致，但那是把某个用户的 home 路径写进「干净机部署」文档，干净机
  仍不可复现；且换机必再分叉。弃用。
- **把模板固定成 `/opt/stability-test-platform`（与清单示例一致）**：
  会把现有生产 unit 指向不存在目录（`control-plane-deploy` SKILL §1 已实测校准
  `/opt/...` 在本机不存在），等于用文档压事实。弃用。
- **只改文档措辞，模板保留硬编码**：清单与模板仍是两套口径，下一台机器还会分叉；
  且无法被门禁拦住。弃用。
- **保留 `npm run build` + 文档手写 `cp -a dist dist-prod`**：产物目录与 Nginx root
  仍靠人工两步维持，漏一步就是 404。弃用（改由构建脚本承担）。
- **让 Nginx root 改指 `frontend/dist`**：会与现有生产站点的 `dist-prod` 原子切换
  语义冲突，破坏 SKILL §1.5 的回滚路径。弃用。

## Verification

```bash
# 模板语义不变量（占位符 + 无硬编码部署根）；清单 §3.6 的 preflight 自检同样调用它
/home/debian13/stability-test-platform/.venv/bin/python tools/verify_control_plane_templates.py
# → OK: control-plane templates look consistent

# 新增/受影响测试（含 verifier、Nginx root ↔ 构建脚本、env 模板未忽略）
/home/debian13/stability-test-platform/.venv/bin/python -m pytest \
  backend/tests/test_deployment_files.py tests/test_prepare_env.py -q
# → 18 passed

# 反样例自证（守卫非空转）：把真实模板的 <deploy-root> 反向 sed 成 /home/debian13/...
# 后跑 verifier —— 必须 FAIL，实际报
# "Missing '<deploy-root>' in .../stability-backend.service"（exit 1）

# 渲染自证：按清单 §3.2 的 render_template 渲染全部 7 个控制面模板，
# 残留占位符计数均为 0（systemd 3 + nginx 3 + logrotate 1）

# 忽略语义：模板不被忽略、运行期 env 仍被忽略
git check-ignore -q deploy/control-plane/env/.env.backend.internal.example; echo $?  # 1
git check-ignore -q deploy/control-plane/env/.env.backend.internal;          echo $?  # 0

# 门禁
/home/debian13/stability-test-platform/.venv/bin/python scripts/run_gates.py check:quick
# → [OK] check:quick (7 gates)：ruff / eslint / tsc / knip / compileall / gov-surface / ai-work
```

## Revisit

- `deploy/control-plane/env/.env.backend.example` 内注释示例仍写 `/opt/...`（已跟踪文件、
  纯注释，未随本次改写）；若将来要求 env 模板也参与渲染，需把注释一并参数化。
- 历史 Agent Note / 预览说明里的 `npm run build && cp -a dist dist-preview` 写法保留为
  当时记录，未回填 `build:preview`；新文档一律用构建脚本。
- 本机 `/etc/systemd/system/stability-backend.service` 等**已安装** unit 仍是旧路径渲染结果：
  路径等价、无需立即重装；下次部署按清单 §3.5 重新 `render_template` 时自然对齐。
