"""Log processing pipeline actions: aee_extract, log_scan."""

import logging
import os
import re
import subprocess
from collections import defaultdict
from backend.agent.pipeline_engine import StepContext, StepResult

logger = logging.getLogger(__name__)


def aee_extract(ctx: StepContext) -> StepResult:
    """Invoke aee_extract tool to decrypt db log files."""
    input_dir = ctx.params.get("input_dir", "")
    output_dir = ctx.params.get("output_dir", "")
    tool_path = ctx.params.get("tool_path", "aee_extract")

    if not input_dir:
        return StepResult(success=False, exit_code=1, error_message="No input_dir specified")
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
