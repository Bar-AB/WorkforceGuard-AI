from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Final

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection, pq
from psycopg import Error as PsycopgError
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool, PoolTimeout
from sqlalchemy import make_url

SSL_MODES: Final = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})
LIBPQ_KEYWORDS: Final = frozenset(option.keyword.decode() for option in pq.Conninfo.get_defaults())
CONNECT_TIMEOUT_SECONDS: Final = 10
CHECKPOINT_POOL_TIMEOUT_SECONDS: Final = 5.0
CHECKPOINT_STATEMENT_TIMEOUT_MS: Final = 5000
KEEPALIVE_SETTINGS: Final = {
    "keepalives": 1,
    "keepalives_idle": 10,
    "keepalives_interval": 5,
    "keepalives_count": 3,
}


class CheckpointerUnavailableError(RuntimeError):
    pass


def _libpq_param(key: str, value: str) -> tuple[str, str]:
    if key == "password":
        raise ValueError("database URL query parameter 'password' is not allowed")
    if key == "ssl":
        if value not in SSL_MODES:
            raise ValueError(f"database URL ssl must be one of {sorted(SSL_MODES)}")
        return "sslmode", value
    if key not in LIBPQ_KEYWORDS:
        raise ValueError(f"database URL query parameter {key!r} is not supported by psycopg")
    return key, value


def _libpq_query(query: Mapping[str, str | tuple[str, ...]]) -> dict[str, str]:
    params: dict[str, str] = {}
    for key, value in query.items():
        if not isinstance(value, str):
            raise ValueError(f"database URL query parameter {key!r} is repeated")
        name, setting = _libpq_param(key, value)
        params[name] = setting
    return params


def psycopg_conninfo(database_url: str) -> str:
    url = make_url(database_url)
    params: dict[str, str | int | None] = {
        "host": url.host,
        "port": url.port,
        "dbname": url.database,
        "user": url.username,
        "password": url.password,
    }
    params.update(_libpq_query(url.query))
    params["connect_timeout"] = CONNECT_TIMEOUT_SECONDS
    return make_conninfo("", **params)


def _redacted(message: str, conninfo: str) -> str:
    password = conninfo_to_dict(conninfo).get("password")
    return message.replace(str(password), "***") if password else message


async def _check_connection(conninfo: str) -> None:
    try:
        connection = await AsyncConnection.connect(conninfo)
    except PsycopgError as error:
        raise CheckpointerUnavailableError(_redacted(str(error), conninfo)) from None
    await connection.close()


async def _open_pool(pool: AsyncConnectionPool[AsyncConnection[DictRow]]) -> None:
    try:
        await pool.open(wait=True, timeout=CONNECT_TIMEOUT_SECONDS)
    except PoolTimeout:
        raise CheckpointerUnavailableError(
            f"checkpointer pool could not connect within {CONNECT_TIMEOUT_SECONDS} s"
        ) from None


@asynccontextmanager
async def open_checkpointer(database_url: str) -> AsyncIterator[AsyncPostgresSaver]:
    conninfo = psycopg_conninfo(database_url)
    await _check_connection(conninfo)
    pool = AsyncConnectionPool(
        conninfo,
        connection_class=AsyncConnection[DictRow],
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c statement_timeout={CHECKPOINT_STATEMENT_TIMEOUT_MS}",
            **KEEPALIVE_SETTINGS,
        },
        min_size=1,
        max_size=5,
        timeout=CHECKPOINT_POOL_TIMEOUT_SECONDS,
        check=AsyncConnectionPool.check_connection,
        open=False,
    )
    try:
        await _open_pool(pool)
        yield AsyncPostgresSaver(pool)
    finally:
        await pool.close()
