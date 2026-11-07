Downloaded Tableau Cloud Prep regression flow, 2026-11-07.

flow.tfl preserves the downloaded native nodes, transformations, container
ports, FilterOperation expression, GroupBy markers and source field types.
Only PublishExtract destination metadata (server URL, project name/ID and
datasource name) is anonymized; JSON whitespace is normalized. The CSV files
are the synthetic input data used in the native flow.

The regression test plans this flow through the managed CLI and executes the
generated output SELECT in DuckDB. Its Completed predicate excludes the
Cancelled South order, and IFNULL preserves the null-Amount East order's cost
and quantity. Expected totals: Amount 500, Cost 250, NetSales 250, Quantity 10.
