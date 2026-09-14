from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Sequence

from harlequin.adapter import HarlequinAdapter, HarlequinConnection, HarlequinCursor
from harlequin.autocomplete.completion import HarlequinCompletion
from harlequin.catalog import Catalog, CatalogItem, CatalogSearchKind, CatalogSearchResult
from harlequin.exception import HarlequinConfigError, HarlequinConnectionError, HarlequinQueryError
from harlequin.options import HarlequinAdapterOption, HarlequinCopyFormat

from harlequin_chdb.catalog import (
    ColumnCatalogItem,
    DatabaseCatalogItem,
    RelationCatalogItem,
)
from harlequin_chdb.cli_options import CHDB_OPTIONS
from harlequin_chdb.completions import get_completion_data

if TYPE_CHECKING:
    import pyarrow as pa
    from textual_fastdatatable.backend import AutoBackendType

_MEMORY_URI = "chdb://"
_MEMORY_CLASSIFIER_PATH = ":memory:"
_MEMORY_SENTINELS = {"", "chdb://", ":memory:", "chdb://:memory:", "chdb::memory:"}
_SYSTEM_DATABASES = ("system", "INFORMATION_SCHEMA", "information_schema")


def _as_bool(value: bool | str) -> bool:
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_uri(value: str) -> bool:
    return "://" in value or value.startswith(("chdb:", "file:", "local:"))


def _path_to_uri(value: str | Path) -> str:
    return "file:" + Path(value).expanduser().resolve().as_posix()


def _resolve_uri(
    conn_str: Sequence[str],
    *,
    path: str | Path | None,
    uri: str | None,
) -> str:
    positional = [str(item) for item in conn_str if str(item).strip()]
    explicit = [name for name, value in (("uri", uri), ("path", path)) if value]
    if len(explicit) + len(positional) > 1:
        raise HarlequinConfigError(
            title="Harlequin could not initialize the chDB adapter.",
            msg="Pass only one of a positional database path, --path, or --uri.",
        )

    if uri:
        raw = str(uri)
    elif path:
        raw = str(path)
    elif positional:
        raw = positional[0]
    else:
        raw = _MEMORY_URI

    if raw in _MEMORY_SENTINELS:
        return _MEMORY_URI
    if uri or _is_uri(raw):
        return raw
    return _path_to_uri(raw)


def _classifier_path(uri: str) -> str:
    if uri in _MEMORY_SENTINELS:
        return _MEMORY_CLASSIFIER_PATH
    return uri


def _cancelled(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "cancelled" in text or "canceled" in text


def _database_filter(column: str) -> str:
    blocked = ", ".join(f"'{name}'" for name in _SYSTEM_DATABASES)
    return f"{column} NOT IN ({blocked})"


class ChdbCursor(HarlequinCursor):
    def __init__(self, connection: "HarlequinChdbConnection", query: str) -> None:
        self.connection = connection
        self.query = query
        self._limit: int | None = None
        self._table: pa.Table | None = None
        self._columns: list[tuple[str, str]] | None = None
        self._executed = False
        self._adbc_cursor: Any | None = None

    @classmethod
    def from_table(
        cls, connection: "HarlequinChdbConnection", table: pa.Table
    ) -> "ChdbCursor":
        cursor = cls(connection=connection, query="")
        cursor._table = table
        cursor._columns = connection._columns_from_schema(table.schema)
        cursor._executed = True
        return cursor

    def columns(self) -> list[tuple[str, str]]:
        self._ensure_executed()
        return self._columns or []

    def set_limit(self, limit: int) -> HarlequinCursor:
        self._limit = max(int(limit), 0)
        return self

    def fetchall(self) -> AutoBackendType | None:
        self._ensure_executed()
        return self._table

    def _ensure_executed(self) -> None:
        if self._executed:
            return
        self.connection._materialize_pending_through(self)

    def _execute_locked(self) -> None:
        if self._executed:
            return

        self.connection._discard_pending_locked(self)
        cursor = self.connection._conn.cursor()
        self._adbc_cursor = cursor
        self.connection._active_adbc_cursor = cursor
        try:
            self._apply_limit_options(cursor)
            cursor.execute(self.query)
            if cursor.description:
                table = cursor.fetch_arrow_table()
                if self._limit == 0:
                    table = table.slice(0, 0)
                self._table = table
                self._columns = self.connection._columns_from_schema(table.schema)
            else:
                self._table = None
                self._columns = []
        except Exception as exc:
            if _cancelled(exc):
                self._table = None
                self._columns = []
            else:
                raise HarlequinQueryError(
                    msg=str(exc),
                    title="chDB raised an error when running your query:",
                ) from exc
        finally:
            self._executed = True
            self.connection._active_adbc_cursor = None
            self._adbc_cursor = None
            try:
                cursor.close()
            except Exception:
                pass

    def _apply_limit_options(self, cursor: Any) -> None:
        if self._limit is None:
            return
        # A max_result_rows=0 setting means "unlimited" in ClickHouse. Use one
        # row to obtain the schema, then slice the Arrow table back to zero rows.
        option_limit = max(self._limit, 1)
        cursor.adbc_statement.set_options(
            **{
                "max_block_size": str(option_limit),
                "max_result_rows": str(option_limit),
                "result_overflow_mode": "break",
            }
        )


class HarlequinChdbConnection(HarlequinConnection):
    COLUMN_TYPE_MAPPING = {
        "bool": "t/f",
        "boolean": "t/f",
        "int8": "#",
        "int16": "#",
        "int32": "##",
        "int64": "##",
        "int128": "###",
        "int256": "###",
        "uint8": "u#",
        "uint16": "u#",
        "uint32": "u##",
        "uint64": "u##",
        "uint128": "u##",
        "uint256": "u##",
        "float32": "#.#",
        "float64": "#.#",
        "decimal": "#.#",
        "date": "d",
        "date32": "d",
        "datetime": "ts",
        "datetime64": "ts",
        "timestamp": "ts",
        "string": "s",
        "fixedstring": "s",
        "uuid": "uid",
        "ipv4": "ip",
        "ipv6": "ip",
        "enum8": "enm",
        "enum16": "enm",
        "json": "{}",
        "tuple": "()",
        "map": "{m}",
    }

    def __init__(
        self,
        conn: Any,
        classifier: Any,
        *,
        show_system: bool = False,
        catalog_search_limit: int = 200,
        init_message: str = "",
    ) -> None:
        self._conn = conn
        self._classifier = classifier
        self.show_system = show_system
        self.catalog_search_limit = catalog_search_limit
        self.init_message = init_message
        self._lock = threading.RLock()
        self._pending: list[ChdbCursor] = []
        self._active_adbc_cursor: Any | None = None
        self._closed = False

    def execute(self, query: str) -> HarlequinCursor | None:
        if self._closed:
            raise HarlequinQueryError(
                msg="The chDB connection is closed.",
                title="chDB could not run your query.",
            )

        report = self._classify(query)
        if int(report.get("statement_count", 1)) > 1:
            raise HarlequinQueryError(
                msg="chDB ADBC accepts one statement at a time; submit one statement or let Harlequin split the script.",
                title="chDB could not run your query.",
            )

        if self._is_read_only_statement(report):
            cursor = ChdbCursor(connection=self, query=query)
            with self._lock:
                self._pending.append(cursor)
            return cursor

        with self._lock:
            self._materialize_all_pending_locked()
            return self._execute_immediate_locked(query)

    def cancel(self) -> None:
        active = self._active_adbc_cursor
        if active is not None:
            try:
                active.adbc_cancel()
                return
            except Exception as exc:
                if not _cancelled(exc) and "invalid_state" not in str(exc).lower():
                    raise
        self._conn.adbc_cancel()

    def get_catalog(self) -> Catalog:
        items = [
            DatabaseCatalogItem.from_label(label=name, connection=self)
            for (name,) in self._get_databases()
        ]
        return Catalog(items=items)

    def search_catalog(
        self, term: str, kind: CatalogSearchKind = "all"
    ) -> list[CatalogSearchResult]:
        results: list[CatalogSearchResult] = []
        limit = self.catalog_search_limit
        if kind == "all":
            for (database,) in self._search_databases(term, limit):
                results.append(
                    CatalogSearchResult(
                        item=DatabaseCatalogItem.from_label(database, self)
                    )
                )
        if kind in ("all", "relations"):
            for database, relation, engine in self._search_relations(term, limit):
                database_item = DatabaseCatalogItem.from_label(database, self)
                relation_item = RelationCatalogItem.from_parent(
                    parent=database_item, label=relation, engine=engine
                )
                results.append(CatalogSearchResult(item=relation_item, parents=(database,)))
        if kind in ("all", "columns"):
            for database, relation, engine, column, type_name in self._search_columns(
                term, limit
            ):
                database_item = DatabaseCatalogItem.from_label(database, self)
                relation_item = RelationCatalogItem.from_parent(
                    parent=database_item, label=relation, engine=engine
                )
                results.append(
                    CatalogSearchResult(
                        item=ColumnCatalogItem.from_parent(
                            parent=relation_item,
                            label=column,
                            type_label=self._short_column_type(type_name),
                            type_name=type_name,
                        ),
                        parents=(database, relation),
                    )
                )
        return results

    def get_completions(self) -> list[HarlequinCompletion]:
        return get_completion_data()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._pending.clear()
            if self._active_adbc_cursor is not None:
                try:
                    self._active_adbc_cursor.adbc_cancel()
                except Exception:
                    pass
            self._conn.close()

    def _classify(self, query: str) -> dict[str, Any]:
        try:
            return self._classifier.classify_query(query)
        except Exception as exc:
            raise HarlequinQueryError(
                msg=str(exc),
                title="chDB could not classify your query before running it.",
            ) from exc

    @staticmethod
    def _is_read_only_statement(report: dict[str, Any]) -> bool:
        query_class = report.get("query_class")
        return getattr(query_class, "name", str(query_class).split(".")[-1]) == "READ_ONLY"

    def _execute_immediate_locked(self, query: str) -> HarlequinCursor | None:
        cursor = self._conn.cursor()
        self._active_adbc_cursor = cursor
        try:
            cursor.execute(query)
            if cursor.description:
                table = cursor.fetch_arrow_table()
                return ChdbCursor.from_table(connection=self, table=table)
            return None
        except Exception as exc:
            if _cancelled(exc):
                return None
            raise HarlequinQueryError(
                msg=str(exc),
                title="chDB raised an error when running your query:",
            ) from exc
        finally:
            self._active_adbc_cursor = None
            try:
                cursor.close()
            except Exception:
                pass

    def _materialize_pending_through(self, cursor: ChdbCursor) -> None:
        with self._lock:
            while self._pending:
                current = self._pending[0]
                current._execute_locked()
                if current is cursor:
                    return
            if not cursor._executed:
                cursor._execute_locked()

    def _materialize_all_pending_locked(self) -> None:
        while self._pending:
            self._pending[0]._execute_locked()

    def _discard_pending_locked(self, cursor: ChdbCursor) -> None:
        try:
            self._pending.remove(cursor)
        except ValueError:
            pass

    def _execute_rows(self, query: str, params: Sequence[Any] | None = None) -> list[tuple]:
        with self._lock:
            self._materialize_all_pending_locked()
            cursor = self._conn.cursor()
            self._active_adbc_cursor = cursor
            try:
                cursor.execute(query, params or [])
                return list(cursor.fetchall())
            except Exception as exc:
                if _cancelled(exc):
                    return []
                raise HarlequinQueryError(
                    msg=str(exc),
                    title="chDB raised an error while reading the catalog:",
                ) from exc
            finally:
                self._active_adbc_cursor = None
                try:
                    cursor.close()
                except Exception:
                    pass

    def _get_databases(self) -> list[tuple[str]]:
        where = "1" if self.show_system else _database_filter("name")
        return self._execute_rows(
            f"SELECT name FROM system.databases WHERE {where} ORDER BY name"
        )

    def _get_relations(self, database: str) -> list[tuple[str, str]]:
        return self._execute_rows(
            "SELECT name, engine FROM system.tables "
            f"WHERE database = ? AND ({'1' if self.show_system else _database_filter('database')}) "
            "ORDER BY name",
            [database],
        )

    def _get_columns(self, database: str, relation: str) -> list[tuple[str, str]]:
        return self._execute_rows(
            "SELECT name, type FROM system.columns "
            "WHERE database = ? AND table = ? ORDER BY position",
            [database, relation],
        )

    def _search_databases(self, term: str, limit: int) -> list[tuple[str]]:
        where = "1" if self.show_system else _database_filter("name")
        return self._execute_rows(
            "SELECT name FROM system.databases "
            f"WHERE {where} AND positionCaseInsensitive(name, ?) > 0 "
            "ORDER BY name LIMIT ?",
            [term, limit],
        )

    def _search_relations(self, term: str, limit: int) -> list[tuple[str, str, str]]:
        where = "1" if self.show_system else _database_filter("database")
        return self._execute_rows(
            "SELECT database, name, engine FROM system.tables "
            f"WHERE {where} AND positionCaseInsensitive(name, ?) > 0 "
            "ORDER BY database, name LIMIT ?",
            [term, limit],
        )

    def _search_columns(self, term: str, limit: int) -> list[tuple[str, str, str, str, str]]:
        where = "1" if self.show_system else _database_filter("c.database")
        return self._execute_rows(
            "SELECT c.database, c.table, ifNull(t.engine, 'Table'), c.name, c.type "
            "FROM system.columns AS c "
            "LEFT JOIN system.tables AS t ON t.database = c.database AND t.name = c.table "
            f"WHERE {where} AND positionCaseInsensitive(c.name, ?) > 0 "
            "ORDER BY c.database, c.table, c.position LIMIT ?",
            [term, limit],
        )

    def _columns_from_schema(self, schema: pa.Schema) -> list[tuple[str, str]]:
        return [(field.name, self._short_column_type(field.type)) for field in schema]

    @classmethod
    def _short_column_type(cls, native_type: Any) -> str:
        text = str(native_type).strip()
        lower = text.lower()
        lower = lower.removesuffix(" not null")
        for wrapper in ("nullable", "lowcardinality"):
            prefix = f"{wrapper}("
            if lower.startswith(prefix) and lower.endswith(")"):
                return cls._short_column_type(text[len(prefix) : -1])
        if lower.startswith("array(") and lower.endswith(")"):
            return "[" + cls._short_column_type(text[6:-1])[:1] + "]"
        base = lower.split("(", 1)[0].split("[", 1)[0]
        return cls.COLUMN_TYPE_MAPPING.get(base, "?")


class HarlequinChdbAdapter(HarlequinAdapter):
    ADAPTER_OPTIONS: list[HarlequinAdapterOption] | None = CHDB_OPTIONS
    COPY_FORMATS: list[HarlequinCopyFormat] | None = None
    IMPLEMENTS_CANCEL = True
    IMPLEMENTS_CATALOG_SEARCH = True
    IMPLEMENTS_READ_ONLY = True
    ADAPTER_DETAILS = "A Harlequin adapter for chDB, the in-process ClickHouse engine."

    def __init__(
        self,
        conn_str: Sequence[str],
        read_only: bool | str = False,
        path: Path | str | None = None,
        uri: str | None = None,
        show_system: bool | str = False,
        catalog_search_limit: str | int = 200,
        **_: Any,
    ) -> None:
        try:
            self.uri = _resolve_uri(conn_str, path=path, uri=uri)
            self.read_only = _as_bool(read_only)
            self.show_system = _as_bool(show_system)
            self.catalog_search_limit = int(catalog_search_limit)
        except (TypeError, ValueError) as exc:
            raise HarlequinConfigError(
                msg=f"chDB adapter received bad config value: {exc}",
                title="Harlequin could not initialize the chDB adapter.",
            ) from exc

        if self.catalog_search_limit < 1:
            raise HarlequinConfigError(
                msg="catalog-search-limit must be at least 1.",
                title="Harlequin could not initialize the chDB adapter.",
            )

    @property
    def connection_id(self) -> str | None:
        if self.uri == _MEMORY_URI:
            return ""
        return self.uri

    def connect(self) -> HarlequinChdbConnection:
        try:
            import adbc_driver_chdb.dbapi as chdb_adbc
            import chdb

            conn_kwargs = (
                {"adbc.connection.readonly": "true"} if self.read_only else None
            )
            conn = chdb_adbc.connect(self.uri, conn_kwargs=conn_kwargs)
            classifier = chdb._chdb.connect(_classifier_path(self.uri))
        except Exception as exc:
            raise HarlequinConnectionError(
                msg=str(exc),
                title="Harlequin could not connect to chDB.",
            ) from exc

        return HarlequinChdbConnection(
            conn=conn,
            classifier=classifier,
            show_system=self.show_system,
            catalog_search_limit=self.catalog_search_limit,
            init_message=f"Connected to chDB at {self.uri}.",
        )
