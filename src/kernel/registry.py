
from collections import defaultdict, deque
from typing import Dict, List, Optional

from ..kernel.base import BaseRuntime


class RuntimeRegistry:
    """Quản lý việc đăng ký, lưu trữ instance và metadata của các Runtime."""

    def __init__(self):
        self._runtimes: Dict[str, BaseRuntime] = {}
        self._services: Dict[str, BaseRuntime] = {}  # Infra Services Exported

    def register(self, runtime: BaseRuntime) -> None:
        r_id = runtime.manifest.id
        if r_id in self._runtimes:
            raise ValueError(f"Runtime '{r_id}' đã tồn tại trong Registry!")
        self._runtimes[r_id] = runtime

    def get(self, runtime_id: str) -> Optional[BaseRuntime]:
        return self._runtimes.get(runtime_id)

    def list_all(self) -> List[BaseRuntime]:
        return list(self._runtimes.values())

    def register_service(self, service_name: str, provider: BaseRuntime) -> None:
        self._services[service_name] = provider

    def get_service(self, service_name: str) -> Optional[BaseRuntime]:
        return self._services.get(service_name)

class DependencyResolver:
    """Sử dụng Topological Sort (Kahn's Algorithm) để tính thứ tự Start/Init."""

    @staticmethod
    def resolve_order(runtimes: List[BaseRuntime]) -> List[str]:
        graph: Dict[str, List[str]] = defaultdict(list)
        in_degree: Dict[str, int] = defaultdict(int)
        nodes = {r.manifest.id: r for r in runtimes}

        for r_id in nodes:
            in_degree[r_id] = 0

        for runtime in runtimes:
            r_id = runtime.manifest.id
            for dep in runtime.manifest.dependencies:
                if dep not in nodes:
                    raise KeyError(f"Missing dependency '{dep}' required by '{r_id}'")
                graph[dep].append(r_id)
                in_degree[r_id] += 1

        queue = deque([r_id for r_id in nodes if in_degree[r_id] == 0])
        sorted_order = []

        while queue:
            curr = queue.popleft()
            sorted_order.append(curr)
            for neighbor in graph[curr]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_order) != len(nodes):
            raise ValueError("Phát hiện Vòng lặp phụ thuộc (Circular Dependency) trong các Runtime!")

        return sorted_order