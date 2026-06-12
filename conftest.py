"""Repo-level pytest collection guards.

backend/agent/scripts 是部署脚本载荷(<name>/v<version>/<entry>.py),
不是测试代码;其中 monkey_test.py 等命名匹配 pytest 默认 *_test.py
收集规则,且多版本目录同名 basename 会触发 import file mismatch。
"""

collect_ignore_glob = ["backend/agent/scripts/*"]
