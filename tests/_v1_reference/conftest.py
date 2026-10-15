"""V1.0 时期测试封存：pytest 收集忽略。

V1.0 时期测试封存目录，pytest 不应自动收集（避免 V1.0 import 失败影响正式测试）。
V2.1 阶段 0 起步在 `tests/v21/` 下，仅收集 V2.1 时期的测试。
"""
collect_ignore_glob = ["*"]
