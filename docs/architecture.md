# Architecture

`harlequin-chdb` is a Harlequin adapter for chDB. Harlequin provides the
terminal SQL IDE; chDB provides an in-process ClickHouse engine. The adapter
connects the two so users can run ClickHouse SQL locally without starting a
ClickHouse server.

## Runtime scope

The v0.1.0 adapter targets:

- Harlequin `>=2.13,<3`
- chDB `>=4.4.0`
- chdb-core `>=26.7.3`
- Python `>=3.10`

The chdb-core floor is intentional. The adapter relies on the chDB ADBC driver
for read-only connections, statement cancellation, and bounded reads.

## Query execution

Harlequin calls `connection.execute(sql)` before it applies the display limit to
the returned cursor. chDB bounded reads must be configured before the statement
runs. To preserve both APIs:

- the adapter classifies SQL with chDB before execution;
- read-only statements are queued until `fetchall()` or `columns()` needs the
  result;
- once Harlequin has supplied a limit, the adapter applies chDB statement
  options such as `max_block_size`, `max_result_rows`, and
  `result_overflow_mode='break'`;
- mutating or control statements first materialize earlier queued reads, so one
  Harlequin run observes statement order correctly.

The adapter rejects multi-statement execution at the chDB ADBC boundary and
lets Harlequin split scripts into single statements.

## Arrow data path

Query results are fetched through chDB ADBC as Arrow data:

```python
table = cursor.fetch_arrow_table()
```

The adapter returns `pyarrow.Table` objects to Harlequin. It does not convert
query result sets through pandas or row-wise Python objects.

This is an Arrow columnar handoff, not a promise that every layer is
end-to-end zero-copy. chDB, ADBC, PyArrow, and Harlequin can each decide how to
own or view buffers internally.

## Catalog and completions

Catalog browsing and search read ClickHouse system tables:

- `system.databases`
- `system.tables`
- `system.columns`

System schemas are hidden by default and can be shown with `--show-system`.

The adapter also provides ClickHouse-oriented completions for common table
functions, clauses, and engine names.

## Connection modes

The adapter supports:

- in-memory chDB via `harlequin -a chdb`;
- persistent local storage via a positional path or `--path`;
- direct chDB ADBC URIs via `--uri`;
- Harlequin read-only mode through chDB's native ADBC read-only connection
  option.

chDB allows one storage path per Python process. Tests that need different
engine modes or read-only transitions run in subprocesses.
