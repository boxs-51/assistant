import re
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None

from .utils import clean_whitespace, deduplicate_content

NOISE_TAGS = ["script", "style", "nav", "footer", "header", "noscript", "svg", "form", "aside", "iframe", "dialog"]
NOISE_PATTERN = re.compile(
    r'ad[s]?|cookie|banner|header|footer|nav|sidebar|social|share|popup|widget|modal|consent|cookie-consent|newsletter|subscribe|promo',
    re.IGNORECASE
)

async def extract_tables_and_charts(page_or_html: Any, page_obj: Any = None) -> Dict[str, Any]:
    """Trích xuất bảng và biểu đồ Canvas/ChartJS từ trang web."""
    data: Dict[str, Any] = {"tables": [], "charts": []}
    if page_obj:
        js_script = """
        () => {
            if (typeof window.Chart === 'undefined') return [];
            return Object.values(window.Chart.instances || {}).map(c => ({
               type: c.config?.type || c.type,
               labels: c.data?.labels || [],
               datasets: (c.data?.datasets || []).map(d => ({ label: d.label, data: d.data }))
            }));
        }"""
        try:
            data["charts"] = await page_obj.evaluate(js_script)
        except Exception:
            pass

    soup = BeautifulSoup(page_or_html if isinstance(page_or_html, str) else page_obj.content(), "html.parser")
    for table in soup.find_all("table"):
        grid = {}
        rows = table.find_all("tr")
        for r_idx, tr in enumerate(rows):
            c_idx = 0
            for cell in tr.find_all(["td", "th"]):
                while (r_idx, c_idx) in grid: c_idx += 1
                r_span, c_span = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
                txt = clean_whitespace(cell.get_text(" ", strip=True))
                for r in range(r_idx, r_idx + r_span):
                    for c in range(c_idx, c_idx + c_span): grid[(r, c)] = txt
                c_idx += c_span
        if grid:
            max_r, max_c = max(r for r, c in grid.keys()) + 1, max(c for r, c in grid.keys()) + 1
            mat = [["" for _ in range(max_c)] for _ in range(max_r)]
            for (r, c), val in grid.items(): mat[r][c] = val
            data["tables"].append(mat)
    return data

def extract_clean_content(
    raw_html: str,
    base_url: str,
    clean_noise: bool = True,
    deduplicate: bool = True
) -> Optional[str]:
    """Sử dụng Trafilatura hoặc BeautifulSoup để trích xuất nội dung văn bản chính với lọc rác và khử trùng lặp."""
    if not raw_html:
        return None

    processed_html = raw_html
    if clean_noise:
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
            for tag in soup.find_all(NOISE_TAGS):
                tag.decompose()
            for element in soup.find_all(True, class_=NOISE_PATTERN):
                element.decompose()
            for element in soup.find_all(True, id=NOISE_PATTERN):
                element.decompose()
            processed_html = str(soup)
        except Exception:
            processed_html = raw_html

    extracted_text = None
    if trafilatura:
        extracted_text = trafilatura.extract(
            processed_html,
            url=base_url,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
        )

    if not extracted_text or not extracted_text.strip():
        try:
            soup = BeautifulSoup(processed_html, "html.parser")

            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                text_link = a.get_text(strip=True)
                full_url = urljoin(base_url, href)
                if text_link and full_url.startswith(("http://", "https://")):
                    a.replace_with(f"[{text_link}]({full_url})")

            extracted_text = soup.get_text(separator="\n")
        except Exception:
            return None

    result = clean_whitespace(extracted_text)
    if deduplicate and result:
        result = deduplicate_content(result)

    return result