ARG FLINK_VERSION=1.20.1
ARG PAIMON_VERSION=1.0.1
ARG HADOOP_VERSION=3.3.6

FROM maven:3.9.11-eclipse-temurin-17 AS build
ARG FLINK_VERSION
ARG PAIMON_VERSION
ARG HADOOP_VERSION
WORKDIR /src
COPY pom.xml .
COPY src ./src
RUN mvn -B -DskipTests -Dflink.version="${FLINK_VERSION}" -Dpaimon.version="${PAIMON_VERSION}" \
    -Dhadoop.version="${HADOOP_VERSION}" package

FROM flink:${FLINK_VERSION}-scala_2.12-java17
COPY --from=build /src/target/sql-runner.jar /opt/flink/usrlib/sql-runner.jar
USER flink
