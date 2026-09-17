# God-module 垂直切片：agent_api 鉴权/version 壳收口（#1520）

Status: implemented
Class: bug-fix

## Decision

`plan_runs` / projects 仍有在飞 PR；本刀收口 `agent_api` 残余薄壳：

1. `_verify_agent` → 复用 `auth.verify_agent_secret`（re-export 保测试导入）；
2. 删除死代码 `_version_tuple`（路由内无人调用；真源在 `agent_version_gate`）；
3. `_agent_version_is_supported` → 直接 re-export `agent_version_is_supported`；
4. 去掉 `/steps` 未使用的重复 `X-Agent-Secret` Header 形参与空注释块。

`agent_api.py` **515 → 465**。

## Alternatives

- **新建 `services/agent_auth.py`**：弃——`auth.verify_agent_secret` 已是同语义真源；
- **顺手清 re-export 改测导入**：弃——面大、与本刀鉴权无关；
- **统一 401 detail 大小写前先改客户端契约**：本刀已随 auth 路径变为
  `Invalid agent secret`（原路由为小写）；既有断言不依赖该字面量。

## Verification

- `test_agent_secret_guards` + `test_agent_api_artifacts` + `test_agent_routes`
  （48 passed）；
- dual_write version 断言；
- `ruff` + `check:quick`。

## Revisit

- 下一刀若继续瘦 `agent_api`：把测试改从 service 导入、删 `noqa: F401` re-export；
- Issue #1520 保持 OPEN；`Refs #1520`。
