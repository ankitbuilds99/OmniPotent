"""ROI Dashboard module.

Dollar value calculation, before/after comparison, benchmarks,
executive reports, and predictive ROI.
"""

from app.roi.calculator import (
    ROICalculator,
    ROIMetrics,
    ValueBreakdown,
    get_roi_calculator,
)
from app.roi.benchmarks import (
    Benchmark,
    BenchmarkComparison,
    BenchmarkEngine,
    get_benchmark_engine,
)
from app.roi.reports import (
    ExecutiveReport,
    ReportFormat,
    ReportGenerator,
    get_report_generator,
)

__all__ = [
    "Benchmark",
    "BenchmarkComparison",
    "BenchmarkEngine",
    "ExecutiveReport",
    "ReportFormat",
    "ReportGenerator",
    "ROICalculator",
    "ROIMetrics",
    "ValueBreakdown",
    "get_benchmark_engine",
    "get_report_generator",
    "get_roi_calculator",
]
