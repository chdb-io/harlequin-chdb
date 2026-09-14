# Changelog

## v0.1.0 - 2026-09-14

Initial alpha release of `harlequin-chdb`.

- Add a Harlequin adapter entry point named `chdb`.
- Connect Harlequin to in-process chDB through the chDB ADBC driver.
- Support in-memory and persistent local chDB databases.
- Return query results to Harlequin as PyArrow Tables without converting
  through pandas or row-wise Python objects.
- Support Harlequin read-only mode through chDB's native ADBC connection
  option.
- Delay read-only execution until Harlequin supplies a row limit, then apply
  chDB bounded-read statement options.
- Add catalog browsing and catalog search backed by ClickHouse system tables.
- Add ClickHouse-oriented completions.
- Add Linux/macOS CI for Python 3.10, 3.12, and 3.14.
