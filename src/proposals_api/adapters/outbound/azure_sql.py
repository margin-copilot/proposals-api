"""PriceBook on Azure SQL, read as the signed-in user: each query exchanges the caller's token
(OBO) and opens its own connection with pooling off, so row-level security applies and no
connection is shared across users. Parameters only; error text never leaves (it can carry rows)."""

import asyncio
import logging
import struct
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from margin_copilot_shared.security.context import current_request
from margin_copilot_shared.security.obo import OboClient

from proposals_api.application.proposals import Product

SQL_SCOPES = ["https://database.windows.net/.default"]
ACCESS_TOKEN = 1256  # SQL_COPT_SS_ACCESS_TOKEN
# The free-offer database is resuming from auto-pause (40613); mssql-python 1.17.0 reports it
# without the number, so the message text counts too.
WAKING = ("40613", "is not currently available")
WAKE_DELAYS = (5, 10, 20, 30)
LOGIN_TIMEOUT = 30
QUERY_TIMEOUT = 30
MAX_ROWS = 20
FIND = (
    "SELECT TOP (20) sku, product_name, tier FROM dbo.dim_product "
    "WHERE product_name LIKE ? ESCAPE '\\' ORDER BY product_name"
)
# The shelf price on the last day with sales; row-level security hides other regions' rows.
CURRENT_PRICE = (
    "SELECT TOP (1) shelf_price_mad FROM dbo.fact_sales "
    "WHERE sku = ? AND region_id = ? ORDER BY sale_date DESC"
)
log = logging.getLogger(__name__)


class PriceBookUnavailable(Exception):
    pass


def pack_token(token: str) -> bytes:
    raw = token.encode("utf-16-le")
    return struct.pack(f"<I{len(raw)}s", len(raw), raw)


def escape_like(term: str) -> str:
    for char in ("\\", "%", "_", "["):
        term = term.replace(char, "\\" + char)
    return term


def default_connect() -> Callable[..., Any]:
    import mssql_python  # native driver: only azure mode loads it

    mssql_python.pooling(enabled=False)
    return mssql_python.connect


class AzureSqlPriceBook:
    def __init__(
        self,
        server: str,
        database: str,
        obo: OboClient,
        connect: Callable[..., Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._dsn = f"Server=tcp:{server},1433;Database={database};Encrypt=yes"
        self._obo = obo
        self._connect = connect or default_connect()
        self._sleep = sleep

    async def find(self, term: str) -> list[Product]:
        rows = await self._rows(FIND, (f"%{escape_like(term.strip())}%",))
        return [Product(str(sku), str(name), str(tier)) for sku, name, tier in rows]

    async def current_price(self, sku: str, region: str) -> Decimal | None:
        rows = await self._rows(CURRENT_PRICE, (sku, region))
        return Decimal(str(rows[0][0])) if rows else None

    async def _rows(self, sql: str, params: tuple) -> list[tuple]:
        token = current_request().token
        return await asyncio.to_thread(self._query, token, sql, params)

    def _query(self, user_token: str, sql: str, params: tuple) -> list[tuple]:
        connection = self._open(self._obo.exchange(user_token, SQL_SCOPES))
        try:
            connection.timeout = QUERY_TIMEOUT  # before cursor(): the cursor copies it
            cursor = connection.cursor()
            cursor.execute(sql, params)  # bound by the driver, never spliced into the text
            return [tuple(row) for row in cursor.fetchmany(MAX_ROWS)]
        except Exception as error:
            log.warning("price query failed: %s", type(error).__name__)
            raise PriceBookUnavailable from None
        finally:
            connection.close()

    def _open(self, token: str) -> Any:
        attrs = {ACCESS_TOKEN: pack_token(token)}
        for delay in (*WAKE_DELAYS, None):
            try:
                return self._connect(self._dsn, attrs_before=attrs, timeout=LOGIN_TIMEOUT)
            except Exception as error:
                if delay is None or not any(sign in str(error) for sign in WAKING):
                    log.warning("connect failed: %s: %.300s", type(error).__name__, error)
                    raise PriceBookUnavailable from None
                self._sleep(delay)
