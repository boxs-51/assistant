from .config import ObservabilityConfig, LoggingConfig, TracingConfig
from .logging import init_logging
from .tracing import init_tracing
from ..observability import metrics as metrics_module

def init_observability(config: ObservabilityConfig, custom_metrics_class=None):
    """
    Hàm khởi tạo duy nhất cho toàn bộ hệ thống Observability.
    """
    # 1. Khởi tạo Log
    init_logging(config.logging)
    
    # 2. Khởi tạo Trace
    init_tracing(config)
    
    # 3. Khởi tạo Metrics (Sử dụng Base hoặc Class custom của service con)
    metrics_cls = custom_metrics_class or metrics_module.BaseMetrics
    metrics_module.metrics = metrics_cls(namespace=config.service_name.replace("-", "_"))
    
    return metrics_module.metrics