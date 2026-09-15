FROM mariadb:12.3.3-noble@sha256:ab1c3dd381940233af12512b97d47b508fd3a0f17fbe3ba388739b7bc17cbc0b

RUN set -eu; \
    cd /tmp; \
    package_arch="$(dpkg --print-architecture)"; \
    case "$package_arch" in \
        arm64) package_sha256=8860a23a0bcaaf7f3f85d8e296f75fda27026ec9a46e9e4acbe1d832643f72bb ;; \
        amd64) package_sha256=f96044a1000b20fd9f95e2ed22e4ebcab7e29f9f653c33588c12d2294d4660cc ;; \
        *) echo "Unsupported architecture: $package_arch" >&2; exit 1 ;; \
    esac; \
    package_file="mariadb-plugin-duckdb_1%3a12.3.3+maria~ubu2404_${package_arch}.deb"; \
    apt-get update; \
    apt-get download mariadb-plugin-duckdb=1:12.3.3+maria~ubu2404; \
    echo "$package_sha256  $package_file" \
        | sha256sum --check --strict; \
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        "./$package_file"; \
    rm -f "/tmp/$package_file"; \
    rm -rf /var/lib/apt/lists/*

COPY --chmod=0644 docker/mariadb-init/01-dashboard-user.sh /docker-entrypoint-initdb.d/01-dashboard-user.sh
