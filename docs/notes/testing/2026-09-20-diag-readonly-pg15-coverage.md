# diag-readonly 的 PG15 覆盖：实测证伪「自检在 PG≤15 恒红」（#2849）

Status: implemented
Class: testing

## Decision

#2849 的结论是「`deploy/postgres/diag-readonly.sql` §4 自检在 PG≤15 恒红：role-wide
`ALTER ROLE … SET`（不带 `IN DATABASE`）在 PG16 前落 `pg_authid.rolconfig`，而
`pg_db_role_setting` 零行 → NULL 即 RAISE」。**实测证伪**（两代真容器）：

| 事实 | 实测 |
|---|---|
| `pg_authid` 是否有 `rolconfig` 列 | **PG 15.18 上就没有**（列清单里 `rolcanlogin` 与 `rolconnlimit` 之间没有它）——不是 PG16 才删的 |
| role-wide `ALTER ROLE <r> SET …` 落点 | **15 与 16 都落 `pg_db_role_setting`（setdatabase = 0）**；`pg_roles.rolconfig` 只是它的视图 |
| 原版脚本在 PG15 上 | **exit 0**，且打印 `诊断只读角色就绪：stp_ro（…）`（自检四条全过） |

因此本单**不按建议改自检**——那会把一个不存在的缺陷「修」成一个真缺陷（见 Alternatives）。
真正成立的是 issue 的**覆盖**半边：`deploy/postgres/docker-compose.yml` 仍 ship
`postgres:15-alpine`，而 CI 与既有 fixture 只跑 16。故交付物 = **把 PG15 部署面纳入测试**，
并让这两条新用例把上面的事实钉住。

## Alternatives

- **按建议①加 `pg_authid.rolconfig` 分支（`EXECUTE` 动态语句）**：否决。该列在 15 上就
  不存在（实测），`EXECUTE` 只躲过解析期、执行期照样报 `column a.rolconfig does not exist`
  ——把一个不存在的缺陷换成一个必炸的分支。（起草时我一度照此改了 SQL，实测后撤回。）
- **按建议②在写操作前断言 PG≥16**：否决。仓库自带的部署面就是 15（compose 镜像），
  加版本门禁等于让自带路径不可用。
- **什么都不做（认定 issue 为假阳性）**：否决。覆盖缺口是真的——PG15 是 ship 出去的
  部署面，却没有任何用例跑它；这次的假阳性本身也说明「没有 15 的用例」会让人只能靠
  推演下结论。

## Verification

- 两代真容器的目录事实（本文的判据来源，命令可复跑）：
  - PG15：`SELECT column_name FROM information_schema.columns WHERE table_name='pg_authid'`
    → 无 `rolconfig`；`ALTER ROLE stp_ro SET default_transaction_read_only=on` 后
    `SELECT setrole::regrole, setdatabase, setconfig FROM pg_db_role_setting` →
    `stp_ro | 0 | {default_transaction_read_only=on}`；
  - 原版脚本经 `psql -v ON_ERROR_STOP=1 -f` 在 PG15 上执行 → **exit 0** + §4 NOTICE。
- 新增两条用例（`tests/test_diag_readonly_role_pg.py`）：PG15 上脚本跑完 + 只读闸落点
  与 16 同形、读通写拒；fixture 形状与既有 PG16 用例一致（表由应用属主建、脚本由超级用户跑）。
- 实测：`pytest tests/test_diag_readonly_role_pg.py -q` → **8 passed**（6 条既有 PG16 + 2 条新 PG15）。

## Revisit

- **issue 的结论需要更正**：#2849 正文与「人工核证成立」的表述与实测不符（差异点是
  catalog 语义）。证据已写进本 Note；建议由提单方在 issue 上附实测输出后重新定级或关闭。
- **PG≤14**：本实验只覆盖 15/16。若将来出现 14 及更早的部署面，按同一命令重做实验再定。
- **同类形态**：本单的教训与 #2639/「计数只能派生」同源——**审计结论也要有可复跑的判据**；
  凡是「某版本上恒红」这类断言，附一条真容器/真环境的复现命令比推演有价值。
