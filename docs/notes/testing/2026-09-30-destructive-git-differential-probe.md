# 破坏性 Git 的可重复 Bash 差分探针（#3553 / #3516 G1）

Status: proposed
Class: testing

## Decision

新增 `tools/dev/destructive_git_probe.py` 手动 dev 探针，不接默认 CI gate。
从独立生成的参数、七种引号写法与 shell 宿主得到命令；真实 Bash 的临时 mock git
记录 argv，必须精确等于生成器的预期才能用于比对。执行失败/缺事实/argv 不符为
UNVERIFIED，不当 PASS；守卫只负责判断，不能给事实来源代判子命令。

完整探针发现已合入守卫仍漏掉反引号的双层转义：原始内容只替换了转义反引号，
没有先解码 Bash backquote 的第一层反斜杠，再送入内部 shell 解析器。本单共用
`_backtick_text` 修复普通 / 双引号 / heredoc 三个调用面，不增加新的禁止语义。
语义依据见 [GNU Bash command substitution](https://www.gnu.org/s/bash/manual/html_node/Command-Substitution.html)，
行续接再以真实 Bash argv 验证。没有执行输入内容的解析代码。

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
- 初始 6726 次：已合入版本 44 MISS；e1d8b62 为 586 MISS；均无 UNVERIFIED。
  扩展普通/双引号/heredoc 反引号后，修复前的已合入版本 7174 次报告 132 MISS。
- 修复后相关单测与原守卫回归 73 passed；把共用解码恢复到旧语义为 9 failed / 64 passed。
  首次变异调用误用了系统 Python（无 pytest），不计验证；以上结果为项目解释器实跑。
- 真实 settings.json command：相同反例在已合入脚本返回 0，本单脚本返回 2；这里只
  调用 hook 的 JSON 检查，没有执行输入中的 Git 命令。真实 Claude 会话触发仍 pending。
- 修复后 7174 次矩阵：7154 PASS / 14 CONSERVATIVE_BLOCK / 6 KNOWN_GAP，
  其余 MISS / FALSE_POSITIVE / UNVERIFIED 为 0；修复后 quick 重跑 16 项通过。
  schema-at-head 因无 DATABASE_URL 明确跳过，未作数据库验收。
- 完整矩阵期间不得同时做 checker 变异；一次与变异时间重叠的运行已取消，不计证据。
  探针记录启动时的源码 hash，结束时源码变化则 UNVERIFIED，避免错贴版本。

## Revisit

- 此探针不是前一云端会话未入库的 10,329 次脚本，不能沿用其执行数字。
- 未纳入的外部 wrapper、非 Bash 方言与文档已知动态宿主仍不构成覆盖；扩展须保留
  mock-only、干净环境与逐条真实 argv 证据，不能把无法执行降级为 PASS。
- git hooks opt-in、自检与真实 Claude / Cursor / Zcode / CodeBuddy 验收待后续单元，
  不以差分或 CI 全绿替代实际触发。独立复核前保持 draft。
