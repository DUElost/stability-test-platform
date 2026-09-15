#!/usr/bin/env python3
"""幂等发布 GitHub issue/PR 评论：marker 查重 + 复读校验（#2131）。

为什么存在
----------
本机经代理访问 GitHub API 偶发 EOF/502。两类朴素写法都会产出**重复评论**：

1. 「失败就重发」——请求已落地、响应丢失时再发一条；
2. 「比较评论条数」判断是否落地——基线取数失败即整体失效
   （2026-09-15 #735 连发 4 条：`before` 取数为空 → `[ after -gt before ]`
   恒为假 → 每轮都发）。

本工具以**隐藏 marker** 作幂等键（正文尾部写入 ``<!-- gh-comment-once:<marker> -->``）：

- 发布前：分页拉取该 issue/PR 的全部评论，命中 marker → 默认跳过（``--update`` 则 PATCH 原评论）；
- 发布后 / 失败后：**只按 marker 复读**判定是否落地，不看条数、不比分页；
- 拉取不可用（无法判定）→ 默认**拒绝发布**（fail-safe），``--force`` 才越过；
- 失败重发前必须先「复读确认不存在」，否则不重发。

用法
----
    python tools/dev/gh_comment_once.py --issue 735 --body-file /tmp/note.md
    python tools/dev/gh_comment_once.py --pr 2097 --body-file note.md --marker "2097-review"
    python tools/dev/gh_comment_once.py --issue 735 --body-file note.md --update
    cat note.md | python tools/dev/gh_comment_once.py --issue 735 --body - --json

退出码
------
- ``0``：``created`` / ``skipped`` / ``updated``（幂等成功；``--json`` 输出见 stdout）
- ``2``：``unknown``——无法判定是否已发布，**本次不发**（重跑即可；重跑时发布前查重兜底）
- ``1``：参数/环境错误（缺 body、解析不出 repo、gh 不可用等）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

MARKER_PREFIX = "gh-comment-once:"
MARKER_RE = re.compile(r"<!--\s*" + MARKER_PREFIX + r"([^\s>]+)\s*-->")
DEFAULT_ATTEMPTS = 3
DEFAULT_BACKOFF_S = 2.0
PAGE_SIZE = 100


class GhError(RuntimeError):
    """单次 gh 调用失败（可重试）。"""


class GhUnavailable(GhError):
    """复读/查重不可用——无法判定状态，调用方必须 fail-safe。"""


@dataclass(frozen=True)
class Comment:
    id: int
    body: str
    url: str


def derive_marker(body: str) -> str:
    """未显式给 marker 时按正文内容派生（同一正文 = 同一幂等键）。

    归一化：统一换行、去掉首尾空白，避免复制粘贴产生的尾随差异改变结论。
    """
    normalized = body.replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12]


def extract_marker(body: str) -> str | None:
    m = MARKER_RE.search(body or "")
    return m.group(1) if m else None


def with_marker(body: str, marker: str) -> str:
    """正文尾部写入隐藏 marker（已存在则替换，避免叠加多个）。"""
    stripped = MARKER_RE.sub("", body).rstrip()
    return f"{stripped}\n\n<!-- {MARKER_PREFIX}{marker} -->\n"


def parse_repo_from_remote(url: str) -> str | None:
    """https://github.com/O/R.git / git@github.com:O/R.git → O/R。"""
    url = (url or "").strip()
    m = re.match(r"^(?:https?://[^/]+/|git@[^:]+:)([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def resolve_repo(explicit: str | None, cwd: str) -> str:
    if explicit:
        return explicit
    env = os.environ.get("GH_REPO")
    if env:
        return env
    proc = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        capture_output=True, text=True, cwd=cwd,
    )
    if proc.returncode != 0:
        raise GhError(f"无法解析仓库：git remote get-url origin 失败（rc={proc.returncode}）")
    parsed = parse_repo_from_remote(proc.stdout)
    if not parsed:
        raise GhError(f"无法从 remote 解析 owner/repo：{proc.stdout.strip()!r}")
    return parsed


class GhApi:
    """`gh api` 薄封装（issue 维度分页、退避重试、失败区分可重试/不可判定）。"""

    def __init__(
        self,
        repo: str,
        number: int,
        *,
        bin_: str = "gh",
        timeout: float = 60.0,
        attempts: int = DEFAULT_ATTEMPTS,
        backoff_s: float = DEFAULT_BACKOFF_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.repo = repo
        self.number = int(number)
        self._bin = bin_
        self._timeout = timeout
        self.attempts = max(1, attempts)
        self._backoff_s = backoff_s
        self._sleep = sleep

    # ── 低层 ──
    def backoff(self, attempt: int) -> None:
        self._sleep(self._backoff_s * attempt)

    def _run(self, args: Sequence[str], *, stdin: str | None = None) -> str:
        try:
            proc = subprocess.run(
                [self._bin, *args], capture_output=True, text=True,
                timeout=self._timeout, input=stdin,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            raise GhError(f"gh 调用异常：{exc}") from exc
        if proc.returncode != 0:
            raise GhError(
                f"gh 调用失败（rc={proc.returncode}）：{args[:3]}… {proc.stderr.strip()[:200]}"
            )
        return proc.stdout

    def _api(self, method: str, path: str, payload: dict | None = None) -> object:
        args = ["api", "-X", method, path]
        stdin = None
        if payload is not None:
            args += ["--input", "-"]
            stdin = json.dumps(payload)
        out = self._run(args, stdin=stdin)
        try:
            return json.loads(out or "null")
        except json.JSONDecodeError as exc:
            raise GhError(f"gh 返回非 JSON：{out[:200]!r}") from exc

    # ── 高层 ──
    def list_comments(self) -> list[Comment]:
        """分页拉取本 issue/PR 的全部评论。任一页失败 → GhUnavailable（不可判定）。"""
        comments: list[Comment] = []
        page = 1
        while True:
            path = (
                f"repos/{self.repo}/issues/{self.number}/comments"
                f"?per_page={PAGE_SIZE}&page={page}&sort=created&direction=asc"
            )
            data = None
            last_exc: Exception | None = None
            for attempt in range(1, self.attempts + 1):
                try:
                    data = self._api("GET", path)
                    break
                except GhError as exc:  # 含 EOF/502/超时
                    last_exc = exc
                    if attempt < self.attempts:
                        self.backoff(attempt)
            if data is None:
                raise GhUnavailable(f"评论列表不可用（page={page}）：{last_exc}")
            if not isinstance(data, list):
                raise GhUnavailable(f"评论列表响应非数组（page={page}）")
            for item in data:
                if not isinstance(item, dict):
                    continue
                comments.append(
                    Comment(
                        id=int(item.get("id") or 0),
                        body=str(item.get("body") or ""),
                        url=str(item.get("html_url") or ""),
                    )
                )
            if len(data) < PAGE_SIZE:
                return comments
            page += 1

    def find_by_marker(self, marker: str) -> Comment | None:
        for c in self.list_comments():
            if extract_marker(c.body) == marker:
                return c
        return None

    def create_comment(self, body: str) -> Comment:
        data = self._api("POST", f"repos/{self.repo}/issues/{self.number}/comments",
                         {"body": body})
        if not isinstance(data, dict) or not data.get("id"):
            raise GhError(f"创建评论返回异常：{str(data)[:200]}")
        return Comment(id=int(data["id"]), body=str(data.get("body") or ""),
                       url=str(data.get("html_url") or ""))

    def update_comment(self, comment_id: int, body: str) -> Comment:
        data = self._api("PATCH", f"repos/{self.repo}/issues/comments/{comment_id}",
                         {"body": body})
        if not isinstance(data, dict) or not data.get("id"):
            raise GhError(f"更新评论返回异常：{str(data)[:200]}")
        return Comment(id=int(data["id"]), body=str(data.get("body") or ""),
                       url=str(data.get("html_url") or ""))


def post_once(
    gh: GhApi,
    *,
    body: str,
    marker: str,
    update: bool = False,
    force: bool = False,
    log: Callable[[str], None] = print,
) -> dict:
    """幂等发布主流程，返回 {'action': created|skipped|updated|unknown, ...}。

    语义要点：
    - 发布前查重；命中 → skipped（或 update）；**查重不可用 → unknown（不发）**；
    - 创建失败后**先复读**：已落地 → created（响应丢失但评论在）；
      确认不存在 → 才允许重试创建；无法判定 → unknown（不重发）；
    - ``force`` 是危险出口：跳过查重直接发（用于已知查重不可用、且人工确认
      尚未发布时）。不带 ``force`` 时任何「无法判定」都不发布。
    """
    full_body = with_marker(body, marker)

    if force:
        log("warn: --force 跳过查重直接发布（可能产生重复评论）")
        try:
            created = gh.create_comment(full_body)
        except GhError as exc:
            log(f"fail: --force 创建失败：{exc}")
            return {"action": "unknown", "marker": marker, "forced": True}
        log(f"created: comment {created.id}")
        return {"action": "created", "marker": marker, "comment_id": created.id,
                "url": created.url, "forced": True}

    try:
        existing = gh.find_by_marker(marker)
    except GhUnavailable as unavail:
        log(f"unknown: 查重不可用，拒绝发布（{unavail}）")
        return {"action": "unknown", "marker": marker}

    if existing is not None:
        if not update:
            log(f"skipped: marker 已存在（comment {existing.id}）")
            return {"action": "skipped", "marker": marker,
                    "comment_id": existing.id, "url": existing.url}
        updated = gh.update_comment(existing.id, full_body)
        log(f"updated: comment {updated.id}")
        return {"action": "updated", "marker": marker,
                "comment_id": updated.id, "url": updated.url}

    for attempt in range(1, gh.attempts + 1):
        try:
            created = gh.create_comment(full_body)
        except GhError as exc:
            log(f"warn: 创建失败（第 {attempt} 次）：{exc}")
            try:
                landed = gh.find_by_marker(marker)
            except GhUnavailable as unavail:
                log(f"unknown: 无法判定是否已发布，本次不重发（{unavail}）")
                return {"action": "unknown", "marker": marker}
            if landed is not None:
                log(f"created: 响应丢失但评论已落地（comment {landed.id}）")
                return {"action": "created", "marker": marker,
                        "comment_id": landed.id, "url": landed.url,
                        "detected_after_error": True}
            if attempt < gh.attempts:
                log("info: 复读确认不存在，退避后重试创建")
                gh.backoff(attempt)
                continue
            log("fail: 复读确认不存在且重试耗尽")
            return {"action": "unknown", "marker": marker}
        else:
            log(f"created: comment {created.id}")
            return {"action": "created", "marker": marker,
                    "comment_id": created.id, "url": created.url}
    return {"action": "unknown", "marker": marker}


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        return Path(args.body_file).read_text(encoding="utf-8")
    if args.body == "-":
        return sys.stdin.read()
    if args.body:
        return args.body
    raise GhError("必须提供 --body-file / --body - / --body TEXT")


def main(argv: Sequence[str] | None = None, *, gh: GhApi | None = None) -> int:
    parser = argparse.ArgumentParser(description="幂等发布 GitHub issue/PR 评论（#2131）")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--issue", type=int, help="issue/PR 号（PR 也是 issue）")
    target.add_argument("--pr", type=int, help="--issue 的别名")
    parser.add_argument("--body-file", help="正文文件路径")
    parser.add_argument("--body", help="正文文本，或 '-' 从 stdin 读")
    parser.add_argument("--marker", help="幂等键（默认按正文派生）")
    parser.add_argument("--update", action="store_true", help="已存在时 PATCH 原评论")
    parser.add_argument("--force", action="store_true",
                        help="危险：跳过查重失败保护直接发布（可能重复）")
    parser.add_argument("--repo", help="owner/repo（默认 GH_REPO → git remote origin）")
    parser.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS,
                        help=f"查询/创建的重试次数（默认 {DEFAULT_ATTEMPTS}）")
    parser.add_argument("--json", action="store_true", help="stdout 输出 JSON 结果")
    args = parser.parse_args(argv)

    number = args.issue or args.pr
    try:
        body = _read_body(args)
        repo = resolve_repo(args.repo, os.getcwd())
    except (GhError, OSError) as exc:
        print(f"参数/环境错误：{exc}", file=sys.stderr)
        return 1

    marker = args.marker or derive_marker(body)
    client = gh or GhApi(repo, number, attempts=args.attempts)

    result = post_once(
        client, body=body, marker=marker, update=args.update, force=args.force,
        log=lambda msg: print(msg, file=sys.stderr),
    )
    if args.json:
        print(json.dumps({**result, "repo": repo, "issue": number}, ensure_ascii=False))
    return 2 if result["action"] == "unknown" else 0


if __name__ == "__main__":
    raise SystemExit(main())
