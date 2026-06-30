import pytest
from sketchlog import StreamLog
from sketchlog.sql import SQLParser, SQLStreamEngine, execute_stream_query

def test_sql_parser():
    q1 = "SELECT p99(latency) AS p99_lat, unique_count(user_id) FROM stream GROUP BY endpoint, method HAVING p99_lat > 100"
    parser = SQLParser(q1)
    plan = parser.parse()

    assert plan["from"] == "stream"
    assert plan["group_by"] == ["endpoint", "method"]
    assert plan["having"] == "p99_lat > 100"

    selects = plan["selects"]
    assert len(selects) == 2
    assert selects[0] == {"type": "agg", "func": "p99", "col": "latency", "alias": "p99_lat"}
    assert selects[1] == {"type": "agg", "func": "unique_count", "col": "user_id", "alias": "unique_count(user_id)"}

def test_sql_parser_no_group_by():
    q2 = "SELECT event_count(*) AS total FROM mystream"
    parser = SQLParser(q2)
    plan = parser.parse()

    assert plan["from"] == "mystream"
    assert plan["group_by"] == []
    assert plan["having"] is None

    assert plan["selects"] == [{"type": "agg", "func": "event_count", "col": "*", "alias": "total"}]

def test_sql_stream_engine():
    q = "SELECT p99(latency) AS p99_lat, unique_count(user_id) AS users FROM stream GROUP BY endpoint HAVING p99_lat > 50"
    engine = SQLStreamEngine(q)

    # row 1: /api/v1 (latency 10, user A) -> will not pass HAVING
    engine.add_row({"endpoint": "/api/v1", "latency": 10.0, "user_id": "user_a"})
    # row 2: /api/v1 (latency 20, user B)
    engine.add_row({"endpoint": "/api/v1", "latency": 20.0, "user_id": "user_b"})

    # row 3: /api/v2 (latency 100, user C) -> will pass HAVING
    engine.add_row({"endpoint": "/api/v2", "latency": 100.0, "user_id": "user_c"})
    # row 4: /api/v2 (latency 200, user C)
    engine.add_row({"endpoint": "/api/v2", "latency": 200.0, "user_id": "user_c"})

    results = engine.execute_query()

    # We expect only /api/v2 because p99 > 50
    assert len(results) == 1
    res = results[0]
    assert res["endpoint"] == "/api/v2"
    assert res["users"] == 1  # user_c twice
    assert res["p99_lat"] > 190.0  # approximate 200

def test_sql_stream_engine_having_eq():
    q = "SELECT unique_count(user_id) AS users FROM stream GROUP BY endpoint HAVING users = 2"
    engine = SQLStreamEngine(q)

    # 2 users for /a
    engine.add_row({"endpoint": "/a", "user_id": "u1"})
    engine.add_row({"endpoint": "/a", "user_id": "u2"})

    # 1 user for /b
    engine.add_row({"endpoint": "/b", "user_id": "u3"})

    results = engine.execute_query()
    assert len(results) == 1
    assert results[0]["endpoint"] == "/a"
    assert results[0]["users"] == 2


def test_documented_live_stream_grammar():
    log = StreamLog(deterministic=True)
    log.add_batch([10.0, 20.0, 100.0])
    log.add_unique("alice")
    log.add_event("HTTP_500", 3)

    p99 = SQLParser('SELECT p99(latency) FROM "default/my-service"').parse()
    uniques = SQLParser(
        'SELECT count_unique(users) FROM "default/my-service"').parse()
    events = SQLParser(
        "SELECT event_count(errors, 'HTTP_500') FROM \"default/my-service\""
    ).parse()

    assert execute_stream_query(p99, log)["p99(latency)"] > 90
    assert execute_stream_query(uniques, log)["count_unique(users)"] == 1
    assert execute_stream_query(events, log)[
        "event_count(errors, 'HTTP_500')"] == 3


def test_having_rejects_executable_ast_nodes():
    engine = SQLStreamEngine(
        "SELECT event_count(*) AS total FROM stream "
        "HAVING __import__('os').system('echo unsafe') = 0"
    )
    engine.add_row({})
    assert engine.execute_query() == []


def test_parser_handles_whitespace_and_quoted_keywords_linearly():
    query = (
        " SELECT  p99(latency)  AS tail\n"
        " FROM \"default/from stream\"  GROUP   BY endpoint "
        " HAVING tail > 10 "
    )
    plan = SQLParser(query).parse()
    assert plan["from"] == '"default/from stream"'
    assert plan["group_by"] == ["endpoint"]
    assert plan["having"] == "tail > 10"

    with pytest.raises(ValueError, match="4096"):
        SQLParser("SELECT " + ("x" * 4096) + " FROM stream")
