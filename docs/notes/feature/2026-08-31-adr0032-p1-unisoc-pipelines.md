# ADR-0032 P1 编码落地（Watcher + 归档）

Status: accepted
Class: feature

## 决定了什么

按 [`2026-08-31-unisoc-toolkit-73-463-alignment.md`](../architecture/2026-08-31-unisoc-toolkit-73-463-alignment.md) 顺序落地 PR-A–E（D3 env、UNISOC Reconciler/ScanRunner、B1 路径分区、双 merge、`platform_buckets` UI）。

## 验证

```bash
venv/bin/python -m pytest backend/tests/services/test_agent_env_sync.py \
  backend/tests/core/test_dedup_platform.py \
  backend/agent/tests/test_platform_collector.py \
  backend/agent/tests/test_job_session.py -q
cd frontend && npm run type-check
```

真机 Z258：Watcher UNIVIEW 计数 + `dedup/{run}/unisoc/` merge — 待 toolkit 联调。
