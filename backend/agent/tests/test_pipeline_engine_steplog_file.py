"""#739b：`_MQStepLogger` 的本地落盘失败不再静默（且不刷屏）。

背景：两处 `except Exception: pass` 让「本地日志写不进去」与「这一步本来就没有输出」
不可区分——而落盘失败恰恰最可能发生在**事故时**（坏盘/满盘/权限/路径被占），
那时本地副本正是最要紧的证据。修法：**一次判定 + latch**——首次失败记一条 warning
（带路径与底层错误），此后本步不再尝试本地写入（不刷屏，也不按行放大 syscall）。

三条判据：

1. 构造期 `os.makedirs` 失败 → 立即告警一次并置位；
2. 首次**写入**失败 → 告警一次并置位；
3. 置位之后连续写入 → **只告警一次**、且**不再触碰文件系统**（用 open 计数证明）。
"""

from __future__ import annotations

import builtins
import logging

from backend.agent.pipeline_engine import _MQStepLogger


def _logger(path: str) -> _MQStepLogger:
    # mq 传 None：本组只关心本地落盘那一路
    return _MQStepLogger(None, run_id=7, step_id_str="step-1", log_file=path)


def test_makedirs_failure_warns_once_at_construction(tmp_path, caplog):
    """父路径被一个**文件**占位 → makedirs 必失败；构造期就要告警。"""
    blocker = tmp_path / "blocker"
    blocker.write_text("occupied", encoding="utf-8")
    log_file = str(blocker / "step.log")  # 父目录其实是个文件

    with caplog.at_level(logging.WARNING, logger="backend.agent.pipeline_engine"):
        sink = _logger(log_file)

    assert sink._file_failed is True, "makedirs 失败后必须置位，否则每行都会重试"
    messages = [r.message for r in caplog.records if "step_log_file" in str(r.message)]
    assert len(messages) == 1 and "step_log_file_unavailable" in str(messages[0])


def test_write_failure_warns_once_and_latches(tmp_path, caplog, monkeypatch):
    """写入失败（目标是目录）→ 首次告警一次；其后连续写入不再触发文件系统调用。"""
    target = tmp_path / "as-directory"
    target.mkdir()
    log_file = str(target)  # open(dir, "a") → IsADirectoryError（确定性，不依赖权限）

    sink = _logger(log_file)
    assert sink._file_failed is False, "构造期能建目录（tmp_path 存在），不该置位"

    real_open = builtins.open
    calls: list[str] = []

    def counting_open(file, *args, **kwargs):
        if str(file) == log_file:
            calls.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting_open)

    with caplog.at_level(logging.WARNING, logger="backend.agent.pipeline_engine"):
        sink.info("first line")   # 首次失败：告警 + latch
        for i in range(20):
            sink.info(f"line {i}")

    warnings = [r.message for r in caplog.records if "step_log_file_write_failed" in str(r.message)]
    assert len(warnings) == 1, f"写失败必须只报一次，实测 {len(warnings)} 次（刷屏）"
    assert sink._file_failed is True
    assert len(calls) == 1, (
        f"latch 之后仍尝试落盘 {len(calls)} 次——失败重试应按步收敛到 1 次，"
        "否则坏盘场景每行一次 syscall"
    )


def test_healthy_path_still_writes_every_line(tmp_path, caplog):
    """反向：能写的时候不被 latch 误伤——每行都要落盘。"""
    log_file = str(tmp_path / "ok" / "step.log")
    with caplog.at_level(logging.WARNING, logger="backend.agent.pipeline_engine"):
        sink = _logger(log_file)
        sink.info("a")
        sink.info("b")

    assert sink._file_failed is False
    assert "a" in (tmp_path / "ok" / "step.log").read_text(encoding="utf-8")
    assert not [r for r in caplog.records if "step_log_file" in str(r.message)]
