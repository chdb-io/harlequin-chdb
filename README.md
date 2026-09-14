# harlequin-chdb

[![CI](https://github.com/chdb-io/harlequin-chdb/actions/workflows/ci.yml/badge.svg)](https://github.com/chdb-io/harlequin-chdb/actions/workflows/ci.yml)

`harlequin-chdb` is a Harlequin adapter for chDB, the in-process ClickHouse
engine. It lets Harlequin run ClickHouse SQL locally without a ClickHouse
server.

## Status

This is the first local implementation draft for the standalone
`chdb-io/harlequin-chdb` package. It targets:

- Harlequin `>=2.13,<3`
- chDB `>=4.4.0`
- chdb-core `>=26.7.3`
- Python `>=3.10`

The `chdb-core>=26.7.3` floor is intentional: the adapter relies on the ADBC
read-only connection option, statement cancellation, and statement-level
bounded reads released there.

## Install

```bash
pip install harlequin-chdb
```

For local development from this checkout:

```bash
cd harlequin-chdb
uv run --with-editable . --extra dev pytest
```

Run the same local checks as CI:

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests --statistics
python -m pytest -q
python -m build
```

## Releases

Releases are published from version tags. For example, `v0.1.0` builds the
wheel and source distribution, validates them with Twine, uploads them to
PyPI, and attaches the artifacts to the matching GitHub Release.

See [Architecture](docs/architecture.md) for the adapter design and data path.

## Usage

Start an in-memory chDB database:

```bash
harlequin -a chdb
```

Start a persistent local database:

```bash
harlequin -a chdb /path/to/chdb-data
```

or:

```bash
harlequin -a chdb --path /path/to/chdb-data
```

Pass a chDB ADBC URI directly:

```bash
harlequin -a chdb --uri 'file:/path/to/chdb-data?progress=off'
```

Show system databases in the catalog:

```bash
harlequin -a chdb --show-system
```

Use Harlequin read-only mode:

```bash
harlequin -a chdb --read-only /path/to/chdb-data
```

## Implementation Notes

Harlequin calls `connection.execute(sql)` before it calls
`cursor.set_limit(n)`. chDB ADBC bounded reads must be configured before the
statement executes. To use the native bounded-read support without breaking SQL
ordering, this adapter:

- classifies SQL with chDB's Python query classifier;
- delays read-only statements until `fetchall()`, after Harlequin has supplied
  the limit;
- applies `max_block_size`, `max_result_rows`, and
  `result_overflow_mode='break'` before executing a limited statement;
- materializes earlier pending reads before running a later mutating/control
  statement, preserving statement order inside one Harlequin run.

chDB allows one storage path per Python process. Tests that need different
engine modes or read-only transitions run in subprocesses.

Query results use chDB's ADBC Arrow path. The adapter returns `pyarrow.Table`
objects to Harlequin and avoids converting result sets through pandas or
row-wise Python objects.

## License

Apache-2.0; see [LICENSE](LICENSE).
