#!/usr/bin/env bash
# Nachtelijke Neo4j-backup van de ECHTE brein-databases.
# - Enterprise: korte per-db STOP -> dump -> START (online backup bleek
#   onbetrouwbaar in de 2GB-container en pakte de lege default-db 'neo4j').
# - Community (sinds de C1-migratie): STOP DATABASE bestaat daar niet ->
#   container kort stoppen, dumpen via een one-off container op het volume,
#   container weer starten (~1 min downtime, 's nachts).
#
# Retentie: laatste N dumps per database (default 7).
# Off-host (aanbevolen): NOVA_BACKUP_SSH (versleuteld, tar-over-ssh) of
# NOVA_BACKUP_REMOTE (rclone). Cron (crontab -e):
#   0 3 * * *  bash /home/administrator/nova/scripts/neo4j-backup.sh >> ~/nova-backups/backup.log 2>&1
set -uo pipefail

DIR="${NOVA_BACKUP_DIR:-$HOME/nova-backups/neo4j}"
CT="${NOVA_NEO4J_CONTAINER:-nova-neo4j}"
KEEP="${NOVA_BACKUP_KEEP:-7}"
mkdir -p "$DIR"
ts() { date +%Y-%m-%dT%H:%M:%S; }
STAMP="$(date +%Y%m%dT%H%M%S)"

PW="$(docker exec "$CT" printenv NEO4J_AUTH | cut -d/ -f2)"
cyp() { docker exec "$CT" cypher-shell -u neo4j -p "$PW" -d system "$1" >/dev/null 2>&1; }

# db-lijst en editie opvragen VÓÓR een eventuele stop
DBS="$(docker exec "$CT" cypher-shell -u neo4j -p "$PW" -d system --format plain \
       "SHOW DATABASES YIELD name WHERE name <> 'system' RETURN name;" 2>/dev/null \
       | tail -n +2 | tr -d '"\r')"
if [ -z "$DBS" ]; then echo "[$(ts)] FOUT: geen databases gevonden" >&2; exit 1; fi
EDITION="$(docker exec "$CT" cypher-shell -u neo4j -p "$PW" -d system --format plain \
           "CALL dbms.components() YIELD edition RETURN edition;" 2>/dev/null \
           | tail -n 1 | tr -d '\"\r')"

ok=0
if [ "$EDITION" = "community" ]; then
  # Community: hele server kort offline; dump via one-off container op het volume
  IMG="$(docker inspect --format '{{.Config.Image}}' "$CT")"
  VOL="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Name}}{{end}}{{end}}' "$CT")"
  if [ -z "$VOL" ]; then echo "[$(ts)] FOUT: data-volume niet gevonden" >&2; exit 1; fi
  docker stop "$CT" >/dev/null
  for db in $DBS; do
    # --entrypoint omzeilt de privilege-drop van de image (de entrypoint gosu't
    # naar uid 7474, óók met --user root, en die uid mag de host-backupmap niet
    # in). neo4j-admin leest de store alleen; schrijft enkel naar /backups.
    if docker run --rm --user root --entrypoint neo4j-admin \
         -v "$VOL":/data -v "$DIR":/backups "$IMG" \
         database dump "$db" --to-path=/backups \
         --overwrite-destination=true >/dev/null 2>&1; then
      mv -f "$DIR/$db.dump" "$DIR/${db}-${STAMP}.dump"
      ok=$((ok + 1))
    else
      echo "[$(ts)] WAARSCHUWING: dump van '$db' mislukt" >&2
    fi
  done
  docker start "$CT" >/dev/null
else
  # Enterprise: per-db stop, server blijft draaien
  docker exec "$CT" sh -c 'rm -rf /tmp/nova-bk && mkdir -p /tmp/nova-bk'
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
  for f in $(docker exec "$CT" sh -c 'ls /tmp/nova-bk/*.dump 2>/dev/null'); do
    base="$(basename "$f" .dump)"
    docker cp "$CT:$f" "$DIR/${base}-${STAMP}.dump" >/dev/null 2>&1
  done
  docker exec "$CT" rm -rf /tmp/nova-bk >/dev/null 2>&1
fi
echo "[$(ts)] $ok database(s) gedumpt naar $DIR ($EDITION)"

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

# off-host via ssh/rsync met client-side encryptie (AVG: dumps bevatten
# persoonsgegevens; versleuteld vóór ze de host verlaten). Voor doelen zonder
# sftp-subsysteem (bv. Synology met alleen ssh+rsync).
#   NOVA_BACKUP_SSH      = doel, bv. Bas_Spaan@192.168.3.6:nova-backups/neo4j
#   NOVA_BACKUP_SSH_OPTS = ssh-opties, bv. "-p 55 -i ~/.ssh/id_ed25519_synology_backup"
#   NOVA_BACKUP_KEYFILE  = wachtwoordbestand voor openssl (default hieronder)
if [ -n "${NOVA_BACKUP_SSH:-}" ]; then
  KEYFILE="${NOVA_BACKUP_KEYFILE:-$HOME/.secrets/nova-backup-key}"
  if [ ! -f "$KEYFILE" ]; then
    echo "[$(ts)] WAARSCHUWING: NOVA_BACKUP_SSH gezet maar sleutelbestand $KEYFILE ontbreekt" >&2
  else
    ENC="$DIR/.encrypted"
    mkdir -p "$ENC"
    # versleutel wat nog niet versleuteld is (dumps zijn immutable per timestamp)
    for f in "$DIR"/*.dump; do
      [ -f "$f" ] || continue
      out="$ENC/$(basename "$f").enc"
      [ -f "$out" ] || openssl enc -aes-256-cbc -pbkdf2 -iter 200000 -salt \
        -in "$f" -out "$out" -pass "file:$KEYFILE"
    done
    # retentie spiegelen: .enc zonder bron-dump opruimen
    for e in "$ENC"/*.dump.enc; do
      [ -f "$e" ] || continue
      bron="$DIR/$(basename "$e" .enc)"
      [ -f "$bron" ] || rm -f "$e"
    done
    # tar-over-ssh i.p.v. rsync/sftp: op een Synology kunnen de rsync-dienst
    # en het sftp-subsysteem uitstaan terwijl kaal ssh gewoon werkt. Remote
    # retentie op mtime (KEEP+2 dagen marge t.o.v. de lokale retentie).
    DEST_HOST="${NOVA_BACKUP_SSH%%:*}"
    DEST_PATH="${NOVA_BACKUP_SSH#*:}"
    # shellcheck disable=SC2086
    if tar -C "$ENC" -cf - . | ssh ${NOVA_BACKUP_SSH_OPTS:-} "$DEST_HOST" \
         "mkdir -p '$DEST_PATH' && tar -C '$DEST_PATH' -xf - && \
          find '$DEST_PATH' -name '*.enc' -mtime +$((KEEP + 2)) -delete"; then
      echo "[$(ts)] off-host kopie (versleuteld) ok -> $NOVA_BACKUP_SSH"
    else
      echo "[$(ts)] WAARSCHUWING: off-host kopie naar $NOVA_BACKUP_SSH mislukt" >&2
    fi
  fi
fi
