"""Log processing pipeline actions: aee_extract, log_scan."""

import json
import logging
import os
import re
import shutil
import subprocess
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from ..pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def aee_extract(ctx: StepContext) -> StepResult:
    """Invoke aee_extract tool to decrypt db log files.

    When batch=true, recursively scans input_dir for .dbg files and decrypts
    them in parallel with retry tracking via LocalDB.
    """
    input_dir = ctx.params.get("input_dir", "")
    output_dir = ctx.params.get("output_dir", "")
    tool_path = ctx.params.get("tool_path", "aee_extract")
    batch = ctx.params.get("batch", False)

    if not input_dir:
        return StepResult(success=False, exit_code=1, error_message="No input_dir specified")

    if batch:
        return _aee_extract_batch(ctx, input_dir, tool_path)

    # Original single-file mode
    if not output_dir:
        output_dir = input_dir + "_decoded"

    os.makedirs(output_dir, exist_ok=True)

    try:
        result = subprocess.run(
            [tool_path, input_dir, output_dir],
            capture_output=True, text=True, timeout=300,
        )
        if ctx.logger:
            for line in result.stdout.splitlines():
                ctx.logger.info(line)
            for line in result.stderr.splitlines():
                ctx.logger.warn(line)

        if result.returncode != 0:
            return StepResult(
                success=False, exit_code=result.returncode,
                error_message=f"aee_extract exited with code {result.returncode}",
            )
        return StepResult(success=True, metrics={"output_dir": output_dir})
    except FileNotFoundError:
        return StepResult(success=False, exit_code=1, error_message=f"aee_extract tool not found at '{tool_path}'")
    except subprocess.TimeoutExpired:
        return StepResult(success=False, exit_code=124, error_message="aee_extract timed out")
    except Exception as e:
        return StepResult(success=False, exit_code=1, error_message=str(e))


def _aee_extract_batch(ctx: StepContext, input_dir: str, tool_path: str) -> StepResult:
    """Batch mode: recursively find .dbg files and decrypt in parallel."""
    max_workers = ctx.params.get("max_workers", 4)
    retry_limit = ctx.params.get("retry_limit", 2)
    min_free_disk_gb = ctx.params.get("min_free_disk_gb", 10)
    state_key_prefix = ctx.params.get("state_key_prefix", "aee_decrypt")

    # 1. Disk space check
    try:
        usage = shutil.disk_usage(input_dir)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < min_free_disk_gb:
            if ctx.logger:
                ctx.logger.warn(f"Low disk space: {free_gb:.1f}GB free (min={min_free_disk_gb}GB), skipping decrypt")
            return StepResult(
                success=True,
                metrics={"total_found": 0, "decrypted": 0, "failed": 0, "skipped_retry_limit": 0, "skipped_low_disk": True},
            )
    except Exception:
        pass  # If disk check fails, proceed anyway

    # 2. Find all .dbg files
    dbg_files = []
    for root, _dirs, files in os.walk(input_dir):
        for f in files:
            if f.endswith(".dbg"):
                dbg_files.append(os.path.join(root, f))

    if not dbg_files:
        if ctx.logger:
            ctx.logger.info("No .dbg files found in input_dir")
        return StepResult(
            success=True,
            metrics={"total_found": 0, "decrypted": 0, "failed": 0, "skipped_retry_limit": 0, "skipped_low_disk": False},
        )

    # 3. Load failure state from LocalDB
    failures = {}
    if ctx.local_db and hasattr(ctx.local_db, "get_state"):
        try:
            raw = ctx.local_db.get_state(f"{state_key_prefix}:failures", "{}")
            failures = json.loads(raw)
        except Exception:
            failures = {}

    # Filter out files exceeding retry limit
    skipped_retry = 0
    files_to_process = []
    for path in dbg_files:
        if failures.get(path, 0) >= retry_limit:
            skipped_retry += 1
        else:
            files_to_process.append(path)

    # 4. Parallel decrypt
    decrypted = 0
    failed = 0

    def _decrypt_one(dbg_path):
        output_path = dbg_path.replace(".dbg", "_decoded")
        try:
            result = subprocess.run(
                [tool_path, dbg_path, output_path],
                capture_output=True, text=True, timeout=300,
            )
            return dbg_path, result.returncode == 0
        except Exception:
            return dbg_path, False

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_decrypt_one, p) for p in files_to_process]
        for future in as_completed(futures):
            path, success = future.result()
            if success:
                decrypted += 1
            else:
                failed += 1
                failures[path] = failures.get(path, 0) + 1

    # 5. Save failures back to LocalDB
    if ctx.local_db and hasattr(ctx.local_db, "set_state"):
        try:
            ctx.local_db.set_state(f"{state_key_prefix}:failures", json.dumps(failures))
        except Exception:
            pass

    if ctx.logger:
        ctx.logger.info(
            f"AEE decrypt (batch): total={len(dbg_files)}, decrypted={decrypted}, "
            f"failed={failed}, skipped_retry={skipped_retry}"
        )

    return StepResult(
        success=True,
        metrics={
            "total_found": len(dbg_files),
            "decrypted": decrypted,
            "failed": failed,
            "skipped_retry_limit": skipped_retry,
            "skipped_low_disk": False,
        },
    )


def log_scan(ctx: StepContext) -> StepResult:
    """Scan log files for keywords, deduplicate, and generate report."""
    input_dir = ctx.params.get("input_dir", "")
    keywords = ctx.params.get("keywords", ["FATAL", "CRASH", "ANR"])
    deduplicate = ctx.params.get("deduplicate", True)

    if not input_dir or not os.path.isdir(input_dir):
        return StepResult(success=False, exit_code=1, error_message=f"input_dir not found: {input_dir}")

    matches = []
    seen_signatures = set()
    keyword_counts = defaultdict(int)

    pattern = re.compile("|".join(re.escape(k) for k in keywords), re.IGNORECASE)

    for root, _dirs, files in os.walk(input_dir):
        for fname in files:
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for line_num, line in enumerate(f, 1):
                        m = pattern.search(line)
                        if m:
                            keyword = m.group(0).upper()
                            keyword_counts[keyword] += 1

                            # Deduplicate by first 100 chars of matching line
                            sig = line.strip()[:100]
                            if deduplicate and sig in seen_signatures:
                                continue
                            seen_signatures.add(sig)

                            matches.append({
                                "file": os.path.relpath(fpath, input_dir),
                                "line": line_num,
                                "keyword": keyword,
                                "content": line.strip()[:200],
                            })
            except Exception:
                continue

    if ctx.logger:
        ctx.logger.info(f"Scan complete: {len(matches)} unique matches in {input_dir}")
        for kw, count in keyword_counts.items():
            ctx.logger.info(f"  {kw}: {count} occurrences")

    report = {
        "total_matches": sum(keyword_counts.values()),
        "unique_matches": len(matches),
        "keyword_counts": dict(keyword_counts),
        "matches": matches[:100],  # Cap at 100 for report size
    }

    return StepResult(success=True, metrics=report)
