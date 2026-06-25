"""Merge helper — 读取文件列表并调原始 start_log_scan.py -merge_files。

通过 runpy.run_path() 在当前进程空间执行原始脚本，
绕过 Windows CreateProcessW 命令行 32767 字符限制。
不再使用 subprocess 调原始脚本（会重建超长 argv，无法解决问题）。

用法：python _merge_from_list.py <start_log_scan.py 路径>

环境变量：
  STP_MERGE_FILE_LIST  — 文件列表路径（一行一个 _org.xls 路径，必填）
  STP_DEDUP_SCAN_SIDE   — shanghai / factory（默认 shanghai）
"""
import os
import runpy
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

    # 通过修改 sys.argv 让原始脚本的 argparse/参数解析正常工作，
    # 然后用 runpy.run_path 在当前进程空间执行脚本——无需 CreateProcessW，
    # 没有命令行长度限制。
    sys.argv = [scan_script, "-merge_files"] + org_files + ["-side", side]
    # run_path 中 sys.exit() 会直接退出 helper 进程，
    # 父进程通过 returncode 判断成功/失败。
    runpy.run_path(scan_script, run_name="__main__")


if __name__ == "__main__":
    main()
