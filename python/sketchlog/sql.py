import ast
import re
import threading
from typing import Any, Dict, List, Tuple, Optional

from .facade import StreamLog

class SQLParser:
    """Minimal SQL parser tailored for Streaming Sketches without external dependencies."""

    def __init__(self, query: str):
        stripped = query.strip()
        if len(stripped) > 4096:
            raise ValueError("SQL query exceeds 4096 characters")
        self.query = self._normalize_whitespace(stripped)

    @staticmethod
    def _normalize_whitespace(text: str) -> str:
        """Collapse SQL whitespace outside quoted identifiers/literals."""
        output: List[str] = []
        quote: Optional[str] = None
        pending_space = False
        index = 0
        while index < len(text):
            char = text[index]
            if quote is not None:
                output.append(char)
                if char == quote:
                    if index + 1 < len(text) and text[index + 1] == quote:
                        output.append(text[index + 1])
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if char in ("'", '"'):
                if pending_space and output:
                    output.append(" ")
                pending_space = False
                quote = char
                output.append(char)
            elif char.isspace():
                pending_space = True
            else:
                if pending_space and output:
                    output.append(" ")
                pending_space = False
                output.append(char)
            index += 1
        return "".join(output)

    @staticmethod
    def _find_keyword(text: str, keyword: str, start: int = 0) -> int:
        """Find a SQL keyword outside quotes and parentheses in linear time."""
        target = keyword.upper()
        upper = text.upper()
        quote: Optional[str] = None
        depth = 0
        index = start
        while index < len(text):
            char = text[index]
            if quote is not None:
                if char == quote:
                    if index + 1 < len(text) and text[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if char in ("'", '"'):
                quote = char
                index += 1
                continue
            if char == "(":
                depth += 1
                index += 1
                continue
            if char == ")":
                if depth == 0:
                    raise ValueError("Unbalanced SQL parentheses")
                depth -= 1
                index += 1
                continue
            if depth == 0 and upper.startswith(target, index):
                before_ok = index == 0 or text[index - 1].isspace()
                end = index + len(target)
                after_ok = end == len(text) or text[end].isspace()
                if before_ok and after_ok:
                    return index
            index += 1
        if quote is not None or depth != 0:
            raise ValueError("Unbalanced SQL expression")
        return -1

    @staticmethod
    def _split_top_level(text: str) -> List[str]:
        """Split comma-separated expressions without regex backtracking."""
        parts: List[str] = []
        quote: Optional[str] = None
        depth = 0
        start = 0
        index = 0
        while index < len(text):
            char = text[index]
            if quote is not None:
                if char == quote:
                    if index + 1 < len(text) and text[index + 1] == quote:
                        index += 2
                        continue
                    quote = None
                index += 1
                continue
            if char in ("'", '"'):
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    raise ValueError("Unbalanced SQL parentheses")
                depth -= 1
            elif char == "," and depth == 0:
                part = text[start:index].strip()
                if not part:
                    raise ValueError("Empty SQL expression")
                parts.append(part)
                start = index + 1
            index += 1
        if quote is not None or depth != 0:
            raise ValueError("Unbalanced SQL expression")
        final = text[start:].strip()
        if not final:
            raise ValueError("Empty SQL expression")
        parts.append(final)
        return parts

    def parse(self) -> Dict[str, Any]:
        if not self.query[:6].upper() == "SELECT":
            raise ValueError("Query must start with SELECT")
        if len(self.query) == 6 or not self.query[6].isspace():
            raise ValueError("SELECT must be followed by an expression")

        from_index = self._find_keyword(self.query, "FROM", 7)
        if from_index < 0:
            raise ValueError("Query must contain FROM")
        select_clause = self.query[6:from_index].strip()
        from_start = from_index + len("FROM")
        group_index = self._find_keyword(self.query, "GROUP BY", from_start)
        having_index = self._find_keyword(self.query, "HAVING", from_start)
        if (group_index >= 0 and having_index >= 0
                and having_index < group_index):
            raise ValueError("HAVING must follow GROUP BY")

        from_end_candidates = [
            index for index in (group_index, having_index) if index >= 0]
        from_end = min(from_end_candidates, default=len(self.query))
        from_clause = self.query[from_start:from_end].strip()
        group_by_clause: Optional[str] = None
        if group_index >= 0:
            group_start = group_index + len("GROUP BY")
            group_end = having_index if having_index >= 0 else len(self.query)
            group_by_clause = self.query[group_start:group_end].strip()
        having_clause = (
            self.query[having_index + len("HAVING"):].strip()
            if having_index >= 0 else None
        )
        if not select_clause or not from_clause:
            raise ValueError("SELECT and FROM clauses must not be empty")
        if group_index >= 0 and not group_by_clause:
            raise ValueError("GROUP BY must not be empty")
        if having_index >= 0 and not having_clause:
            raise ValueError("HAVING must not be empty")

        selects = self._parse_select(select_clause)
        group_by = (
            self._split_top_level(group_by_clause)
            if group_by_clause else []
        )
        having = having_clause.strip() if having_clause else None

        return {
            "selects": selects,
            "from": from_clause.strip(),
            "group_by": group_by,
            "having": having
        }

    def _parse_select(self, clause: str) -> List[Dict[str, Any]]:
        parts = self._split_top_level(clause)
        selects = []
        for part in parts:
            part = part.strip()
            alias_index = self._find_keyword(part, "AS")
            if alias_index >= 0:
                expr = part[:alias_index].strip()
                alias = part[alias_index + len("AS"):].strip()
                if not expr or not alias:
                    raise ValueError("AS requires an expression and alias")
            else:
                expr = part
                alias = part

            open_paren = expr.find("(")
            if open_paren > 0 and expr.endswith(")"):
                func = expr[:open_paren].strip().lower()
                if not func.replace("_", "a").isalnum():
                    raise ValueError("Invalid aggregate function name")
                if func == "count_unique":
                    func = "unique_count"
                if func not in {"p99", "p95", "p50", "unique_count", "event_count"}:
                    raise ValueError(f"Unsupported aggregate function: {func}")
                col = expr[open_paren + 1:-1].strip()
                if not col:
                    raise ValueError(f"{func} requires an argument")
                if func == "event_count":
                    args = self._split_top_level(col)
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
