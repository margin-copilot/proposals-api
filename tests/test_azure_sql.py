"""AzureSqlPriceBook against a fake driver: parameters only, a fresh connection per query."""

from decimal import Decimal

import pytest
from margin_copilot_shared.security.context import RequestContext, request_scope
from margin_copilot_shared.security.tokens import Caller

from proposals_api.adapters.outbound.azure_sql import (
    ACCESS_TOKEN,
    SQL_SCOPES,
    AzureSqlPriceBook,
    PriceBookUnavailable,
)
from proposals_api.application.proposals import Product

USER = RequestContext(Caller("north-mgr", "tid", "action-agent", frozenset()), token="user-token")


class FakeObo:
    def __init__(self):
        self.calls = []

    def exchange(self, user_token, scopes):
        self.calls.append((user_token, list(scopes)))
        return "sql-token"


class FakeConnection:
    def __init__(self, rows, error=None):
        self.rows, self.error, self.executed, self.closed = rows, error, [], False
        self.timeout = 0

    def cursor(self):
        return self

    def execute(self, sql, params):
        self.executed.append((sql, params))
        if self.error:
            raise self.error

    def fetchmany(self, size):
        return self.rows

    def close(self):
        self.closed = True


class FakeDriver:
    def __init__(self, rows, error=None):
        self.rows, self.error, self.connections = rows, error, []

    def __call__(self, dsn, attrs_before, timeout):
        connection = FakeConnection(self.rows, self.error)
        connection.attrs = attrs_before
        self.connections.append(connection)
        return connection


def price_book(driver, obo=None):
    return AzureSqlPriceBook("sql.example", "riad", obo or FakeObo(), connect=driver)


async def test_product_search_escapes_wildcards():
    driver = FakeDriver([("DAI-002", "UHT milk 1 L", "B")])
    with request_scope(USER):
        found = await price_book(driver).find("50%_[x\\")
    sql, params = driver.connections[0].executed[0]
    assert params == ("%50\\%\\_\\[x\\\\%",)
    assert "LIKE ? ESCAPE '\\'" in sql
    assert found == [Product("DAI-002", "UHT milk 1 L", "B")]


async def test_each_query_opens_a_fresh_connection_with_the_users_token():
    driver, obo = FakeDriver([(Decimal("7.00"),)]), FakeObo()
    book = price_book(driver, obo)
    with request_scope(USER):
        assert await book.current_price("DAI-002", "N") == Decimal("7.00")
        await book.current_price("DAI-002", "N")
    assert obo.calls == [("user-token", SQL_SCOPES)] * 2
    assert len(driver.connections) == 2
    assert all(c.closed and ACCESS_TOKEN in c.attrs for c in driver.connections)
    sql, params = driver.connections[0].executed[0]
    assert "dbo.fact_sales" in sql and params == ("DAI-002", "N")


async def test_a_price_hidden_by_row_level_security_is_none():
    with request_scope(USER):
        assert await price_book(FakeDriver([])).current_price("DAI-002", "S") is None


async def test_query_errors_hide_their_text():
    driver = FakeDriver([], error=RuntimeError("row value 42 leaked"))
    with request_scope(USER), pytest.raises(PriceBookUnavailable) as failed:
        await price_book(driver).current_price("DAI-002", "N")
    assert "42" not in str(failed.value)
    assert driver.connections[0].closed
