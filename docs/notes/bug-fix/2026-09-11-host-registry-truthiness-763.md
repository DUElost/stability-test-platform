# auto_register_host 4xx 日志丢状态码（#763 / #729 同型残留）

Status: implemented
Class: bug-fix

## Decision

`backend/agent/host_registry.py` 的 HTTPError 分支沿用 #729 同款真值脚枪：

```python
status_code = exc.response.status_code if exc.response else None
body = exc.response.text[:500] if exc.response else None
```

`requests.Response.__bool__` 是 `ok` 别名 → 4xx/5xx 恒假 → 自动注册失败日志记
`status=None, body=None`，故障定位无法区分「服务端拒绝（400/401/403）」与
「网络层异常」；异常随后 re-raise，流程不受影响（**仅日志降级**）。

修复：改为 `is not None`（与 `outbox_drainer.py` #733 同改）；并全仓扫描确认
agent 侧同类站点（`api_client` / `pipeline_engine` / `step_trace_uploader`）
此前已用辅助函数/is not None，host_registry 是**唯一残留**——扫描结果为空。

## Alternatives

- **保持原样（仅日志降级）**：日志是本类故障唯一可观测面，`status=None` 会让
  定位从「看状态码」退化为「猜」——修复成本 2 行，没有理由保留；
- **抽公共 helper（`_status_code(exc)`）统一各站点**：各站点已各自正确，抽公共
  函数属顺手重构（超出本单），Revisit 中登记。

## Verification

实际运行：

- 新增 `backend/agent/tests/test_host_registry_truthiness.py` → **2 passed**
  （真实 falsy 4xx Response：日志含 `status=400` 与 body、绝无 `status=None`；
  无 response 的网络异常走 exception 分支）；
- 全仓扫描：`if x.response`（无 `is not None`）/`.response.status_code if
  x.response else` 形态在 `backend/agent` **已清零**（唯一命中为本测试 docstring）；
- `backend/agent/tests` 全量 → **1615 passed**；
- `ruff check`（2 文件）→ All checks passed；
- `python scripts/run_gates.py check:quick` → 7 gates 全绿。

未完成（pending）：

- 无（log-only 修复，无真机依赖）。

## Revisit

- 若后续出现第三个同类站点，把 `status_code/body` 提取抽成
  `watcher/contracts` 之外的共享 helper（当前仅两处用同款写法，暂不抽象）；
- 本单只修日志形态，自动注册失败后的重试/告警策略不在范围内（#729/#302 域）。
