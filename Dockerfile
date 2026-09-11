FROM mariadb:12.3.3-noble@sha256:ab1c3dd381940233af12512b97d47b508fd3a0f17fbe3ba388739b7bc17cbc0b

RUN set -eu; \
    cd /tmp; \
    test "$(dpkg --print-architecture)" = "arm64"; \
    apt-get update; \
    apt-get download mariadb-plugin-duckdb=1:12.3.3+maria~ubu2404; \
    echo "8860a23a0bcaaf7f3f85d8e296f75fda27026ec9a46e9e4acbe1d832643f72bb  mariadb-plugin-duckdb_1%3a12.3.3+maria~ubu2404_arm64.deb" \
        | sha256sum --check --strict; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        ./mariadb-plugin-duckdb_1%3a12.3.3+maria~ubu2404_arm64.deb; \
    rm -f /tmp/mariadb-plugin-duckdb_1%3a12.3.3+maria~ubu2404_arm64.deb; \
    rm -rf /var/lib/apt/lists/*
