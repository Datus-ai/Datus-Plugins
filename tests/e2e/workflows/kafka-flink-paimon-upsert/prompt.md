Deploy a Flink job that consumes the provided 120 keyed update events from the
test Kafka-compatible endpoint and writes a primary-key Paimon table. Wait until
all events are committed. Save the SQL, Operator manifest, and an execution
summary. The deterministic final state contains 100 keys.
