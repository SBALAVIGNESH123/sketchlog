import ast
import re
import threading
from typing import Any, Dict, List, Tuple, Optional

from sketchlog.facade import StreamLog

class SQLParser:
    """Minimal SQL parser tailored for Streaming Sketches without external dependencies."""

    def __init__(self, query: str):
        self.query = query.strip()

    def parse(self) -> Dict[str, Any]:
        pattern = re.compile(
            r"^SELECT\s+(.*?)\s+FROM\s+(.*?)"
            r"(?:\s+GROUP\s+BY\s+(.*?))?"
            r"(?:\s+HAVING\s+(.*?))?$",
            re.IGNORECASE | re.DOTALL
        )
        match = pattern.match(self.query)
        if not match:
            raise ValueError(f"Invalid SQL query format or unsupported syntax: {self.query}")

        select_clause, from_clause, group_by_clause, having_clause = match.groups()

        selects = self._parse_select(select_clause)
        group_by = [g.strip() for g in group_by_clause.split(',')] if group_by_clause else []
        having = having_clause.strip() if having_clause else None

        return {
            "selects": selects,
            "from": from_clause.strip(),
            "group_by": group_by,
            "having": having
        }

    def _parse_select(self, clause: str) -> List[Dict[str, Any]]:
        # Split by comma, but not commas inside parentheses
        parts = re.split(r",\s*(?![^()]*\))", clause)
        selects = []
        for part in parts:
            part = part.strip()
            # check for AS
            alias_match = re.search(r"^(.*?)\s+AS\s+(.+)$", part, re.IGNORECASE)
            if alias_match:
                expr = alias_match.group(1).strip()
                alias = alias_match.group(2).strip()
            else:
                expr = part
                alias = part

            # check for func(col)
            func_match = re.match(r"^(\w+)\((.*?)\)$", expr)
            if func_match:
                func = func_match.group(1).lower()
                if func == "count_unique":
                    func = "unique_count"
                if func not in {"p99", "p95", "p50", "unique_count", "event_count"}:
                    raise ValueError(f"Unsupported aggregate function: {func}")
                col = func_match.group(2).strip()
                if func == "event_count":
                    args = [arg.strip() for arg in re.split(
                        r",(?=(?:[^']*'[^']*')*[^']*$)", col)]
                    if len(args) == 2:
                        col = args[1].strip("'\"")
                    elif len(args) != 1:
                        raise ValueError("event_count accepts one key or (column, key)")
                selects.append({"type": "agg", "func": func, "col": col, "alias": alias})
            else:
                selects.append({"type": "col", "col": expr, "alias": alias})

        return selects


class SQLStreamEngine:
    """SQL execution engine that builds sketches dynamically based on the parsed query."""

    def __init__(self, query: str, sk_kwargs: Optional[Dict[str, Any]] = None):
        self.parser = SQLParser(query)
        self.plan = self.parser.parse()
        self.sk_kwargs = sk_kwargs or {}
        self.groups: Dict[Tuple[Any, ...], StreamLog] = {}
        self.global_log = StreamLog(**self.sk_kwargs)
        self._lock = threading.RLock()

    def add_row(self, row: Dict[str, Any]) -> None:
        """Ingest a dictionary row, hashing it to the correct sketch group."""
        if self.plan["group_by"]:
            key = tuple(row.get(col) for col in self.plan["group_by"])
            with self._lock:
                if key not in self.groups:
                    self.groups[key] = StreamLog(**self.sk_kwargs)
                log = self.groups[key]
        else:
            log = self.global_log

        # extract values based on selects
        for sel in self.plan["selects"]:
            if sel["type"] == "agg":
                col = sel["col"]
                func = sel["func"]
                if func in ["p99", "p95", "p50"]:
                    val = row.get(col)
                    if val is not None:
                        log.add_latency(float(val))
                elif func == "unique_count":
                    val = row.get(col)
                    if val is not None:
                        log.add_unique(val)
                elif func == "event_count":
                    if col == "*":
                        log.add_event("*", 1)
                    else:
                        val = row.get(col)
                        if val is not None:
                            log.add_event(str(val), 1)

    def execute_query(self) -> List[Dict[str, Any]]:
        """Evaluate the sketches and return the SQL result set."""
        results = []
        items: List[Tuple[Tuple[Any, ...], StreamLog]] = []

        with self._lock:
            if self.plan["group_by"]:
                items = list(self.groups.items())
            else:
                items = [((), self.global_log)]

        for key, log in items:
            row_out = {}
            if self.plan["group_by"]:
                for i, col in enumerate(self.plan["group_by"]):
                    row_out[col] = key[i]

            for sel in self.plan["selects"]:
                alias = sel["alias"]
                if sel["type"] == "agg":
                    func = sel["func"]
                    col = sel["col"]
                    if func == "p99":
                        row_out[alias] = log.p99()
                    elif func == "p95":
                        row_out[alias] = log.p95()
                    elif func == "p50":
                        row_out[alias] = log.p50()
                    elif func == "unique_count":
                        row_out[alias] = log.unique_count()
                    elif func == "event_count":
                        if col == "*":
                            row_out[alias] = log.total_events
                        else:
                            if col in row_out:
                                row_out[alias] = log.event_count(str(row_out[col]))
                            else:
                                row_out[alias] = log.total_events
                elif sel["type"] == "col":
                    if alias not in row_out:
                        row_out[alias] = None

            # eval having
            if self.plan["having"]:
                if not self._eval_having(self.plan["having"], row_out):
                    continue

            results.append(row_out)

        return results

    def _eval_having(self, condition: str, row: Dict[str, Any]) -> bool:
        """Evaluate a bounded expression AST without code execution."""
        expression = re.sub(r'(?<![<>=!])=(?![=])', '==', condition)
        try:
            tree = ast.parse(expression, mode="eval")
            return bool(self._evaluate_node(tree.body, row))
        except (SyntaxError, TypeError, ValueError, KeyError, ZeroDivisionError):
            return False

    def _evaluate_node(self, node: ast.AST, row: Dict[str, Any]) -> Any:
        if isinstance(node, ast.Constant) and isinstance(
                node.value, (str, int, float, bool)):
            return node.value
        if isinstance(node, ast.Name):
            return row[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = self._evaluate_node(node.operand, row)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BoolOp) and isinstance(node.op, (ast.And, ast.Or)):
            values = [bool(self._evaluate_node(value, row)) for value in node.values]
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.BinOp) and isinstance(
                node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left = self._evaluate_node(node.left, row)
            right = self._evaluate_node(node.right, row)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            return left / right
        if isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators):
            left = self._evaluate_node(node.left, row)
            for operation, comparator in zip(node.ops, node.comparators):
                right = self._evaluate_node(comparator, row)
                if isinstance(operation, ast.Eq):
                    result = left == right
                elif isinstance(operation, ast.NotEq):
                    result = left != right
                elif isinstance(operation, ast.Lt):
                    result = left < right
                elif isinstance(operation, ast.LtE):
                    result = left <= right
                elif isinstance(operation, ast.Gt):
                    result = left > right
                elif isinstance(operation, ast.GtE):
                    result = left >= right
                else:
                    raise ValueError("Unsupported HAVING comparison")
                if not result:
                    return False
                left = right
            return True
        raise ValueError("Unsupported HAVING expression")


def execute_stream_query(plan: Dict[str, Any], stream: StreamLog) -> Dict[str, Any]:
    """Execute a parsed aggregate query against one existing StreamLog."""
    if plan["group_by"]:
        raise ValueError("GROUP BY is available only in embedded row-ingestion mode")
    result: Dict[str, Any] = {}
    for selection in plan["selects"]:
        if selection["type"] != "agg":
            raise ValueError("Live stream queries support aggregate expressions only")
        function = selection["func"]
        column = selection["col"]
        if function == "p99":
            value: Any = stream.p99()
        elif function == "p95":
            value = stream.p95()
        elif function == "p50":
            value = stream.p50()
        elif function == "unique_count":
            value = stream.unique_count()
        elif function == "event_count":
            value = stream.total_events if column == "*" else stream.event_count(column)
        else:
            raise ValueError(f"Unsupported aggregate function: {function}")
        result[selection["alias"]] = value

    if plan["having"]:
        evaluator = SQLStreamEngine("SELECT event_count(*) FROM placeholder")
        if not evaluator._eval_having(plan["having"], result):
            return {}
    return result
