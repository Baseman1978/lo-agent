"""De belangrijkste veiligheidsgrens: nooit schrijven op productiedata."""

import pytest

from span.db.work import ReadOnlyViolation, assert_read_only


@pytest.mark.parametrize(
    "query",
    [
        "CREATE (n:Asset {id: 1})",
        "MATCH (n) SET n.x = 1",
        "MATCH (n) DELETE n",
        "MATCH (n) DETACH DELETE n",
        "MERGE (n:Project {id: 'p1'})",
        "MATCH (n) REMOVE n.x",
        "DROP INDEX foo",
        "LOAD CSV FROM 'file:///x.csv' AS row RETURN row",
        "match (n) set n.sneaky = true",  # lowercase
        "FOREACH (x IN [1] | CREATE (:Hack))",
        "CALL apoc.create.node(['X'], {})",
    ],
)
def test_write_queries_rejected(query):
    with pytest.raises(ReadOnlyViolation):
        assert_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:Project) RETURN n LIMIT 10",
        "MATCH (a:Asset)-[:IN]->(l:Location) RETURN a.name, l.name",
        "MATCH (n) WHERE n.name CONTAINS 'offset' RETURN count(n)",
        "CALL db.labels()",
    ],
)
def test_read_queries_allowed(query):
    assert_read_only(query)  # geen exception
