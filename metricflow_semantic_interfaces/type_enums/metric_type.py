from __future__ import annotations

from metricflow_semantic_interfaces.enum_extension import ExtendedEnum


class MetricType(ExtendedEnum):
    """Currently supported metric types."""

    SIMPLE = "simple"  # 简单指标：按定义对源数据聚合。
    RATIO = "ratio"  # 比率指标：分子指标除以分母指标。
    CUMULATIVE = "cumulative"  # 累计指标：按时间窗口或截至当前的范围累计。
    DERIVED = "derived"  # 派生指标：用表达式组合输入指标。
    CONVERSION = "conversion"  # 转化指标：按实体和转化窗口匹配基础事件与转化事件。
