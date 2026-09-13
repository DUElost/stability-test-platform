# 脚本版本不可变门禁补 `--self-test` + notes 断链（审计 §七）

Status: implemented
Class: tech-debt / ci-gate

## Decision

1. 将 `check-script-version-immutability.py` 的分类逻辑抽成纯函数
   `classify_change` / `collect_violations`，并增加离线 `--self-test`
   （红：修改/删除/改名/塞入已发布版本；绿：全新版本新增、非版本路径）。
2. CI lint job 在真实 `--base` 检查前先跑 `--self-test`，与分层/治理面门禁同模式
   （PLATFORM_AUDIT_2026-09-11 §七 第二批 #10）。
3. 顺手修 `docs/notes/architecture/` 两篇 note 共 3 处相对链接深度
   （`../` → `../../`，§七 第一批 #5 残留）。

既有 `tests/test_script_version_immutability_gate.py` 仍覆盖真实 git fixture；
`--self-test` 补的是**检查器自身毫秒级回归**（不依赖 tmp repo），避免分类规则
改坏却要等 pytest 才发现。

## Alternatives

- **只依赖 pytest fixture**：否决——lint job 不跑该测试文件；其它门禁已统一
  `--self-test` 进 CI。
- **self-test 内再起临时 git 仓**：过重；分类规则与 git 解耦后纯函数足够。

## Verification

- `python tools/dev/check-script-version-immutability.py --self-test`
- `python -m pytest tests/test_script_version_immutability_gate.py -q`
- `python scripts/run_gates.py check:quick`
- 两篇 note 相对链接 resolve 存在

## Revisit

若日后 `_MUTATING_STATUS` / `#888` 规则再扩，先扩 `--self-test` 样例再改 CI 行为。
