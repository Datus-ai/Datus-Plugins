ARG FLINK_VERSION=2.0.2
ARG PAIMON_FLINK_VERSION=2.0
ARG PAIMON_VERSION=1.4.1
ARG HADOOP_VERSION=3.3.6

FROM maven:3.9.11-eclipse-temurin-17 AS verifier-build
ARG FLINK_VERSION
ARG PAIMON_FLINK_VERSION
ARG PAIMON_VERSION
ARG HADOOP_VERSION
WORKDIR /src
COPY pom.xml .
COPY src ./src
RUN mvn -B -DskipTests -Dflink.version="${FLINK_VERSION}" \
    -Dpaimon.flink.version="${PAIMON_FLINK_VERSION}" -Dpaimon.version="${PAIMON_VERSION}" \
    -Dhadoop.version="${HADOOP_VERSION}" package

FROM flink:${FLINK_VERSION}-scala_2.12-java17
COPY --from=verifier-build /src/target/e2e-verifier.jar /opt/flink/lib/e2e-verifier.jar
USER flink
