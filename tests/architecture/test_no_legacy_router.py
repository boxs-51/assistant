from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2] / "src"


def _source_files():
    return [path for path in SRC_ROOT.rglob("*.py") if "__pycache__" not in path.parts]


def test_legacy_model_router_facade_is_removed_from_source():
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        if "LegacyModelRouterFacade" in text or "legacy_model_router" in text:
            offenders.append(str(path))
    assert not offenders, f"Legacy router references remain: {offenders}"


def test_legacy_transport_router_imports_are_removed():
    offenders = []
    for path in _source_files():
        text = path.read_text(encoding="utf-8")
        # Chỉ bắt các dòng import thực tế thay vì bắt toàn bộ chuỗi
        for line in text.splitlines():
            line_str = line.strip()
            if not line_str.startswith("#") and "transport.gateway.router" in line_str:
                offenders.append(str(path))
                break
    assert not offenders, f"Legacy router imports remain: {offenders}"


def test_canonical_v1_router_modules_exist():
    required = {
        "auth_router.py",
        "agent_router.py",
        "tool_router.py",
        "events_router.py",
        "multi_agent_router.py",
        "chat_router.py",
        "embeddings_router.py",
        "models_router.py",
        "files_router.py",
        "health_router.py",
        "admin.py",
    }
    actual = {path.name for path in (SRC_ROOT / "transport/gateway/api/v1").glob("*.py")}
    assert required <= actual
