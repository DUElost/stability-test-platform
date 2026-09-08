# R04-F03 落地：PUT /hosts 只写提交字段，保留密钥认证配置（#950）

Status: implemented
Class: bug-fix

## Decision

`update_host` 复用 `HostCreate` 并逐字段无条件写回：未提交字段落到 schema
缺省值（`ssh_auth_type="password"`、密钥路径 `None`）。编辑表单（AddHostModal）
提交体只含 name/ip/ssh_port/ssh_user（+ 可选空密码省略）——密钥认证主机仅改
名称即被重置为 password，密钥路径与专用 known-hosts 清空（密码有「未提交则
保留」逻辑，密钥字段没有）。

修复（`backend/api/schemas/host.py` + `hosts.py`）：

1. 新增 `HostUpdate`（全字段 Optional，无缺省值污染）并导出；
2. `update_host` 以 `payload.model_fields_set` 驱动条件写——只写请求显式
   提交的字段；`ssh_password` 保持「提交且非空才更新」语义（密码不可读回，
   编辑永不含旧值）；
3. 身份冲突检查改用提交后目标值（未提交字段取宿主当前值）；host-key
   keyscan 触发条件改为「更新后 ip/port 与旧值比较」（原比较 payload 值，
   若 ip 未提交而端口变化会漏扫——一并修正）。

PUT 的既有全量消费方（全字段提交）行为不变；前端编辑表单本就省略认证字段，
无需改动（HostMutationInput 已全 optional）。

## Alternatives

- **改前端编辑表单提交全量字段**——放弃：密钥路径等字段输出侧有意隐藏
  （HostOut 不返回 ssh_key_path），UI 无回填来源；后端 schema 语义
  （PUT=提交字段集）才是正解；
- **update 端点改 PATCH 只接收 diff**——放弃：前端 `hosts.update` 走 PUT，
  改方法面波及调用方；`HostUpdate` + `model_fields_set` 在 PUT 内实现
  diff 语义，向后兼容；
- **保留 HostCreate、仅把三个密钥字段设为不重置**——放弃：缺省默认值的
  存在本身就允许「显式提交默认值」与「未提交」混淆；独立 schema 让意图
  显式（显式传 `ssh_key_path: null` 仍可清空）。

## Verification

- **行为反例实证**：回退 `hosts.py` 保留新测试 → 3 用例全红（认证配置被
  重置为 password）；修复版全绿；
- 新增用例（`test_hosts.py::TestUpdateHostPreserveSsh` 3 例）：仅改名称 →
  key 认证/密钥路径/known_hosts 保留（DB 层断言）；显式切换认证方式仍
  生效且未提交字段保留；IP 变化仍触发 keyscan；
- `backend/tests/api/test_hosts.py` 全套 **31 passed**；
- `check:quick` 与 PR 门禁：见 PR 描述。

## Revisit

- HostOut 有意不返回 `ssh_key_path`（安全），UI 无法展示当前密钥路径——
  仅改名称等操作现在安全了；若未来要「查看已配置密钥路径」，需单独的
  只读呈现设计（不属本单）；
- 其它 PUT 端点若仍复用 Create schema 做更新（如 Device/Suite 系），存在
  同类「未提交重置」风险——已单列的 R04/R05 在案 issue 处理各自域。
