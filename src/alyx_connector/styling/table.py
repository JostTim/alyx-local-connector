from .base import BaseStyler, Style, Rule, Element, noescape

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Mapping, Tuple

Row = Mapping[str, Any]
CellKey = Tuple[int, str]  # (row_index, column_name)


@dataclass(frozen=True)
class TableRule(Rule):
    rule_type = "table"
    fn: Callable[[], Style]
    # no value needed, always return a Style


@dataclass(frozen=True)
class HeaderRule(Rule):
    rule_type = "header"
    fn: Callable[[int, int, str], Style]
    # icol (col index) irow (row index) col (col name)


@dataclass(frozen=True)
class RowRule(Rule):
    rule_type = "row"
    fn: Callable[[int, Row], Style]
    # irow (row index) row (mapping of values for the row)


@dataclass(frozen=True)
class CellRule(Rule):
    rule_type = "cell"
    fn: Callable[[int, int, str, Any, Row], Style]
    # icol (col index) irow (row index) col (col name) value (cell value)
    # row (mapping of values for the rest of the row)


class TableStyler(BaseStyler):
    _rows: List[Row]
    _columns: List[str]
    _headers: List[dict[str, int]]
    _rules: List[Rule]
    _table_id: Optional[str]
    _caption: Optional[str]
    _title: Optional[str]

    def __init__(
        self,
        rows: Sequence[Row],
        columns: Optional[Sequence[str]] = None,
        headers: Optional[Sequence[Dict[str, int]]] = None,
        *,
        table_id: Optional[str] = None,
        caption: Optional[str] = None,
        title: Optional[str] = None,
    ):
        super().__init__()

        self._rows = list(rows)
        self._columns = list(columns) if columns is not None else self._infer_columns(self._rows)
        self._headers = (
            list(headers) if headers is not None else self._infer_headers(self._columns)
        )
        self._table_id = table_id
        self._caption = caption
        self._title = title

    @staticmethod
    def _infer_columns(rows: Sequence[Row]) -> List[str]:
        cols: List[str] = []
        seen = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    cols.append(k)
        return cols

    @staticmethod
    def _infer_headers(columns: List[str]) -> List[Dict[str, int]]:
        header: dict[str, int] = {}
        for c in columns:
            header[c] = 1
        return [header]

    def set_caption(self, caption: str) -> "TableStyler":
        self._caption = caption
        return self

    def set_table_attributes(self, *, table_id: Optional[str] = None) -> "TableStyler":
        self._table_id = table_id
        return self

    def add_table_rule(self, fn: Callable[[], Style]) -> "TableStyler":
        self._rules.append(TableRule(fn))
        return self

    def add_row_rule(self, fn: Callable[[int, Row], Style]) -> "TableStyler":
        self._rules.append(RowRule(fn))
        return self

    def add_header_rule(self, fn: Callable[[int, int, str], Style]) -> "TableStyler":
        self._rules.append(HeaderRule(fn))
        return self

    def add_cell_rule(self, fn: Callable[[int, int, str, Any, Row], Style]) -> "TableStyler":
        self._rules.append(CellRule(fn))
        return self

    def zebra(self, even: str = "#ffffff", odd: str = "#fafafa") -> "TableStyler":
        def row(i: int, r: Row) -> Style:
            return Style.css(background=(odd if i % 2 else even))

        return self.add_row_rule(row)

    def monospace_columns(self, cols: Iterable[str]) -> "TableStyler":
        cols = set(cols)

        def cell(ic: int, ir: int, col: str, v: Any, row: Row) -> Style:
            if col in cols:
                return Style.css(
                    font_family='ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace'
                )
            return Style()

        return self.add_cell_rule(cell)

    def highlight_max(
        self, cols: Optional[Iterable[str]] = None, color: str = "#ffffdd"
    ) -> "TableStyler":

        target_cols = list(cols) if cols is not None else list(self._columns)
        max_by_col: Dict[str, Any] = {}
        for c in target_cols:
            vals = [r.get(c) for r in self._rows]
            nums = [v for v in vals if isinstance(v, (int, float))]
            if nums:
                max_by_col[c] = max(nums)

        def cell(ic: int, ir: int, col: str, v: Any, row: Row) -> Style:
            if col in max_by_col and v == max_by_col[col]:
                return Style.css(background=color)
            return Style()

        return self.add_cell_rule(cell)

    # convenience helpers
    def highlight_null(self, color: str = "#ffffff") -> "TableStyler":
        def cell(ic: int, ir: int, col: str, value: Any, row: Row) -> Style:
            if value is None or value == "":
                return Style.css(background=color)
            return Style()

        return self.add_cell_rule(cell)

    def compose(self) -> Element:

        root = super().compose()
        table = Element("table", style=self.style_from_rules("table"))
        root.add_content(
            Element(
                "div", style=Style().add_attr(id=self._unique_id).add_cls("file-table-container")
            ).add_content(table)
        )

        if self._caption:
            table.add_content(Element("caption", content=[self._caption]))

        if self._title:
            table.add_content(
                Element("thead").add_content(
                    Element(
                        "th",
                        style=Style().add_cls("title").add_attr(colspan=str(len(self._columns))),
                    ).add_content(
                        self._title,
                        Element(
                            "button",
                            style=Style().add_attr(id="toggle-headers-btn"),
                            content=[noescape("&#x25BC;")],
                        ),
                    )
                )
            )

        heads = Element("thead", style=Style().add_cls("head"))
        table.add_content(heads)
        for row_idx, header_row in enumerate(self._headers):
            tr = Element("tr", style=Style().add_cls("foldable-header") if row_idx else Style())
            heads.add_content(tr)
            for col_idx, (col, span) in enumerate(header_row.items()):
                th_style = self.style_from_rules("header", col_idx, row_idx, col)
                if row_idx == 0:
                    th_style = th_style.add_attr(rowspan=str(span))
                else:
                    th_style = th_style.add_attr(colspan=str(span))
                tr.add_content(Element("th", content=[col], style=th_style))

        tbody = Element("tbody")
        table.add_content(tbody)
        for row_idx, row in enumerate(self._rows):
            tr_syle = self.style_from_rules("row", row_idx, row)
            tr = Element("tr", style=tr_syle)
            tbody.add_content(tr)
            for c in self._columns:
                value = row[c]
                td_syle = self.style_from_rules("cell", col_idx, row_idx, col, value, row)
                tr.add_content(Element("td", content=[value], style=td_syle))

        return root

    @property
    def css(self) -> str:
        css = (
            super().css
            + """
            .file-table-container{
                border: 1px solid rgb(173, 173, 173);
                border-radius: 5px;
                overflow: hidden;
                display: inline-block;
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue",
                    Arial, "Noto Sans", "Liberation Sans", sans-serif, "Apple Color Emoji",
                    "Segoe UI Emoji", "Segoe UI Symbol", "Noto Color Emoji";
                font-size: 14px;
                line-height: 1.4;
            }
            .file-table-container .title{
                text-align: left;
                white-space: pre;
                font-weight: bolder;
                position:relative;
            }
            .file-table-container table {
                border-collapse: collapse;
            }
            .file-table-container th, .file-table-container td {
                border: 1px solid rgb(160 160 160);
            }
            .file-table-container thead:first-of-type th {
                border-top: none;
            }
            .file-table-container td {
                border-bottom: 0px;
            }
            .file-table-container th:first-of-type, .file-table-container td:first-of-type {
                border-left: none;
            }
            .file-table-container td:last-of-type, .file-table-container th:last-of-type {
                border-right: none;
            }
            .file-table-container thead > tr > th:hover, .file-table-container tbody > tr > td:hover{
                background-color: rgba(111, 110, 160, 0.267);
            }
            .file-table-container #toggle-headers-btn {
                position:absolute;
                right:8px;
                top:4px;
                font-size:0.9em;
            }
            .file-table-container .foldable-header {
                display: none;
                transition: display 0.2s;
            }
            .file-table-container .foldable-header.visible {
                display: table-row;
            }
            .file-table-container #toggle-headers-btn {
                background: none;
                border: none;
                cursor: pointer;
                padding: 0 4px;
                color: #444;
                transition: color 0.2s;
            }
            .file-table-container #toggle-headers-btn:hover {
                color: #222;
            }
        """
        )
        return css

    @property
    def js(self):
        js = (
            super().js
            + """
            // This function is called on the container that is injected, and only on that one
            // (using the unique id system)
            function initFileTable(containerId) {
                const container = document.getElementById(containerId);
                if (!container) throw new Error(`Container not found: ${containerId}`);

                const btn = container.querySelector('#toggle-headers-btn');
                const rows = container.querySelectorAll('.foldable-header');

                if (!btn) throw new Error(`Toggle button not found in: ${containerId}`);

                let expanded = false;

                function setRows(show) {
                    rows.forEach(row => row.classList.toggle('visible', show));
                    btn.innerHTML = show ? '&#x25B2;' : '&#x25BC;';
                    btn.setAttribute('aria-expanded', String(show));
                }

                btn.addEventListener('click', () => {
                    expanded = !expanded;
                    setRows(expanded);
                });

                setRows(false);
            }

            initFileTable('{{ unique_id }}');
        """
        )
        return js
