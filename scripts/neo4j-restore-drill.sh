#!/usr/bin/env bash
# Herstel-DRILL voor het Neo4j-brein (A5) — bewijst dat de jongste dump écht
# terug te zetten is, ZONDER de productie-database aan te raken.
#
# Community-editie (prod sinds C1): dump laden in een wegwerp-container op een
# wegwerp-volume, nodes tellen, vergelijken met productie, alles opruimen.
# Enterprise: volg docs/RUNBOOK-restore.md sectie A (restoredrill in-container).
#
# Handmatig draaien (maandelijks, NIET tussen 03:00-04:00 — nachtdump en
# nachttaken), zie RUNBOOK sectie D:
#   bash scripts/neo4j-restore-drill.sh
# Exit 0 = geslaagd; 1 = mislukt; 2 = editie niet ondersteund door dit script.
set -uo pipefail

DIR="${NOVA_BACKUP_DIR:-$HOME/nova-backups/neo4j}"
CT="${NOVA_NEO4J_CONTAINER:-nova-neo4j}"
DB="${NOVA_DRILL_DB:-span-brain}"
MIN_RATIO="${NOVA_DRILL_MIN_RATIO:-90}"   # drill-count >= 90% van prod = slagen
ts() { date +%Y-%m-%dT%H:%M:%S; }
T0="$(date +%s)"

DUMP="$(ls -1t "$DIR/${DB}-"*.dump 2>/dev/null | head -1)"
if [ -z "$DUMP" ]; then echo "[$(ts)] FOUT: geen ${DB}-dump in $DIR" >&2; exit 1; fi
DUMP_AGE_H=$(( ( $(date +%s) - $(stat -c %Y "$DUMP") ) / 3600 ))

# Het prod-wachtwoord blijft BINNEN de container: cypher-shell leest
# NEO4J_USERNAME/NEO4J_PASSWORD uit zijn omgeving, dus het geheim komt nooit
# als docker-exec-argument in de proceslijst van de host terecht. En bewust
# nergens `set -x` in dit script — dat zou de expansie alsnog loggen.
prod_cypher() {  # $1 = database, $2 = query -> laatste plain-regel van de output
  docker exec "$CT" sh -c \
    'NEO4J_USERNAME=neo4j NEO4J_PASSWORD="${NEO4J_AUTH#*/}" cypher-shell -d "$1" --format plain "$2"' \
    sh "$1" "$2" 2>/dev/null | tail -n 1 | tr -d '"\r'
}

EDITION="$(prod_cypher system "CALL dbms.components() YIELD edition RETURN edition;")"
if [ "$EDITION" != "community" ]; then
  echo "[$(ts)] editie '$EDITION': gebruik RUNBOOK-restore.md sectie A (restoredrill)" >&2
  exit 2
fi
PROD_N="$(prod_cypher "$DB" "MATCH (n) RETURN count(n);")"
PROD_N="${PROD_N:-0}"

IMG="$(docker inspect --format '{{.Config.Image}}' "$CT")"
TMP="$(mktemp -d)"
cleanup() {
  docker rm -f nova-drill >/dev/null 2>&1
  docker volume rm nova-drill-data >/dev/null 2>&1
  rm -rf "$TMP"
}
trap cleanup EXIT

# dump laden als default-db 'neo4j' op een vers wegwerp-volume (--entrypoint
# omzeilt de privilege-drop van de image, zie scripts/neo4j-backup.sh r45-48)
cp "$DUMP" "$TMP/neo4j.dump"
docker volume create nova-drill-data >/dev/null
if ! docker run --rm --user root --entrypoint neo4j-admin \
       -v nova-drill-data:/data -v "$TMP":/backups "$IMG" \
       database load neo4j --from-path=/backups --overwrite-destination=true; then
  echo "[$(ts)] FOUT: dump laden mislukt ($DUMP)" >&2; exit 1
fi
docker run -d --name nova-drill -e NEO4J_AUTH=neo4j/drill-tijdelijk \
  -v nova-drill-data:/data "$IMG" >/dev/null

DRILL_N=""
for _ in $(seq 1 60); do   # max ~2 min wachten tot de wegwerp-server op is
  DRILL_N="$(docker exec nova-drill cypher-shell -u neo4j -p drill-tijdelijk \
             --format plain "MATCH (n) RETURN count(n);" 2>/dev/null \
             | tail -n 1 | tr -d '"\r')"
  [ -n "$DRILL_N" ] && break
  sleep 2
done
if [ -z "$DRILL_N" ]; then echo "[$(ts)] FOUT: wegwerp-server kwam niet op" >&2; exit 1; fi

RTO=$(( $(date +%s) - T0 ))
echo "[$(ts)] drill: $DRILL_N nodes hersteld uit $(basename "$DUMP") (productie: $PROD_N)"
echo "[$(ts)] RTO: ${RTO}s (drill-duur) — RPO: max ${DUMP_AGE_H}u (leeftijd jongste dump)"
if [ "$DRILL_N" -eq 0 ]; then echo "[$(ts)] DRILL MISLUKT: 0 nodes" >&2; exit 1; fi
if [ "$PROD_N" -gt 0 ] && [ $(( DRILL_N * 100 )) -lt $(( PROD_N * MIN_RATIO )) ]; then
  echo "[$(ts)] DRILL MISLUKT: hersteld aantal < ${MIN_RATIO}% van productie" >&2
  exit 1
fi
echo "[$(ts)] DRILL GESLAAGD"
