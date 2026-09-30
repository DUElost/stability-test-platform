# 破坏性 Git 的可重复 Bash 差分探针（#3553 / #3516 G1）

Status: proposed
Class: testing

## Decision

新增 `tools/dev/destructive_git_probe.py` 手动 dev 探针，不接默认 CI gate。
从独立生成的参数、七种引号写法与 shell 宿主得到命令；真实 Bash 的临时 mock git
记录 argv，必须精确等于生成器的预期才能用于比对。执行失败/缺事实/argv 不符为
UNVERIFIED，不当 PASS；守卫只负责判断，不能给事实来源代判子命令。

所有实际 git 均为临时 mock，绝对路径案例也指向它；无真实破坏性 Git 命令。
隔离 cwd 与环境，禁用 profile，不读取 BASH_ENV / 凭据 / 生产配置。mock 只记录 argv。
`--checker` 可指定可信的历史版本文件作变异验证；报告保存 checker 路径与 SHA256。

动态宿主的已知漏拦单列 KNOWN_GAP；超深回退对包含禁令字面数据的合法提交消息可能
保守拦截，单列 CONSERVATIVE_BLOCK，不把它描述成零不一致。其他漏拦/误拦/无法执行
使探针非零。小型 `--self-test` 为 36 次真实 Bash 执行，不替代完整矩阵。

## Alternatives

- 只用 checker 的 --self-test：首版全绿仍漏了 --config-env 与深 heredoc。
- 复用 checker 的 Git 参数解析来构建 oracle：会共享同一遗漏；本探针比对完整 argv。
- 执行真实 git/sudo 或读取用户 shell 配置：没有必要，不作为事实来源。
- 每次 CI 跑千余子进程：额外成本不适合日常门禁，手动修改解析器时重跑。

## Verification

- 36 次小矩阵：33 PASS / 3 CONSERVATIVE_BLOCK，无无法执行或受支持宿主漏拦。
- 完整当前/历史 checker 矩阵、探针负向单测与 check:quick pending。

## Revisit

- 此探针不是前一云端会话未入库的 10,329 次脚本，不能沿用其执行数字。
- 未纳入的外部 wrapper、非 Bash 方言与文档已知动态宿主仍不构成覆盖；扩展须保留
  mock-only、干净环境与逐条真实 argv 证据，不能把无法执行降级为 PASS。
- git hooks opt-in、自检与真实 Claude / Cursor / Zcode / CodeBuddy 验收待后续单元，
  不以差分或 CI 全绿替代实际触发。独立复核前保持 draft。
