# God-module 切片：agent_api 删 re-export，测改 service 导入（#1520）

Status: implemented
Class: bug-fix

## Decision

叠在 #2590（鉴权壳）之上：路由内大段 `noqa: F401` re-export 只为保测试从
路由拿私有符号。本刀：

1. 测试改从真源 `backend.services.agent_*`（及 `auth.verify_agent_secret` /
   `agent_version_gate`）导入；
2. `agent_api.py` 只保留端点签名与调用实际需要的 import。

`agent_api.py` **462 → 391**（相对 #2590 tip；相对 main 515 → 391）。

## Alternatives

- **等 #2590 合入再对 main 开刀**：弃——叠栈更短路径，冲突面明确；
- **只删一半 re-export**：弃——测试面已扫全，一次收口。

## Verification

- secret_guards + fencing + patrol + artifacts + dle + routes（85 passed）；
- dual_write version + shared_row_lock + recovery_sync lock + step5a（36 passed）；
- `ruff` + `check:quick`。

## Revisit

- #1520 三主战场均已薄壳；后续若还有体量，优先注释/形状契约（见 #2606）或棘轮；
- Issue #1520 保持 OPEN；`Refs #1520`。
