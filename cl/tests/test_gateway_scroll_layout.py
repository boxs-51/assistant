import re
from pathlib import Path


WEB_ROOT = Path(__file__).parents[1] / "src" / "ui" / "web"


def _rule(source: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{(?P<body>.*?)\}}", source, re.DOTALL)
    assert match is not None, f"Missing CSS rule for {selector}"
    return match.group("body")


def test_sidebar_pages_keep_flex_layout_when_tabs_change():
    index = (WEB_ROOT / "index.html").read_text(encoding="utf-8")
    sidebar = (WEB_ROOT / "js" / "components" / "sidebar.js").read_text(encoding="utf-8")

    assert 'id="page-explorer" class="sidebar-page" style="display: flex;"' in index
    assert "page.id === `page-${pageName}` ? 'flex' : 'none'" in sidebar


def test_gateway_scroll_container_has_a_bounded_flex_height():
    sidebar_css = (WEB_ROOT / "css" / "sidebar.css").read_text(encoding="utf-8")
    gateway_css = (WEB_ROOT / "css" / "gateway.css").read_text(encoding="utf-8")

    assert "min-height: 0" in _rule(sidebar_css, ".sidebar-content")
    assert "min-height: 0" in _rule(sidebar_css, ".sidebar-page")

    gateway_panel = _rule(gateway_css, ".gateway-panel")
    assert "flex: 1 1 0" in gateway_panel
    assert "min-height: 0" in gateway_panel
    assert "overflow-y: auto" in gateway_panel


def test_gateway_bridge_forwards_runtime_arguments_without_apply_dispatch():
    gateway_panel = (WEB_ROOT / "js" / "components" / "gatewayPanel.js").read_text(
        encoding="utf-8"
    )

    assert "const response = await target(...args);" in gateway_panel
    assert "target.apply(window.pywebview.api, args)" not in gateway_panel
    assert "callBridge('execute_tool', state.selectedTool.name, args)" in gateway_panel
