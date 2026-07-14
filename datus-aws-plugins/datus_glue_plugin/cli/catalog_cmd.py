"""`datus glue catalog ...` — Data Catalog: databases, tables, partitions, stats."""

from __future__ import annotations

import argparse

from datus_aws_common import (
    UsageError,
    add_output_option,
    call,
    confirm,
    paginate,
    parse_json_arg,
    render_one,
    render_rows,
)


def _cat(ctx) -> dict:
    return {"CatalogId": ctx.settings.catalog_id} if ctx.settings.catalog_id else {}


def register(sub: argparse._SubParsersAction) -> None:
    catalog = sub.add_parser("catalog", help="Glue Data Catalog: databases, tables, partitions")
    group = catalog.add_subparsers(dest="subcommand", required=True, metavar="<subcommand>")

    p = group.add_parser("databases", help="list databases")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_databases)

    p = group.add_parser("tables", help="list tables in a database")
    p.add_argument("database")
    p.add_argument("-e", "--expression", help="table-name filter expression")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_tables)

    p = group.add_parser("show", help="show a table's schema")
    p.add_argument("database")
    p.add_argument("table")
    add_output_option(p)
    p.set_defaults(func=cmd_show)

    p = group.add_parser("search", help="search tables across the catalog")
    p.add_argument("keyword")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_search)

    p = group.add_parser("partitions", help="list a table's partitions")
    p.add_argument("database")
    p.add_argument("table")
    p.add_argument("-e", "--expression", help="partition filter expression")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_partitions)

    p = group.add_parser("versions", help="list a table's versions")
    p.add_argument("database")
    p.add_argument("table")
    p.add_argument("--limit", type=int)
    add_output_option(p)
    p.set_defaults(func=cmd_versions)

    p = group.add_parser("stats", help="column statistics for a table")
    p.add_argument("database")
    p.add_argument("table")
    p.add_argument("-c", "--column", action="append", dest="columns", help="column (repeatable; default: all)")
    add_output_option(p)
    p.set_defaults(func=cmd_stats)

    p = group.add_parser("create-database", help="create a database")
    p.add_argument("name")
    p.add_argument("--description", default="")
    p.set_defaults(func=cmd_create_database)

    p = group.add_parser("delete-database", help="delete a database (and all its tables)")
    p.add_argument("name")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete_database)

    p = group.add_parser("create-table", help="create a table from a TableInput JSON")
    p.add_argument("database")
    p.add_argument("--table-input", required=True, help="Glue TableInput as JSON")
    p.set_defaults(func=cmd_create_table)

    p = group.add_parser("update-table", help="update a table from a TableInput JSON")
    p.add_argument("database")
    p.add_argument("--table-input", required=True, help="Glue TableInput as JSON")
    p.set_defaults(func=cmd_update_table)

    p = group.add_parser("delete-table", help="delete a table")
    p.add_argument("database")
    p.add_argument("table")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete_table)

    p = group.add_parser("add-partition", help="add a partition from a PartitionInput JSON")
    p.add_argument("database")
    p.add_argument("table")
    p.add_argument("--partition-input", required=True, help="Glue PartitionInput as JSON")
    p.set_defaults(func=cmd_add_partition)

    p = group.add_parser("delete-partition", help="delete a partition by its values")
    p.add_argument("database")
    p.add_argument("table")
    p.add_argument("--values", required=True, help="JSON array of partition values")
    p.add_argument("-y", "--yes", action="store_true")
    p.set_defaults(func=cmd_delete_partition)


# ----------------------------------------------------------------- reads


def cmd_databases(ctx, ns) -> int:
    client = ctx.client("glue")
    rows = paginate(client, "get_databases", "DatabaseList", limit=ns.limit, **_cat(ctx))
    print(render_rows(rows, ["Name", "Description", "LocationUri"], ns.output))
    return 0


def cmd_tables(ctx, ns) -> int:
    client = ctx.client("glue")
    kwargs = dict(_cat(ctx), DatabaseName=ns.database)
    if ns.expression:
        kwargs["Expression"] = ns.expression
    rows = paginate(client, "get_tables", "TableList", limit=ns.limit, **kwargs)
    print(render_rows(rows, ["Name", "TableType", "UpdateTime"], ns.output))
    return 0


def cmd_show(ctx, ns) -> int:
    client = ctx.client("glue")
    table = call(client.get_table, DatabaseName=ns.database, Name=ns.table, **_cat(ctx))["Table"]
    if ns.output in ("json", "yaml"):
        print(render_one(table, ns.output))
        return 0
    sd = table.get("StorageDescriptor", {})
    info = {
        "Database": table.get("DatabaseName") or ns.database,
        "Table": table.get("Name"),
        "Type": table.get("TableType"),
        "Location": sd.get("Location"),
        "InputFormat": sd.get("InputFormat"),
        "Serde": sd.get("SerdeInfo", {}).get("SerializationLibrary"),
    }
    print(render_one(info, ns.output))
    cols = [{"Column": c["Name"], "Type": c.get("Type"), "Comment": c.get("Comment", "")}
            for c in sd.get("Columns", [])]
    cols += [{"Column": f"{c['Name']} (partition)", "Type": c.get("Type"), "Comment": c.get("Comment", "")}
             for c in table.get("PartitionKeys", [])]
    print()
    print(render_rows(cols, ["Column", "Type", "Comment"], ns.output))
    return 0


def cmd_search(ctx, ns) -> int:
    client = ctx.client("glue")
    rows = paginate(client, "search_tables", "TableList", limit=ns.limit, SearchText=ns.keyword, **_cat(ctx))
    print(render_rows(rows, ["DatabaseName", "Name", "TableType"], ns.output))
    return 0


def cmd_partitions(ctx, ns) -> int:
    client = ctx.client("glue")
    kwargs = dict(_cat(ctx), DatabaseName=ns.database, TableName=ns.table)
    if ns.expression:
        kwargs["Expression"] = ns.expression
    rows = paginate(client, "get_partitions", "Partitions", limit=ns.limit, **kwargs)
    if ns.output in ("json", "yaml"):
        print(render_rows(rows, None, ns.output))
        return 0
    view = [{"Values": p.get("Values"), "Location": p.get("StorageDescriptor", {}).get("Location")} for p in rows]
    print(render_rows(view, ["Values", "Location"], ns.output))
    return 0


def cmd_versions(ctx, ns) -> int:
    client = ctx.client("glue")
    rows = paginate(
        client, "get_table_versions", "TableVersions", limit=ns.limit,
        DatabaseName=ns.database, TableName=ns.table, **_cat(ctx),
    )
    if ns.output in ("json", "yaml"):
        print(render_rows(rows, None, ns.output))
        return 0
    view = [{"VersionId": v.get("VersionId"), "UpdateTime": v.get("Table", {}).get("UpdateTime")} for v in rows]
    print(render_rows(view, ["VersionId", "UpdateTime"], ns.output))
    return 0


def cmd_stats(ctx, ns) -> int:
    client = ctx.client("glue")
    columns = ns.columns
    if not columns:
        table = call(client.get_table, DatabaseName=ns.database, Name=ns.table, **_cat(ctx))["Table"]
        columns = [c["Name"] for c in table.get("StorageDescriptor", {}).get("Columns", [])]
    if not columns:
        raise UsageError("table has no columns to fetch statistics for")
    resp = call(
        client.get_column_statistics_for_table,
        DatabaseName=ns.database, TableName=ns.table, ColumnNames=columns, **_cat(ctx),
    )
    stats = resp.get("ColumnStatisticsList", [])
    if ns.output in ("json", "yaml"):
        print(render_rows(stats, None, ns.output))
        return 0
    print(render_rows(stats, ["ColumnName", "Type"], ns.output))
    return 0


# ----------------------------------------------------------------- writes


def cmd_create_database(ctx, ns) -> int:
    client = ctx.client("glue")
    call(client.create_database, DatabaseInput={"Name": ns.name, "Description": ns.description}, **_cat(ctx))
    print(f"created database {ns.name}")
    return 0


def cmd_delete_database(ctx, ns) -> int:
    if not confirm(f"delete database {ns.name!r} and ALL its tables?", ns.yes):
        print("aborted")
        return 1
    call(ctx.client("glue").delete_database, Name=ns.name, **_cat(ctx))
    print(f"deleted database {ns.name}")
    return 0


def cmd_create_table(ctx, ns) -> int:
    table_input = parse_json_arg(ns.table_input, "--table-input")
    call(ctx.client("glue").create_table, DatabaseName=ns.database, TableInput=table_input, **_cat(ctx))
    print(f"created table {ns.database}.{table_input.get('Name')}")
    return 0


def cmd_update_table(ctx, ns) -> int:
    table_input = parse_json_arg(ns.table_input, "--table-input")
    call(ctx.client("glue").update_table, DatabaseName=ns.database, TableInput=table_input, **_cat(ctx))
    print(f"updated table {ns.database}.{table_input.get('Name')}")
    return 0


def cmd_delete_table(ctx, ns) -> int:
    if not confirm(f"delete table {ns.database}.{ns.table}?", ns.yes):
        print("aborted")
        return 1
    call(ctx.client("glue").delete_table, DatabaseName=ns.database, Name=ns.table, **_cat(ctx))
    print(f"deleted table {ns.database}.{ns.table}")
    return 0


def cmd_add_partition(ctx, ns) -> int:
    partition_input = parse_json_arg(ns.partition_input, "--partition-input")
    resp = call(
        ctx.client("glue").batch_create_partition,
        DatabaseName=ns.database, TableName=ns.table, PartitionInputList=[partition_input], **_cat(ctx),
    )
    errors = resp.get("Errors", [])
    if errors:
        raise UsageError(f"partition not added: {errors[0].get('ErrorDetail', {}).get('ErrorMessage', errors)}")
    print(f"added partition to {ns.database}.{ns.table}")
    return 0


def cmd_delete_partition(ctx, ns) -> int:
    values = parse_json_arg(ns.values, "--values")
    if not isinstance(values, list):
        raise UsageError("--values must be a JSON array of partition values")
    if not confirm(f"delete partition {values} from {ns.database}.{ns.table}?", ns.yes):
        print("aborted")
        return 1
    call(
        ctx.client("glue").batch_delete_partition,
        DatabaseName=ns.database, TableName=ns.table, PartitionsToDelete=[{"Values": values}], **_cat(ctx),
    )
    print(f"deleted partition {values} from {ns.database}.{ns.table}")
    return 0
