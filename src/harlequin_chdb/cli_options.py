from __future__ import annotations

from pathlib import Path

from harlequin.options import FlagOption, PathOption, TextOption


def _int_validator(raw: str | None) -> tuple[bool, str | None]:
    if raw in (None, ""):
        return True, None
    try:
        value = int(raw)
    except ValueError:
        return False, f"Cannot convert {raw!r} to an integer."
    if value < 1:
        return False, "Value must be at least 1."
    return True, None


uri = TextOption(
    name="uri",
    description=(
        "A chDB ADBC URI, such as 'chdb://' for memory or "
        "'file:/path/to/db?progress=off' for a persistent database. If set, "
        "do not also pass a positional database path or --path."
    ),
)

path = PathOption(
    name="path",
    description=(
        "Path to a persistent local chDB database directory. This is converted "
        "to a file: URI before connecting."
    ),
    short_decls=["-p"],
    exists=False,
    file_okay=False,
    dir_okay=True,
    resolve_path=True,
    path_type=Path,
)

show_system = FlagOption(
    name="show-system",
    description="Show system, INFORMATION_SCHEMA, and information_schema databases in the catalog.",
)

catalog_search_limit = TextOption(
    name="catalog-search-limit",
    description="Maximum number of catalog search matches to return per search branch.",
    default="200",
    validator=_int_validator,
)

CHDB_OPTIONS = [uri, path, show_system, catalog_search_limit]
