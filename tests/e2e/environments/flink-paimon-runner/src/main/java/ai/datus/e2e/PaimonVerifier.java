package ai.datus.e2e;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.TableEnvironment;
import org.apache.flink.types.Row;
import org.apache.flink.util.CloseableIterator;

/** Read Paimon through an independent TableEnvironment and emit one JSON verdict. */
public final class PaimonVerifier {
    private static final ObjectMapper JSON = new ObjectMapper();
    private PaimonVerifier() {}

    public static void main(String[] args) throws Exception {
        Map<String, String> options = parseArgs(args);
        Map<String, Object> expected = JSON.readValue(options.get("expected"), new TypeReference<>() {});
        TableEnvironment tables = TableEnvironment.create(EnvironmentSettings.newInstance().inBatchMode().build());
        tables.executeSql(String.format(
            "CREATE CATALOG verify_catalog WITH (" +
            "'type'='paimon','warehouse'='%s','s3.endpoint'='%s'," +
            "'s3.access-key'='%s','s3.secret-key'='%s','s3.path.style.access'='true')",
            quote(options.get("warehouse")), quote(options.get("endpoint")),
            quote(options.get("access-key")), quote(options.get("secret-key"))));
        tables.executeSql("USE CATALOG verify_catalog");
        tables.executeSql("USE `" + options.get("database") + "`");
        String table = "`" + options.get("table") + "`";
        List<String> primaryKey = tables.from(table).getResolvedSchema().getPrimaryKey()
            .map(key -> key.getColumns())
            .orElse(List.of());

        List<Map<String, Object>> schema = new ArrayList<>();
        try (CloseableIterator<Row> rows = tables.executeSql("DESCRIBE " + table).collect()) {
            while (rows.hasNext()) {
                Row row = rows.next();
                Map<String, Object> column = new LinkedHashMap<>();
                column.put("name", row.getField(0));
                column.put("type", String.valueOf(row.getField(1)));
                column.put("nullable", Boolean.valueOf(String.valueOf(row.getField(2))));
                column.put("key", row.getArity() > 3 ? String.valueOf(row.getField(3)) : "");
                schema.add(column);
            }
        }

        Row aggregates;
        try (CloseableIterator<Row> rows = tables.executeSql(
                "SELECT COUNT(*), COUNT(DISTINCT id), MIN(id), MAX(id), MIN(source), MAX(source) FROM " + table).collect()) {
            aggregates = rows.next();
        }
        long snapshots;
        try (CloseableIterator<Row> rows = tables.executeSql("SELECT COUNT(*) FROM `" + options.get("table") + "$snapshots`").collect()) {
            snapshots = ((Number) rows.next().getField(0)).longValue();
        }

        Map<String, Object> actual = new LinkedHashMap<>();
        actual.put("schema", schema);
        actual.put("primaryKey", primaryKey);
        actual.put("count", ((Number) aggregates.getField(0)).longValue());
        actual.put("distinctId", ((Number) aggregates.getField(1)).longValue());
        actual.put("minId", ((Number) aggregates.getField(2)).longValue());
        actual.put("maxId", ((Number) aggregates.getField(3)).longValue());
        actual.put("source", List.of(String.valueOf(aggregates.getField(4))));
        actual.put("sourceMax", String.valueOf(aggregates.getField(5)));
        actual.put("snapshots", snapshots);

        @SuppressWarnings("unchecked")
        List<Map<String, Object>> wantedSchema = (List<Map<String, Object>>) expected.get("schema");
        boolean schemaMatches = schema.size() == wantedSchema.size();
        if (schemaMatches) {
            for (int index = 0; index < schema.size(); index++) {
                Map<String, Object> got = schema.get(index);
                Map<String, Object> wanted = wantedSchema.get(index);
                schemaMatches &= String.valueOf(got.get("name")).equals(String.valueOf(wanted.get("name")));
                schemaMatches &= String.valueOf(got.get("type")).equalsIgnoreCase(String.valueOf(wanted.get("type")));
                schemaMatches &= Boolean.valueOf(String.valueOf(got.get("nullable"))).equals(wanted.get("nullable"));
            }
        }
        boolean passed = schemaMatches
            && primaryKey.equals(expected.get("primaryKey"))
            && number(actual, "count") == number(expected, "count")
            && number(actual, "distinctId") == number(expected, "distinctId")
            && number(actual, "minId") == number(expected, "minId")
            && number(actual, "maxId") == number(expected, "maxId")
            && actual.get("source").equals(expected.get("source"))
            && String.valueOf(actual.get("sourceMax")).equals(String.valueOf(((List<?>) expected.get("source")).get(0)))
            && snapshots > 0;
        Map<String, Object> verdict = new LinkedHashMap<>();
        verdict.put("passed", passed);
        verdict.put("actual", actual);
        verdict.put("expected", expected);
        System.out.println("DATUS_E2E_ORACLE=" + JSON.writeValueAsString(verdict));
        if (!passed) {
            System.exit(1);
        }
    }

    private static long number(Map<String, Object> value, String key) {
        return ((Number) value.get(key)).longValue();
    }

    private static String quote(String value) {
        return value.replace("'", "''");
    }

    private static Map<String, String> parseArgs(String[] args) {
        Map<String, String> result = new LinkedHashMap<>();
        for (int index = 0; index < args.length; index += 2) {
            if (index + 1 >= args.length || !args[index].startsWith("--")) {
                throw new IllegalArgumentException("arguments must be --key value pairs");
            }
            result.put(args[index].substring(2), args[index + 1]);
        }
        for (String required : List.of("endpoint", "warehouse", "access-key", "secret-key", "database", "table", "expected")) {
            if (!result.containsKey(required)) {
                throw new IllegalArgumentException("missing --" + required);
            }
        }
        return result;
    }
}
