# B1-G3：fill_storage / clean_env 把 plan 参数插进设备端 root shell 的白名单收口（2026-09-27）

Status: implemented
Class: bug-fix

## Decision

按 #3463 §3 G3 执行，判据沿用 #3107（「宁可红不把破坏性命令执行面交给参数」），逐族同构 port 参照实现，不自行改写：

- **fill_storage v1.1.2（#3172）**：`fill_path` 此前被原样插进 root shell 的三个面
  （`rm -f {fill_path}` / `dd of={fill_path}` / `du -sk {fill_path}`），`/data/local/tmp/*`
  清目录、`; ` 拼命令、空格静默重定向；且 `args.get("fill_path", default)` 在键存在但为空串时
  不回退（`rm -f` 裸奔）。port `clear_recents.validated_dump_path` 形态：白名单
  `^/data/local/tmp/[A-Za-z0-9._-]{1,64}$` + 尾段非 `.`/`..`，空值显式回退默认路径，
  非法值在任何 adb 命令下发前整步转红（参照调用点：`main()` try/except ValueError →
  `output_result(False)`；参照实现 clear_recents/v1.0.5）。
- **clean_env v1.1.1（#3463 §8.2 新发现、无 issue 跟踪）**：`log_dirs` 与 monkey_setup v2.3.10
  完全同形（`[""]` ⇒ `rm -rf /*`），port `validated_log_dirs`（`^/(?:data|sdcard)/[A-Za-z0-9._/-]{1,120}$`
  + `..` 段拒绝；参照实现 monkey_setup/v2.3.11，默认目录与错误语义原样）；`pm uninstall` 包名与
  `setprop` 键按 `^[A-Za-z0-9._-]+$` 白名单，`setprop` 值过 `shlex.quote`；非法项记入 `errors`
  整步转红且不下发任何含未校验值的命令（沿用族内既有 errors 聚合出口，不静默跳过）。
- **F2（两族 `_adb.py`）**：port `gpu_setup._lib.decode_device_output`（#3069）——四个 adb helper
  （`adb_shell`/`adb_shell_quiet`/`adb_push`/`adb_install`）去掉 `text=True` 严格解码，改字节采集 +
  宽容解码（坏字节 → U+FFFD）。签名与返回类型不变（§6：本批不动 helper 签名）。

版本登记：`check_script_packages.py --register fill_storage 1.1.2`（sha 436467ab53bb）、
`--register clean_env 1.1.1`（sha 9170856f2294），均按给定号成功，无占用顺延。

## Alternatives

- **值侧统一 shlex.quote 而不做白名单**：对 `du`/`dd of=` 的读面可用，但 `rm -rf {d}/*` 的删除面
  quote 后通配仍是参数可控（`'*'` 引掉后不展开，但 `rm -rf` 目标本身仍由参数决定）——#3107 已裁决
  用白名单，拒绝。
- **在 `params()`/校验中间层做通用参数守卫**：属 #3463 §2 模型级问题（helper 副本无法传播），
  Owner 未裁决前不预设，按现行逐族 port（§2 C）。
- **把 clean_env 的非法项静默跳过**：「清理没做」会以 success 结束（假绿），参照实现已排除。

## Verification

- `python tools/dev/check_script_packages.py` → OK（35 族树与最新登记等价）
- `python tools/dev/check_tool_manifest.py --base origin/main` → OK（相对 origin/main append-only）
- `python -m pytest backend/agent/tests/ -q -k "fill_storage"` → 36 passed
- `python -m pytest backend/agent/tests/ -q -k "clean_env"` → 44 passed
  （含新增反例：§4 G3 全清单——`""`/`/data/local/tmp/*`/`" ; rm -rf /system ; "`/带空格路径/`..`/
  超长对两族判据逐项判红，默认值与合法值判绿；非法值步骤断言 **零命令下发**；`""` fill_path 断言
  显式回退默认路径进 `du`/`dd` 命令；F2 用假 adb 二进制喂 `0xf9` 坏字节自证严格解码抛、宽容解码
  不抛，并静态守卫两族 `_adb.py` 无调用形态 `text=True`）
- `python scripts/run_gates.py check:quick` → OK（16 gates；worktree 借 symlink node_modules 跑 eslint，
  与 G1/G2 同法）

## Revisit

- 生效属 #3463 §5 整批一次（deploy + publish + scan + 重指 plan_step；fill_storage/clean_env 引用面
  由 Owner 定窗），本 PR 不激活。
- §1 F3 同形态扫描中其余族的参数插值（约 20 族 F2 残余、其他族 `chmod`/`fill_path` 面）按 #3463
  §6/§8 不入本批；等 §2 裁决（A. 同名 helper 字节一致守卫 / B. 共享运行时库）后统一收口，
  否则每修一族都要人工 port N 次。
