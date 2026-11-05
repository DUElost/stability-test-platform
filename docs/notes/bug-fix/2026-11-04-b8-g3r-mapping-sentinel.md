# B8 G3r：伴随映射过滤工具未知设备号并隔离平台失败

Status: implemented
Class: bug-fix

## Decision

[#3879](https://github.com/DUElost/stability-test-platform/issues/3879) 修正 G3 伴随映射：
把扫描工具的「设备号未知」哨兵值当成越界串号，导致整份报表被拒。

- 在 `backend/agent/aee/scan_scope.py` 集中定义
  `UNKNOWN_DEVICE_ID_VALUES = {none, undefined, unknown_device_id, discard_device_id}`，
  并新增 `normalize_device_id()`：去空白后按小写匹配，命中哨兵或空串即返回 `None`。
  该集合与工具 v2.1.0 的 `BaseScan.py:245`、`modules/dedup/membership.py:106` 保持一致。
- `report_mapping()` 对 Detail 的捕获值以及 `serial` / `device_id` 列值统一走
  `normalize_device_id()`；哨兵值不进入 `row_serials`。路径推断和 sources 的串号
  仍是可信证据，不过滤。
- 两条硬失败保留并显式分类：过滤后无证据的行抛 `unprovable`，真实值越界抛
  `outside_run`（#3720 语义不变）。报表级失败分类为 `missing_path` / `no_rows`。
  异常类型 `ScanReportMappingError` 携带 `reason` 与 `row`。
- `ScanRunner._execute_job()` 分别捕获 MTK 与 Unisoc 的映射拒绝：一个平台被拒不再
  跳过同一 job 的另一个平台。拒绝时记录 `scan_report_mapping_rejected`
  （plan_run、host、platform、row、reason）并累加进程级计数器
  `scan_report_mapping_rejected_total`；该计数器经 `heartbeat_bindings` 随心跳上报。
- 本单元不读 `DeviceId` 列（表头小写为 `deviceid`，与现有 `device_id` 不一致），
  不修改报表格式、上传协议、影子逻辑或工具仓库。

## Alternatives

- 直接丢弃所有不属于本 run 的串号：会让 #3720 的越界保护失效，未采用。
- 在 Detail 正则处就地过滤：未知值集合会在多个入口重复，未采用；归一化集中在
  `normalize_device_id()`。
- 读取 `DeviceId` 列补强证据：属 #3879「不在本单元」的已知限制，需另行裁定，未采用。
- 让映射异常继续向上冒泡：会连带跳过 Unisoc 并只留下 `scan_queue_job_failed`，
  无法区分平台与原因，未采用。

## Verification

反例先行：在未修复的 main（`e48e6a4`）上新增
`test_unknown_device_id_sentinel_with_scoped_path_is_accepted`，报表使用真实
`aeeexp` 表头与 `<scan_root>/<计划目录>/<串号>/vendor_aee_exp/…` 布局，
Detail 写 `Device_id: None`。失败证据：

```text
ValueError: scan report serial outside run or unprovable at row 2
backend/agent/aee/scan_scope.py:299
```

修复后同一用例通过。测试覆盖：

- 哨兵集合所有值及大小写变体（含 `None`、`Discard_Device_Id`）在扫描根内均通过；
- 同样 `Device_id: None` 但路径在扫描根外、无 DLE 命中时按 `unprovable` 拒绝；
- `serial` / `device_id` 列入口分别写入 `none` / `UNDEFINED`：路径在扫描根内通过，
  路径在根外判 `unprovable`。把列值归一化回退为 `str(...).strip()` 时这 4 个用例
  全部失败（误判 `outside_run`），恢复后通过；
- 真实越界串号仍按 `outside_run` 拒绝；缺 Path 列与超时无行分别报
  `missing_path` / `no_rows`；
- MTK 映射被拒后，同一 job 的 Unisoc 照常扫描并上传，计数器加 1 且日志含
  `scan_report_mapping_rejected`；反向 Unisoc 拒绝不影响 MTK 已完成的上传；
- 心跳载荷包含 `scan_report_mapping_rejected_total`。

真实报表离线回放（只读副本，不连生产库，不运行扫描工具）：

- Run 1193：12/12 通过（38 个路径串号，161 行）。
- Run 1179：6/6 通过；Run 1189：1/1 通过；Run 1190：2/2 通过。
- 修复前同一回放：1193 8/12、1179 0/6、1189 0/1、1190 0/2 通过。

回放脚本（放在 `docs/notes/` 记录中，不进生产路径）：

```python
#!/usr/bin/env python3
"""B8 G3r offline replay: report copies only; no DB, no scan tools."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

import xlrd

from backend.agent.aee.scan_scope import ScanReportMappingError, report_mapping


def _report_files(run_dir: Path) -> list[Path]:
    files = sorted((run_dir / "mtk").glob("*_org.xls"))
    return [p for p in files if "_org_dedup_org_" not in p.name]


def _paths(book: xlrd.Book):
    for sheet in book.sheets():
        if not sheet.nrows:
            continue
        columns = {
            str(sheet.cell_value(0, c)).strip().lower(): c
            for c in range(sheet.ncols)
        }
        if "path" not in columns:
            continue
        for index in range(1, sheet.nrows):
            raw = str(sheet.cell_value(index, columns["path"]))
            yield Path(raw.replace("\\", "/"))


def _root_of(path: Path) -> str | None:
    parts = path.parts
    for index, part in enumerate(parts):
        if part == ".stp-scan" and index + 1 < len(parts):
            return str(Path(*parts[: index + 2]))
    return None


def _inspect(report: Path) -> tuple[str, set[str]]:
    book = xlrd.open_workbook(str(report), on_demand=True)
    try:
        roots: set[str] = set()
        serials: set[str] = set()
        for path in _paths(book):
            root = _root_of(path)
            if root is None:
                continue
            roots.add(root)
            rel = path.relative_to(root).parts
            if len(rel) >= 3:
                serials.add(rel[1])
        if len(roots) != 1:
            raise SystemExit(f"{report.name}: expected one scan root, got {sorted(roots)}")
        return roots.pop(), serials
    finally:
        book.release_resources()


def _host_tag(name: str) -> str:
    return f".{name.split('_', 1)[0].rsplit('-', 1)[-1]}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", action="append", required=True, type=Path)
    parser.add_argument("--copy-root", type=Path, default=None)
    args = parser.parse_args()

    copy_root = args.copy_root or Path(tempfile.mkdtemp(prefix="b8-g3r-replay-"))
    copy_root.mkdir(parents=True, exist_ok=True)
    print(f"copy_root={copy_root} (read-only copies; source is never written)")

    failed_total = 0
    for run_dir in args.run_dir:
        run_id = int(run_dir.name)
        files = _report_files(run_dir)
        inspected = {report: _inspect(report) for report in files}
        run_serials: set[str] = set()
        for _, serials in inspected.values():
            run_serials |= serials
        print(f"run={run_id} source={run_dir} files={len(files)} serials={len(run_serials)}")
        run_failed = 0
        for report in files:
            root, _ = inspected[report]
            copy = copy_root / f"{run_id}-{report.name}"
            shutil.copy2(report, copy)
            try:
                mapping = report_mapping(
                    str(copy), serials=sorted(run_serials), sources=[],
                    scan_root=root, plan_run_id=run_id, host_id="offline",
                    platform="mtk",
                )
            except ScanReportMappingError as error:
                run_failed += 1
                print(
                    f"  FAIL host={_host_tag(report.name)} reason={error.reason} "
                    f"row={error.row} detail={error}"
                )
            else:
                print(f"  PASS host={_host_tag(report.name)} rows={len(mapping['rows'])}")
        failed_total += run_failed
        print(f"  SUMMARY run={run_id} pass={len(files) - run_failed} fail={run_failed}")
    print(f"TOTAL failed={failed_total}")
    return 1 if failed_total else 0


if __name__ == "__main__":
    sys.exit(main())
```

实际命令（仓库根目录）：

```text
PYTHONPATH=. .venv/bin/python /tmp/b8_g3r_replay.py \
  --run-dir /mnt/stp-log/dedup/1193 \
  --run-dir /mnt/stp-aee/dedup/1179 \
  --run-dir /mnt/stp-aee/dedup/1189 \
  --run-dir /mnt/stp-aee/dedup/1190
```

结果摘要：

```text
run=1193 files=12 serials=38  SUMMARY pass=12 fail=0
run=1179 files=6  serials=23  SUMMARY pass=6  fail=0
run=1189 files=1  serials=1   SUMMARY pass=1  fail=0
run=1190 files=2  serials=7   SUMMARY pass=2  fail=0
TOTAL failed=0
```

测试命令与结果：

- `scripts/run_pytest.py backend/agent/tests/test_b8_g3_round_scan.py` 等 10 个相关
  Agent 测试文件：126 passed。
- `scripts/run_gates.py check:quick`：见 PR 最终结果。

## Revisit

- 工具新增或改名未知值哨兵时，同步 `UNKNOWN_DEVICE_ID_VALUES`，不要在各调用点另建集合。
- 若后续裁定读取 `DeviceId` 列，必须复用 `normalize_device_id()`，并重新评估
  `outside_run` 的证据优先级。
- 告警侧目前只消费 `scan_report_mapping_rejected_total` 的进程级累计；需要按 host
  长期趋势告警时，另建指标而不是改本计数语义。
