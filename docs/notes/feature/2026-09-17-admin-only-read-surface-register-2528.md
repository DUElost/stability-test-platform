# admin-only 读接口的可达性登记棘轮（#2528）

Status: implemented
Class: feature

## Decision

把 #2360 族缺陷（**页面入口对普通用户可见、其读接口却 `require_admin`** → 满页 403）从
「一次性审计」固化成**结构棘轮**（`tests/test_admin_only_read_surface_register.py`，PR 路径）：

1. 扫 `backend/api/routes/*.py` 的「`@router.get` + `Depends(require_admin)`」（逐行状态机，
   只认函数签名里的 `Depends(require_admin)`，注释与别处提及不算）；
2. 每个命中端点必须在 `_REGISTERED` 里，并写明**四种依据之一**：入口隐藏（导航 `adminOnly`）
   / AdminRoute（页面在门控下）/ 页内收窄（同页按角色分权）/ 前端零调用方；
3. **反向**：登记项若在扫描结果里消失（端点删除/改动词）同样报错——防登记表腐烂；
4. **自证**：合成样本上验证扫描器有判别力（admin-only GET 命中、写操作不命中）。

**为什么是 GET 而不是全部**：写操作（POST/PUT/DELETE）对普通用户 403 是**正当**的权限边界；
只有「读」被拦才会让一个可打开的页面变成满页 403。实测分布也印证：81 处 `require_admin` 里
GET 只占 13。

## Alternatives

- **A. 只写文档记录这次清点**：否决。下一次有人加 admin-only GET 时没人会想起翻文档——
  这正是 #2360 从引入到被发现之间的状态。
- **B. 自动解析前端调用面（端点→页面→是否 admin 可达）**：否决。需要 TS 语义分析且极易
  假红（动态 queryKey、条件调用、HOC），维护成本高于收益；登记表 + 人工一句话依据是同等
  防护下的最省方案（仓内 `UNPRODUCED_METRICS` 已是同一模式）。
- **C. 把 13 个端点全部改成非 admin**：否决。其中多数（users/settings/audit/storage）本就该
  admin-only，且页面已在 AdminRoute 下——要改的是「入口可见性」，不是接口权限。

## Verification

- 扫描器实测命中 **13** 个 admin-only GET（与 2026-09-17 人工清点逐条一致）：`ai_assistant`
  2 / `audit` 1 / `hosts` 1 / `notifications` 2 / `resource_pools` 3 / `settings` 1 /
  `stats` 1 / `users` 2。
- **变异检查**：临时新增 `audit.py:/sneaky-admin-read`（GET + `Depends(require_admin)`）→
  棘轮红并给出可行动提示（`['audit.py:/sneaky-admin-read']`）；移除后 5 passed。
- 自证用例（合成样本）、非空钉子（≥10 个命中）、双向一致、依据非空话 —— 共 5 例全绿。
- `check:quick` 通过（本次为新增测试文件，无业务代码改动）。

## Revisit

- **登记表的维护成本**：每次新增 admin-only GET 都要写一句依据。若这个面增长很快（例如
  新页连出三四个），说明「谁该看什么」该有一次统一裁决，而不是逐个登记。
- **前端侧的对偶**：本棘轮只保证「有人回答过可达性问题」，不保证回答正确（例如声称
  「页内收窄」但实际没做）。若这类缺陷再出现一例，应考虑把登记项与前端的具体守卫
  （`enabled: isAdmin` 字面量或 AdminRoute 路径）做弱关联断言。
- **`hosts.py:/{host_id}/log-signal-dead-letters` 一支**：目前依据是「前端零调用方」。若将来
  主机页要展示死信，需同时决定它对非 admin 的可见性（很可能整块只对 admin 显示）。
