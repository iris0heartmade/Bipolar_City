"""兼容旧参考入口：公式仅在游戏共用模块中维护。"""
from backend.systems.v21.industry_model import (
    Capacity, attractiveness, capacity, clipped, employment_target,
    fraction, integer_count, labor_month, load_parameters, match,
    nonnegative, quota, validate_parameters,
)
