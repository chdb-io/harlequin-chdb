from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harlequin.catalog import InteractiveCatalogItem

if TYPE_CHECKING:
    from harlequin_chdb.adapter import HarlequinChdbConnection


def quote_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


@dataclass
class ColumnCatalogItem(InteractiveCatalogItem["HarlequinChdbConnection"]):
    parent: "RelationCatalogItem | None" = None

    @classmethod
    def from_parent(
        cls,
        parent: "RelationCatalogItem",
        label: str,
        type_label: str,
        type_name: str,
    ) -> "ColumnCatalogItem":
        return cls(
            qualified_identifier=f"{parent.qualified_identifier}.{quote_identifier(label)}",
            query_name=quote_identifier(label),
            label=label,
            type_label=type_label,
            type_name=type_name,
            connection=parent.connection,
            parent=parent,
            loaded=True,
        )


@dataclass
class RelationCatalogItem(InteractiveCatalogItem["HarlequinChdbConnection"]):
    parent: "DatabaseCatalogItem | None" = None

    def fetch_children(self) -> list[ColumnCatalogItem]:
        if self.parent is None or self.connection is None:
            return []
        rows = self.connection._get_columns(self.parent.label, self.label)
        return [
            ColumnCatalogItem.from_parent(
                parent=self,
                label=name,
                type_label=self.connection._short_column_type(type_name),
                type_name=type_name,
            )
            for name, type_name in rows
        ]

    @classmethod
    def from_parent(
        cls,
        parent: "DatabaseCatalogItem",
        label: str,
        engine: str,
    ) -> "RelationCatalogItem":
        relation_identifier = f"{parent.qualified_identifier}.{quote_identifier(label)}"
        return cls(
            qualified_identifier=relation_identifier,
            query_name=relation_identifier,
            label=label,
            type_label=_short_relation_type(engine),
            type_name=engine,
            connection=parent.connection,
            parent=parent,
        )


class DatabaseCatalogItem(InteractiveCatalogItem["HarlequinChdbConnection"]):
    @classmethod
    def from_label(
        cls, label: str, connection: "HarlequinChdbConnection"
    ) -> "DatabaseCatalogItem":
        database_identifier = quote_identifier(label)
        return cls(
            qualified_identifier=database_identifier,
            query_name=database_identifier,
            label=label,
            type_label="db",
            type_name="database",
            connection=connection,
        )

    def fetch_children(self) -> list[RelationCatalogItem]:
        if self.connection is None:
            return []
        return [
            RelationCatalogItem.from_parent(parent=self, label=name, engine=engine)
            for name, engine in self.connection._get_relations(self.label)
        ]


def _short_relation_type(engine: str) -> str:
    normalized = engine.lower()
    if normalized == "view":
        return "v"
    if normalized == "materializedview":
        return "mv"
    if normalized == "dictionary":
        return "dic"
    return "t"
