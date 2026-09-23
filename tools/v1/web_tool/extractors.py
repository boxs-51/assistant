from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

try:
    import trafilatura
except ImportError:
    trafilatura = None

from .config import (
    MAX_CHART_DATASETS,
    MAX_CHART_LABEL_CHARS,
    MAX_CHART_POINTS_TOTAL,
    MAX_CHARTS,
    MAX_TABLE_CELL_CHARS,
    MAX_TABLE_CELLS_TOTAL,
    MAX_TABLE_COLUMNS,
    MAX_TABLE_ROWS,
    MAX_TABLES,
)
from .errors import WebToolError
from .utils import clean_whitespace, deduplicate_content

NOISE_TAGS = [
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "noscript",
    "svg",
    "form",
    "aside",
    "iframe",
    "dialog",
]


def extract_clean_content(
    raw_html: str,
    base_url: str,
    clean_noise: bool = True,
    deduplicate: bool = True,
) -> tuple[Optional[str], str]:
    if not raw_html:
        return None, "none"

    processed_html = raw_html
    if clean_noise:
        try:
            soup = BeautifulSoup(raw_html, "html.parser")
            for tag in soup.find_all(NOISE_TAGS):
                tag.decompose()
            processed_html = str(soup)
        except Exception:
            processed_html = raw_html

    if trafilatura is not None:
        try:
            extracted = trafilatura.extract(
                processed_html,
                url=base_url,
                output_format="markdown",
                include_links=True,
                include_images=False,
                include_tables=True,
            )
        except Exception:
            extracted = None
        if extracted and extracted.strip():
            result = clean_whitespace(extracted)
            if deduplicate:
                result = deduplicate_content(result)
            return result, "trafilatura"

    try:
        soup = BeautifulSoup(processed_html, "html.parser")
        for anchor in soup.find_all("a", href=True):
            href = anchor.get("href")
            if not isinstance(href, str):
                continue
            text = anchor.get_text(strip=True)
            full = urljoin(base_url, href.strip())
            if text and full.startswith(("http://", "https://")):
                anchor.replace_with(f"[{text}]({full})")
        result = clean_whitespace(soup.get_text(separator="\n"))
        if deduplicate:
            result = deduplicate_content(result)
        return (result or None), "beautifulsoup"
    except Exception:
        return None, "beautifulsoup"


def _bounded_text(value: Any, maximum: int) -> str:
    text = clean_whitespace(str(value or ""))
    return text[:maximum]


def _bounded_table(table: Any, total_cells: list[int]) -> list[list[str]]:
    matrix: list[list[str]] = []
    rows = table.find_all("tr", limit=MAX_TABLE_ROWS + 1)
    if len(rows) > MAX_TABLE_ROWS:
        raise WebToolError(
            "WEB_STRUCTURED_DATA_LIMIT",
            "structured table row limit exceeded",
            details={"max_rows": MAX_TABLE_ROWS},
        )
    for row in rows:
        output_row: list[str] = []
        cells = row.find_all(["td", "th"], limit=MAX_TABLE_COLUMNS + 1)
        if len(cells) > MAX_TABLE_COLUMNS:
            raise WebToolError(
                "WEB_STRUCTURED_DATA_LIMIT",
                "structured table column limit exceeded",
                details={"max_columns": MAX_TABLE_COLUMNS},
            )
        for cell in cells:
            if total_cells[0] >= MAX_TABLE_CELLS_TOTAL:
                raise WebToolError(
                    "WEB_STRUCTURED_DATA_LIMIT",
                    "structured table cell limit exceeded",
                    details={"max_cells": MAX_TABLE_CELLS_TOTAL},
                )
            output_row.append(
                _bounded_text(cell.get_text(" ", strip=True), MAX_TABLE_CELL_CHARS)
            )
            total_cells[0] += 1
        if output_row:
            matrix.append(output_row)
    return matrix


def _bound_charts(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list):
        return []
    charts: list[dict[str, Any]] = []
    total_points = 0
    if len(raw) > MAX_CHARTS:
        raise WebToolError(
            "WEB_STRUCTURED_DATA_LIMIT",
            "structured chart count limit exceeded",
            details={"max_charts": MAX_CHARTS},
        )
    for chart in raw:
        if not isinstance(chart, dict):
            continue
        labels = chart.get("labels")
        if not isinstance(labels, list):
            labels = []
        bounded_labels = [
            _bounded_text(label, MAX_CHART_LABEL_CHARS)
            for label in labels[:MAX_CHART_POINTS_TOTAL]
        ]

        datasets_out: list[dict[str, Any]] = []
        datasets = chart.get("datasets")
        if not isinstance(datasets, list):
            datasets = []
        if len(datasets) > MAX_CHART_DATASETS:
            raise WebToolError(
                "WEB_STRUCTURED_DATA_LIMIT",
                "structured chart dataset limit exceeded",
                details={"max_datasets": MAX_CHART_DATASETS},
            )
        for dataset in datasets:
            if not isinstance(dataset, dict):
                continue
            data = dataset.get("data")
            if not isinstance(data, list):
                data = []
            remaining = MAX_CHART_POINTS_TOTAL - total_points
            if remaining <= 0:
                raise WebToolError(
                    "WEB_STRUCTURED_DATA_LIMIT",
                    "structured chart point limit exceeded",
                    details={"max_points": MAX_CHART_POINTS_TOTAL},
                )
            if len(data) > remaining:
                raise WebToolError(
                    "WEB_STRUCTURED_DATA_LIMIT",
                    "structured chart point limit exceeded",
                    details={"max_points": MAX_CHART_POINTS_TOTAL},
                )
            bounded_data = []
            for point in data:
                if isinstance(point, (str, int, float, bool)) or point is None:
                    bounded_data.append(point)
                else:
                    bounded_data.append(str(point)[:MAX_CHART_LABEL_CHARS])
            total_points += len(bounded_data)
            datasets_out.append(
                {
                    "label": _bounded_text(
                        dataset.get("label", ""),
                        MAX_CHART_LABEL_CHARS,
                    ),
                    "data": bounded_data,
                }
            )

        charts.append(
            {
                "type": _bounded_text(chart.get("type", ""), 128),
                "labels": bounded_labels,
                "datasets": datasets_out,
            }
        )
    return charts


async def extract_tables_and_charts(
    html: str,
    page_obj: Any = None,
) -> dict[str, Any]:
    if not isinstance(html, str):
        raise WebToolError(
            "WEB_EXTRACTION_FAILED",
            "structured extraction requires HTML text",
        )

    charts: list[dict[str, Any]] = []
    if page_obj is not None:
        script = """
        () => {
            if (typeof window.Chart === 'undefined') return [];
            return Object.values(window.Chart.instances || {}).map(c => ({
                type: c.config?.type || c.type || '',
                labels: c.data?.labels || [],
                datasets: (c.data?.datasets || []).map(d => ({
                    label: d.label || '',
                    data: d.data || []
                }))
            }));
        }
        """
        try:
            charts = _bound_charts(await page_obj.evaluate(script))
        except WebToolError:
            raise
        except Exception:
            charts = []

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as exc:
        raise WebToolError(
            "WEB_EXTRACTION_FAILED",
            "HTML could not be parsed for structured data",
            details={"exception_type": type(exc).__name__},
        ) from exc

    total_cells = [0]
    tables: list[list[list[str]]] = []
    table_nodes = soup.find_all("table", limit=MAX_TABLES + 1)
    if len(table_nodes) > MAX_TABLES:
        raise WebToolError(
            "WEB_STRUCTURED_DATA_LIMIT",
            "structured table count limit exceeded",
            details={"max_tables": MAX_TABLES},
        )
    for table in table_nodes:
        matrix = _bounded_table(table, total_cells)
        if matrix:
            tables.append(matrix)

    return {"tables": tables, "charts": charts}
