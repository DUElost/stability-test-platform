"""Merge helper — 读取文件列表并调原始 start_log_scan.py -merge_files。

绕过 Windows CreateProcessW 命令行 32767 字符限制：
将完整命令写入临时 .cmd 批处理文件后执行。
cmd.exe 解析 .cmd 文件中的命令行时无 32767 字符限制，且子进程
通过 python 解释器正常启动，原始脚本的 import / __file__ 均正确。

runpy.run_path 方案已废弃：原始脚本 depends on 同目录 modules/ 子包，
runpy 不将脚本目录加入 sys.path，导致 import 失败。

用法：python _merge_from_list.py <start_log_scan.py 路径>

环境变量：
  STP_MERGE_FILE_LIST  — 文件列表路径（一行一个 _org.xls 路径，必填）
  STP_DEDUP_SCAN_SIDE   — shanghai / factory（默认 shanghai）
  STP_DEDUP_SCAN_PYTHON — Python 解释器路径（必填，与父进程相同）
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> None:
    listpath = os.environ.get("STP_MERGE_FILE_LIST", "")
    if not listpath:
        print("ERROR: STP_MERGE_FILE_LIST env not set", file=sys.stderr)
        sys.exit(1)

    if len(sys.argv) < 2:
        print("USAGE: python _merge_from_list.py <start_log_scan.py>", file=sys.stderr)
        sys.exit(1)

    scan_script = sys.argv[1]
    python_exe = os.environ.get("STP_DEDUP_SCAN_PYTHON", sys.executable)
    side = os.environ.get("STP_DEDUP_SCAN_SIDE", "shanghai")

    with open(listpath, encoding="utf-8") as f:
        org_files = [line.strip() for line in f if line.strip()]

    if not org_files:
        print("WARNING: empty file list", file=sys.stderr)
        sys.exit(0)

    argv = [python_exe, scan_script, "-merge_files"] + org_files + ["-side", side]

    # Windows cmd.exe 解析 .cmd 文件中的命令行无 32767 限制
    # 通过临时批处理文件绕过 CreateProcessW 限制
    if os.name == "nt":
        cmd_content = " ".join(_cmd_quote(a) for a in argv) + "\n"
        cmd_file = Path(tempfile.mktemp(suffix=".cmd", prefix="merge_helper_"))
        cmd_file.write_text(cmd_content, encoding="utf-8")
        try:
            result = subprocess.run(
                ["cmd", "/c", str(cmd_file)],
                cwd=str(Path(scan_script).parent) or None,
                timeout=300,
            )
        except subprocess.TimeoutExpired:
            sys.exit(124)
        finally:
            try:
                cmd_file.unlink(missing_ok=True)
            except Exception:
                pass
    else:
        result = subprocess.run(
            argv,
            cwd=str(Path(scan_script).parent) or None,
            timeout=300,
        )

    sys.exit(result.returncode)


def _cmd_quote(s: str) -> str:
    """引号转义：保证特殊字符在 cmd.exe 中正确解析。"""
    if " " in s or '"' in s or "(" in s or ")" in s or "&" in s:
        return '"' + s.replace('"', '""') + '"'
    return s


if __name__ == "__main__":
    main()
