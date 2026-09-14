from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from importlib.metadata import entry_points
from pathlib import Path

import pytest
from harlequin.adapter import HarlequinAdapter
from harlequin.exception import HarlequinConfigError

from harlequin_chdb import HarlequinChdbAdapter

ROOT = Path(__file__).resolve().parents[1]


def _run_python(script: str) -> None:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(ROOT / "src") if not existing else str(ROOT / "src") + os.pathsep + existing
    )
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(script)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_plugin_discovery() -> None:
    eps = entry_points(group="harlequin.adapter")
    assert eps["chdb"].load() is HarlequinChdbAdapter
    assert issubclass(HarlequinChdbAdapter, HarlequinAdapter)


def test_connection_id_uses_empty_string_for_memory() -> None:
    assert HarlequinChdbAdapter(()).connection_id == ""
    assert HarlequinChdbAdapter(("/tmp/harlequin-chdb",)).connection_id.startswith("file:")
    assert HarlequinChdbAdapter((), uri="file:/tmp/chdb").connection_id == "file:/tmp/chdb"


def test_rejects_ambiguous_database_location() -> None:
    with pytest.raises(HarlequinConfigError):
        HarlequinChdbAdapter(("/tmp/a",), path="/tmp/b")


def test_select_returns_arrow_table_with_exact_limit() -> None:
    _run_python(
        """
        import pyarrow as pa
        from harlequin_chdb import HarlequinChdbAdapter

        conn = HarlequinChdbAdapter(()).connect()
        cur = conn.execute("SELECT number FROM numbers(1000)")
        assert cur is not None
        table = cur.set_limit(12).fetchall()
        assert isinstance(table, pa.Table)
        assert table.num_rows == 12
        assert table.column_names == ["number"]
        assert table.column("number").to_pylist()[:3] == [0, 1, 2]
        assert cur.columns() == [("number", "u##")]
        conn.close()
        """
    )


def test_zero_limit_returns_schema_only_arrow_table() -> None:
    _run_python(
        """
        import pyarrow as pa
        from harlequin_chdb import HarlequinChdbAdapter

        conn = HarlequinChdbAdapter(()).connect()
        cur = conn.execute("SELECT number AS n, toString(number) AS s FROM numbers(1000)")
        assert cur is not None
        table = cur.set_limit(0).fetchall()
        assert isinstance(table, pa.Table)
        assert table.num_rows == 0
        assert table.column_names == ["n", "s"]
        assert cur.columns() == [("n", "u##"), ("s", "s")]
        conn.close()
        """
    )


def test_pending_read_materializes_before_later_write() -> None:
    _run_python(
        """
        from harlequin_chdb import HarlequinChdbAdapter

        conn = HarlequinChdbAdapter(()).connect()
        conn.execute("CREATE TABLE order_probe (x Int64) ENGINE = MergeTree() ORDER BY x")
        conn.execute("INSERT INTO order_probe VALUES (1)")

        before = conn.execute("SELECT count() AS c FROM order_probe")
        assert before is not None
        before.set_limit(10)

        conn.execute("INSERT INTO order_probe VALUES (2)")
        after = conn.execute("SELECT count() AS c FROM order_probe")
        assert after is not None
        after.set_limit(10)

        assert before.fetchall().to_pylist() == [{"c": 1}]
        assert after.fetchall().to_pylist() == [{"c": 2}]
        conn.close()
        """
    )


def test_catalog_and_search_read_clickhouse_system_tables() -> None:
    _run_python(
        """
        from harlequin.catalog import Catalog
        from harlequin_chdb import HarlequinChdbAdapter

        conn = HarlequinChdbAdapter((), catalog_search_limit=20).connect()
        conn.execute("CREATE TABLE orders (order_id UInt64, customer String) ENGINE = MergeTree() ORDER BY order_id")

        catalog = conn.get_catalog()
        assert isinstance(catalog, Catalog)
        default_db = next(item for item in catalog.items if item.label == "default")
        relations = default_db.fetch_children()
        orders = next(item for item in relations if item.label == "orders")
        assert orders.type_label == "t"
        assert [(c.label, c.type_label) for c in orders.fetch_children()] == [
            ("order_id", "u##"),
            ("customer", "s"),
        ]

        relation_hits = conn.search_catalog("orders", "relations")
        assert [(hit.item.label, hit.parents) for hit in relation_hits] == [
            ("orders", ("default",))
        ]
        column_hits = conn.search_catalog("customer", "columns")
        assert [(hit.item.label, hit.parents) for hit in column_hits] == [
            ("customer", ("default", "orders"))
        ]
        assert all(item.label != "system" for item in catalog.items)
        conn.close()
        """
    )


def test_persistent_path_reopens_existing_database(tmp_path: Path) -> None:
    db_path = tmp_path / "chdb-data"
    _run_python(
        f"""
        from harlequin_chdb import HarlequinChdbAdapter

        conn = HarlequinChdbAdapter(({str(db_path)!r},)).connect()
        conn.execute("CREATE TABLE persistent_probe (x Int64) ENGINE = MergeTree() ORDER BY x")
        conn.execute("INSERT INTO persistent_probe VALUES (42)")
        conn.close()
        """
    )
    _run_python(
        f"""
        from harlequin_chdb import HarlequinChdbAdapter

        conn = HarlequinChdbAdapter(({str(db_path)!r},)).connect()
        cur = conn.execute("SELECT x FROM persistent_probe")
        assert cur is not None
        assert cur.set_limit(1).fetchall().to_pylist() == [{{"x": 42}}]
        conn.close()
        """
    )


def test_read_only_uses_native_adbc_connection_option() -> None:
    _run_python(
        """
        from harlequin.exception import HarlequinQueryError
        from harlequin_chdb import HarlequinChdbAdapter

        conn = HarlequinChdbAdapter((), read_only=True).connect()
        cur = conn.execute("SELECT getSetting('readonly') AS ro")
        assert cur is not None
        assert cur.set_limit(1).fetchall().to_pylist() == [{"ro": 2}]

        try:
            conn.execute("CREATE TABLE ro_denied (x Int64) ENGINE = MergeTree() ORDER BY x")
        except HarlequinQueryError as exc:
            assert "readonly" in str(exc).lower()
        else:
            raise AssertionError("read-only connection accepted a CREATE TABLE")
        conn.close()
        """
    )
