ARG SUPERSET_IMAGE
FROM ${SUPERSET_IMAGE}

ARG PSYCOPG2_VERSION
USER root
RUN pip install --no-cache-dir "psycopg2-binary==${PSYCOPG2_VERSION}"
USER superset
