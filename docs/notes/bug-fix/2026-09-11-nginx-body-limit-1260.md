# 反代 body 上限对齐 Suite 上传预算（#1260 / R14-F14）

Status: implemented
Class: bug-fix

## Decision

本质问题：三个 Nginx 模板（http / https / preview）均未设
`client_max_body_size`（默认 1 MiB），而 Suite 上传端点一次可收 `file` +
`global` 两个文件、单文件上限 10 MiB（`backend/api/routes/suites.py:_MAX_UPLOAD_BYTES`）
——合法大上传在反代即 413，后端 `FILE_TOO_LARGE` 根本到不了。

修复 = 三个模板统一加：

```nginx
client_max_body_size 25m;   # 2 × 10 MiB 单文件预算 + multipart 余量
```

并加**双层门禁**：

- `tools/verify_control_plane_templates.py`：断言三个模板含该指令（模板不变量，
  与部署根等检查同源维护）；
- `backend/tests/test_deployment_files.py`：解析 `suites.py` 的
  `_MAX_UPLOAD_BYTES` 与模板值，断言 `body_limit ≥ 2 × per_file`
  ——任一侧变化即联动报警（验收第 3 条）。

超限行为（验收第 2 条）：>25m 请求体在 Nginx 直接 413（不经后端）；单文件
>10 MiB 由后端 413 `FILE_TOO_LARGE` 兜底——两层语义写入模板注释与被测断言。

## Alternatives

- **只写模板值、不加联动测试**——放弃：验收第 3 条要求"模板与后端常量一致"；
  纯字面量断言会随后端常量漂移静默过期；
- **放 `location /api/` 级而非 server 级**——放弃：server 级覆盖全部 API 路径
  且与三个模板结构一致；静态资源不受影响（GET 无 body）；
- **贴边值 21m**——放弃：multipart 边界/头部开销 + 未来单文件上限小调都需要
  余量；25m 仍保持"反代拒绝超大请求体"的防护目的。

## Verification

实际运行（worktree `/tmp/stp-1260`，基于 `origin/main`）：

- `python tools/verify_control_plane_templates.py` → `OK: control-plane templates look consistent`；
- `pytest backend/tests/test_deployment_files.py -q` → **14 passed**（新增 1 例
  跨源联动测试）；
- **反向验证**：移除 https 模板的 `client_max_body_size` → verify FAILED +
  联动测试 1 failed；恢复后 OK + 14 passed；
- `check:quick` → 7 gates 全绿。

未完成（pending）：

- 经真实 Nginx 反代的 ≤10 MiB 上传端到端验证（验收第 1 条环境侧）：本机为生产
  控制面宿主，不在生产 Nginx 上构造上传；需隔离 / 预发布环境验证（`curl -F`
  带 10 MiB 文件经反代至后端 201）。

## Revisit

- 若上传端点增加文件数或调整单文件上限，同步 `client_max_body_size` 与联动
  测试阈值（测试会先红提示）；
- 若生产存在 http 级全局 `client_max_body_size`，server 级更具体值就近生效
  （本修复仍正确）。
