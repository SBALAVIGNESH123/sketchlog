import re
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
                col = func_match.group(2).strip()
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
        
    def add_row(self, row: Dict[str, Any]) -> None:
        """Ingest a dictionary row, hashing it to the correct sketch group."""
        if self.plan["group_by"]:
            key = tuple(row.get(col) for col in self.plan["group_by"])
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
        if self.plan["group_by"]:
            items = self.groups.items()
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
        """Safely evaluate the having condition using a restricted eval environment."""
        expr = condition
        # basic translate SQL '=' to Python '=='
        expr = re.sub(r'(?<![<>=!])=(?![=])', '==', expr)
        
        for k, v in row.items():
            # Replace alias occurrences with their string value representation
            expr = re.sub(rf'\b{re.escape(k)}\b', str(v), expr)
            
        try:
            # We enforce a strict character set to prevent execution of arbitrary code
            if not re.match(r'^[0-9\.\s\+\-\*\/\>\<\=\!\(\)]+$', expr):
                return False
            return eval(expr, {"__builtins__": {}}, {})
        except Exception:
            return False
