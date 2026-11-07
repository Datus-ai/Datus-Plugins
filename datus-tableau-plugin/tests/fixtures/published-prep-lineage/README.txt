Preserved redacted native definitions and workbook Metadata from the live Tableau
0.1.2 bound sync on 2026-11-07. Native and Metadata bytes retain the hashes
recorded in context.json; the full original snapshot remains in test evidence.
All underlying orders are synthetic. Source bindings are explicit test mappings.

Tests reconstruct the NEW datasource-scoped Metadata response from the known
output datasource/flow/table stable IDs. That adapter response is synthetic;
the original 0.1.2 sync did not query datasource-scoped lineage. A fresh live
sync is required to validate that API response. No credentials or extract rows
are included.
