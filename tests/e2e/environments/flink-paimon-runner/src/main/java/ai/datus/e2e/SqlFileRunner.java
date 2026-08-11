package ai.datus.e2e;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import org.apache.flink.table.api.EnvironmentSettings;
import org.apache.flink.table.api.TableEnvironment;

/** Execute a checked SQL file synchronously inside a Flink application cluster. */
public final class SqlFileRunner {
    private SqlFileRunner() {}

    public static void main(String[] args) throws Exception {
        if (args.length != 2 || !"--sql".equals(args[0])) {
            throw new IllegalArgumentException("usage: SqlFileRunner --sql <file>");
        }
        String raw = Files.readString(Path.of(args[1]), StandardCharsets.UTF_8);
        TableEnvironment tables = TableEnvironment.create(EnvironmentSettings.newInstance().inStreamingMode().build());
        for (String statement : raw.split(";\\s*(?:\\r?\\n|$)")) {
            String sql = statement.strip();
            if (sql.isEmpty() || sql.lines().allMatch(line -> line.strip().startsWith("--"))) {
                continue;
            }
            tables.executeSql(sql).await();
        }
    }
}
