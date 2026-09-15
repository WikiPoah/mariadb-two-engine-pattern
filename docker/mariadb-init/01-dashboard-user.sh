#!/bin/sh
# A subshell also keeps these settings private when the image sources this hook.
(
    set -eu
    : "${MARIADB_DASHBOARD_PASSWORD:?Set MARIADB_DASHBOARD_PASSWORD}"
    : "${MARIADB_ROOT_PASSWORD:?Set MARIADB_ROOT_PASSWORD}"
    # Quote SQL literals independently of backslash escaping in the server defaults.
    dashboard_password=$(printf '%s' "$MARIADB_DASHBOARD_PASSWORD" | sed "s/'/''/g")
    MYSQL_PWD="$MARIADB_ROOT_PASSWORD" mariadb --protocol=socket --user=root <<SQL
SET SESSION sql_mode = 'NO_BACKSLASH_ESCAPES';
CREATE USER 'dashboard'@'%' IDENTIFIED BY '$dashboard_password';
GRANT SELECT ON \`commerce\_analytics\`.* TO 'dashboard'@'%';
SQL
)
