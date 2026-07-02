#!/usr/bin/env bash
# Nachtelijke Neo4j-backup van de ECHTE brein-databases (span-brain, brain-shared,
# brain-<oid>, ...). Offline dump met een KORTE per-db stop — online backup bleek
# onbetrouwbaar in de 2GB-container en pakte bovendien de lege default-db 'neo4j'.
#
# Retentie: laatste N dumps per database (default 7).
# Off-host + encryptie (aanbevolen): zet NOVA_BACKUP_REMOTE op een rclone-doel.
#   Cron (crontab -e):  0 3 * * *  /home/administrator/nova/scripts/neo4j-backup.sh >> ~/nova-backups/backup.log 2>&1
set -uo pipefail

DIR="${NOVA_BACKUP_DIR:-$HOME/nova-backups/neo4j}"
CT="${NOVA_NEO4J_CONTAINER:-nova-neo4j}"
KEEP="${NOVA_BACKUP_KEEP:-7}"
mkdir -p "$DIR"
ts() { date +%Y-%m-%dT%H:%M:%S; }
STAMP="$(date +%Y%m%dT%H%M%S)"

PW="$(docker exec "$CT" printenv NEO4J_AUTH | cut -d/ -f2)"
cyp() { docker exec "$CT" cypher-shell -u neo4j -p "$PW" -d system "$1" >/dev/null 2>&1; }

# alle databases behalve system (dus span-brain, brain-shared, brain-<oid>, neo4j)
DBS="$(docker exec "$CT" cypher-shell -u neo4j -p "$PW" -d system --format plain \
       "SHOW DATABASES YIELD name WHERE name <> 'system' RETURN name;" 2>/dev/null \
       | tail -n +2 | tr -d '"\r')"
if [ -z "$DBS" ]; then echo "[$(ts)] FOUT: geen databases gevonden" >&2; exit 1; fi

docker exec "$CT" sh -c 'rm -rf /tmp/nova-bk && mkdir -p /tmp/nova-bk'
ok=0
for db in $DBS; do
  cyp "STOP DATABASE \`$db\`;"
  if docker exec "$CT" neo4j-admin database dump "$db" --to-path=/tmp/nova-bk \
       --overwrite-destination=true >/dev/null 2>&1; then
    ok=$((ok + 1))
  else
    echo "[$(ts)] WAARSCHUWING: dump van '$db' mislukt" >&2
  fi
  cyp "START DATABASE \`$db\`;"
done

# dumps naar de host kopiëren met timestamp
for f in $(docker exec "$CT" sh -c 'ls /tmp/nova-bk/*.dump 2>/dev/null'); do
  base="$(basename "$f" .dump)"
  docker cp "$CT:$f" "$DIR/${base}-${STAMP}.dump" >/dev/null 2>&1
done
docker exec "$CT" rm -rf /tmp/nova-bk >/dev/null 2>&1
echo "[$(ts)] $ok database(s) gedumpt naar $DIR"

# retentie per database
for db in $DBS; do
  ls -1t "$DIR/${db}-"*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
done

# off-host kopie (optioneel, aanbevolen versleuteld)
if [ -n "${NOVA_BACKUP_REMOTE:-}" ]; then
  if command -v rclone >/dev/null 2>&1; then
    rclone copy "$DIR" "$NOVA_BACKUP_REMOTE" && echo "[$(ts)] off-host kopie ok -> $NOVA_BACKUP_REMOTE"
  else
    echo "[$(ts)] WAARSCHUWING: NOVA_BACKUP_REMOTE gezet maar rclone ontbreekt" >&2
  fi
fi
