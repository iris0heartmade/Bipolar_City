"""V1.0 时期 core 月结与仿真代码封存目录（参考代码，不作为正式代码）。

【封存归档 · 2026-10-15】
本目录是 V1.0 月结与仿真基础设施（settlement.py、monthly.py、simulation.py、
service.py）的封存归档版本。V2.1 阶段 9（月结算 15 步引擎）会重做月结逻辑。

正式 core 模块：
- `backend/core/errors.py` — 错误类定义（保留）
- `backend/core/clock.py` — 时钟基础设施（保留）

V1.0 期间月结实现的核心文件：
- settlement.py — 交易队列与统一清算（V2.1 阶段 9 会重做）
- monthly.py — 月度结算入口（V2.1 阶段 9 会重做为 15 步）
- simulation.py — 仿真入口（V2.1 阶段 9 会重做）
- service.py — 服务层（V2.1 阶段 10 政府接口会重做）
"""
