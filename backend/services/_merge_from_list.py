"""Merge helper — 读取文件列表并调原始 start_log_scan.py -merge_files。

解决 Windows CreateProcessW 命令行 32767 字符限制：当文件路径列表过长时，
dedup_scan.run_merge_sync 将列表写入临时文件并通过 STP_MERGE_FILE_LIST 环境变量
传递路径，本脚本读取文件列表后构造完整 argv 调原始 merge 脚本。

用法：python _merge_from_list.py <start_log_scan.py 路径>

环境变量：
  STP_MERGE_FILE_LIST  — 文件列表路径（一行一个 _org.xls 路径，必填）
  STP_DEDUP_SCAN_SIDE   — shanghai / factory（默认 shanghai）
"""
import os
import subprocess
import sys


def main() -> None:
    listpath = os.environ.get("STP_MERGE_FILE_LIST", "")
    if not listpath:
        print("ERROR: STP_MERGE_FILE_LIST env not set", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print("USAGE: python _merge_from_list.py <start_log_scan.py>", file=sys.stderr)
        sys.exit(1)

    scan_script = sys.argv[1]
    side = os.environ.get("STP_DEDUP_SCAN_SIDE", "shanghai")

    with open(listpath, encoding="utf-8") as f:
        org_files = [line.strip() for line in f if line.strip()]

    if not org_files:
        print("WARNING: empty file list", file=sys.stderr)
        sys.exit(0)

    argv = [sys.executable, scan_script, "-merge_files"] + org_files + ["-side", side]
    result = subprocess.run(argv, cwd=os.path.dirname(scan_script) or None)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
