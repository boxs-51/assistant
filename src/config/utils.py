from typing import Dict, Any

def parse_nested_keys(data: Dict[str, str], separator: str = "__") -> Dict[str, Any]:
    """
    Chuyển đổi một dict phẳng với các key chứa separator thành một dict lồng nhau.
    Ví dụ: {"GATEWAY__PORT": "8001"} -> {"gateway": {"port": "8001"}}
    """
    result: Dict[str, Any] = {}
    for key, value in data.items():
        parts = key.lower().split(separator)
        d = result
        for part in parts[:-1]:
            if part not in d:
                d[part] = {}
            d = d[part]
        d[parts[-1]] = value
    return result