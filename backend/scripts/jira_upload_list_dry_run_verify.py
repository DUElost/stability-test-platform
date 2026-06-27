"""Jira upload_list 单条验证：从 dedup xls 截取 1 行 → stage1 生成模板 → stage2 dry-run。

upload_list 阶段仅本地 Excel 转换，不访问 Jira。
create 阶段加 --dry-run，只校验不写 Jira 库。

用法:
    python backend/scripts/jira_upload_list_dry_run_verify.py
    python backend/scripts/jira_upload_list_dry_run_verify.py --vendor tinno
    python backend/scripts/jira_upload_list_dry_run_verify.py --source-xls "Y:\\...\\dedup_org.xls"
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# 仓库根 .env（JIRA_USERNAME / JIRA_PASSWORD）
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILE = _REPO_ROOT / ".env"
if _ENV_FILE.is_file():
    try:
        from dotenv import load_dotenv
        load_dotenv(_ENV_FILE, override=False)
    except ImportError:
        pass

DEFAULT_SOURCE = (
    r"Y:\sonic_tinno\jira\52"
    r"\auto-fdaf1d55e319_Result_None_None_MonkeyAEE_SH_20260627_052222"
    r"_org_dedup_org_20260627_052224.xls"
)
TOOL_ROOT = Path(r"F:\automation-toolkit\python-tools\stability_Jira-Automation")
VENDORS = {
    "transsion": TOOL_ROOT / "Transsion_Jira_Tool_20260323",
    "tinno": TOOL_ROOT / "Tinno_Jira_Tool_20260520",
}


def load_vendor_env(vendor: str) -> None:
    """加载厂商工具凭据；覆盖仓库 .env 里可能误配的跨厂商账号。"""
    tool_dir = VENDORS[vendor]
    candidates = [
        tool_dir / ".env.local",
        tool_dir / "tools" / ".env",
    ]
    for path in candidates:
        if not path.is_file():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, _, value = line.partition("=")
            key = key.strip()
            if not key:
                continue
            value = value.strip().strip('"').strip("'")
            os.environ[key] = value


def die(msg: str) -> None:
    print(f"[FAIL] {msg}", file=sys.stderr)
    sys.exit(1)


def pick_first_valid_row(df: pd.DataFrame) -> pd.DataFrame:
    exp_col = "ExpClass" if "ExpClass" in df.columns else None
    pkg_col = "Package" if "Package" in df.columns else None
    if exp_col or pkg_col:
        mask = pd.Series(False, index=df.index)
        if exp_col:
            mask |= df[exp_col].notna() & (df[exp_col].astype(str).str.strip() != "")
        if pkg_col:
            mask |= df[pkg_col].notna() & (df[pkg_col].astype(str).str.strip() != "")
        subset = df.loc[mask]
        if not subset.empty:
            return subset.iloc[[0]]
    return df.iloc[[0]]


def write_one_row_xlsx(src: Path, dest: Path) -> tuple[pd.DataFrame, Path]:
    df = pd.read_excel(src, engine="xlrd")
    one = pick_first_valid_row(df)
    dest.parent.mkdir(parents=True, exist_ok=True)
    one.to_excel(dest, index=False, engine="openpyxl")
    return one, dest


def run_cmd(argv: list[str], *, cwd: Path, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    print("\n>>>", " ".join(argv))
    return subprocess.run(
        argv,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def latest_upload_list_xlsx(result_dir: Path, prefix: str) -> Path | None:
    candidates = sorted(result_dir.glob(f"{prefix}*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--vendor", choices=sorted(VENDORS), default="tinno")
    p.add_argument("--source-xls", default=DEFAULT_SOURCE)
    p.add_argument("--work-dir", default=str(TOOL_ROOT / "_verify_run52"))
    p.add_argument("--python", default=sys.executable)
    args = p.parse_args()
    load_vendor_env(args.vendor)

    tool_dir = VENDORS[args.vendor]
    if not tool_dir.is_dir():
        die(f"工具目录不存在: {tool_dir}")

    src = Path(args.source_xls)
    if not src.is_file():
        die(f"源 xls 不存在: {src}")

    work = Path(args.work_dir)
    work.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    one_xlsx = work / f"sample_one_row_{ts}.xlsx"
    one_row_df, one_xlsx = write_one_row_xlsx(src, one_xlsx)
    row = one_row_df.iloc[0]
    exp = row.get("ExpClass", "?")
    pkg = row.get("Package", "?")
    print(f"单条样本: ExpClass={exp!r} Package={pkg!r} -> {one_xlsx}")

    gen_script = tool_dir / f"generate_{args.vendor}_jira_upload_list.py"
    create_script = tool_dir / f"create_{args.vendor}_jira_batch_from_excel.py"
    if not gen_script.is_file():
        die(f"缺少 stage1 脚本: {gen_script}")

    out_xlsx = work / f"JIRA_Upload_List_{args.vendor}_verify_{ts}.xlsx"
    stage1 = run_cmd(
        [
            args.python,
            str(gen_script),
            "--add-main-excel",
            str(one_xlsx),
            "--set-output",
            str(out_xlsx),
        ],
        cwd=tool_dir,
    )
    print(stage1.stdout[-4000:] if stage1.stdout else "")
    if stage1.stderr:
        print(stage1.stderr[-2000:], file=sys.stderr)
    if stage1.returncode != 0:
        die(f"upload_list(stage1) 退出码 {stage1.returncode}")

    upload_xlsx = out_xlsx if out_xlsx.is_file() else latest_upload_list_xlsx(
        tool_dir / "result",
        "JIRA_Upload_List_" + ("Transsion" if args.vendor == "transsion" else "Tinno"),
    )
    if upload_xlsx is None or not upload_xlsx.is_file():
        die("未找到 stage1 产出的 JIRA_Upload_List xlsx")

    print(f"\n[PASS] upload_list 完成: {upload_xlsx}")
    upload_df = pd.read_excel(upload_xlsx, engine="openpyxl")
    print(f"  上传模板行数: {len(upload_df)}")

    if not create_script.is_file():
        print("[SKIP] create 脚本不存在，仅完成 upload_list 本地验证")
        return

    stage2 = run_cmd(
        [args.python, str(create_script), "--add-excel-file", str(upload_xlsx), "--dry-run"],
        cwd=tool_dir,
        timeout=900,
    )
    print(stage2.stdout[-6000:] if stage2.stdout else "")
    if stage2.stderr:
        print(stage2.stderr[-2000:], file=sys.stderr)
    if stage2.returncode != 0:
        die(f"create dry-run 退出码 {stage2.returncode}")

    print(f"\n[PASS] create --dry-run 完成（1 条，未写 Jira 库）")


if __name__ == "__main__":
    main()
