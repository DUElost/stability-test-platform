"""api 层 HTTP 中间件（#3297，C4 棘轮出口）。

core 是最底层，不得依赖 web 框架（C4）；这些中间件天然接触 Request/Response，
归 api 层。core 只保留被多方复用的纯函数（limiter 的客户端 IP 解析留在
core/limiter.py；audit/security 的纯函数部分见各自拆分说明）。
"""
