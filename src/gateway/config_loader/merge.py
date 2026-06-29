from typing import Dict, Any

def deep_merge(base: Dict[str, Any], new: Dict[str, Any]) -> Dict[str, Any]:
    """
    Hợp nhất đệ quy `new` dict vào `base` dict.
    - Giá trị từ `new` sẽ ghi đè `base`.
    - Nếu cả hai giá trị cho cùng một key là dict, chúng sẽ được hợp nhất đệ quy.
    - List, tuple, set sẽ được thay thế hoàn toàn.
    """
    merged = base.copy()
    for key, value in new.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged