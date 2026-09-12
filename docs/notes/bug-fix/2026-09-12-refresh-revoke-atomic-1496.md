# #1496 refresh 原子消费未前提化：revoke 冲突仍签发

Status: implemented
Class: bug-fix

## Decision

`/auth/refresh` 在 rotation 路径必须以 `revoke(..., reason="rotation")` 的**首次插入成功**为签发前提：返回 `False`（jti 已被他路消费）时记 `refresh_rejected` / `jti_consume_conflict` 并 401，不再 `_issue_token_pair`。

顺序重放仍由既有 `is_revoked` 短路覆盖；本修补的是**两路都越过 is_revoked 之后**的冲突窗口（85d793 F01 / #901 残余）。

## Alternatives

- **仅依赖客户端 Web Locks（#1039）**——放弃：不能替代服务端正确性。
- **去掉前置 `is_revoked`、只信 revoke**——未选：保留短路减少无谓插入与审计噪声；正确性仍由 revoke 返回值兜底。

## Verification

- 新增：`test_refresh_rejects_when_revoke_returns_false`（反事实：去掉 `if not consumed` 则该用例必红）
- 新增：`test_refresh_logout_race_loser_rejected`、`test_concurrent_refresh_only_first_consumer_issues`
- 既有顺序重放 / logout→refresh 用例保持

## Revisit

无。客户端锁与 `/auth/me` 探活边界仍见 #1039 / #1200。
