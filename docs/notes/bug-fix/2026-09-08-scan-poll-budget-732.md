# scan_task 轮询预算可配置 + 高进度宽限（#732）

Status: implemented
Class: bug-fix

## Decision

硬编码 `_SCAN_POLL_MAX_WAIT = 300` 使慢 host 略超 300s 时被
`saq_scan_partial_artifacts` 永久排除。

1. `STP_SCAN_POLL_MAX_WAIT`（默认 300）+ `STP_SCAN_POLL_PER_HOST_SECONDS`
   （默认 0）→ `base + n_triggered * per_host`。
2. 主预算耗尽且就绪率 ≥ `STP_SCAN_POLL_GRACE_RATIO`（0.9）且缺口 ≤
   `STP_SCAN_POLL_GRACE_MAX_MISSING`（3）时，追加一次
   `STP_SCAN_POLL_GRACE_SECONDS`（120）宽限。
3. 文档写入 `environment-variables.md`。

## Alternatives

- 仅提高默认 300→600：无规模自适应，运维无法按集群调。
- 超时不进 merge：整轮无报告更糟；保持 partial 链并拉长等待。

## Verification

```bash
TESTING=1 JWT_SECRET_KEY=test-secret python -m pytest \
  backend/agent/tests/test_saq_scan_pipeline.py -q \
  -k 'scan_poll or grace or polls_until'
```

## Revisit

若 SAQ `scan_task` timeout(900) 不够覆盖 max_wait+grace，需同步上调
job timeout。
