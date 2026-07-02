# Runbook — Neo4j-brein herstellen uit een backup

**Doel:** de brein-database(s) terugzetten uit een `*.dump` gemaakt door
`scripts/neo4j-backup.sh` (offline dump, Neo4j 5.26 Enterprise).

**Belangrijk:** het échte brein is **`span-brain`** (niet de lege default-db `neo4j`).
In multi-user zijn er ook `brain-shared` en per gebruiker `brain-<oid>`. Het
backup-script dumpt álle non-system databases. Dumps staan op z390 in
`~/nova-backups/neo4j/` als `<db>-<timestamp>.dump` (bv. `span-brain-20260702T....dump`).

Wachtwoord: `PW=$(docker exec nova-neo4j printenv NEO4J_AUTH | cut -d/ -f2)`.
Db-namen met koppelteken moeten in Cypher tussen backticks: `` `span-brain` ``.

## A. Herstel-DRILL (veilig — raakt de productie-DB NIET)

Laad een dump in een **wegwerp-database** en tel de nodes. Bewijst restore-baarheid
zonder `span-brain` (productie) aan te raken.

```bash
CT=nova-neo4j; PW=$(docker exec $CT printenv NEO4J_AUTH | cut -d/ -f2)
DUMP=$(ls -1t ~/nova-backups/neo4j/span-brain-*.dump | head -1)
docker cp "$DUMP" $CT:/tmp/drill.dump
docker exec $CT neo4j-admin database load --from-path=/tmp/drill.dump restoredrill --overwrite-destination=true
docker exec $CT cypher-shell -u neo4j -p "$PW" -d system "CREATE DATABASE restoredrill IF NOT EXISTS;"
sleep 4
echo -n "restoredrill nodes: "; docker exec $CT cypher-shell -u neo4j -p "$PW" -d restoredrill "MATCH (n) RETURN count(n) AS n;"
echo -n "prod span-brain   : "; docker exec $CT cypher-shell -u neo4j -p "$PW" -d span-brain "MATCH (n) RETURN count(n) AS n;"
docker exec $CT cypher-shell -u neo4j -p "$PW" -d system "DROP DATABASE restoredrill;"
docker exec $CT rm -f /tmp/drill.dump
```
Slaag = node-aantal in `restoredrill` ≈ productie. **Leg RTO/RPO vast** (drill-duur;
leeftijd nieuwste dump = max dataverlies). Als `CREATE DATABASE` klaagt over
`restore_metadata.cypher`, voer dat script uit (pad staat in de load-output) — dat
is Neo4j-5-metadata (rollen/aliassen), niet de nodes zelf.

## B. ECHT herstel na dataverlies (overschrijft de productie-DB)

> ⚠️ Alleen bij een kapot/leeg brein. Vervangt `span-brain`.

```bash
CT=nova-neo4j; PW=$(docker exec $CT printenv NEO4J_AUTH | cut -d/ -f2)
DUMP=/pad/naar/span-brain-<timestamp>.dump                 # gekozen herstelpunt
docker cp "$DUMP" $CT:/tmp/restore.dump
docker stop nova-span                                      # app stil tijdens herstel
docker exec $CT cypher-shell -u neo4j -p "$PW" -d system "STOP DATABASE \`span-brain\`;"
docker exec $CT neo4j-admin database load --from-path=/tmp/restore.dump span-brain --overwrite-destination=true
docker exec $CT cypher-shell -u neo4j -p "$PW" -d system "START DATABASE \`span-brain\`;"
docker exec $CT rm -f /tmp/restore.dump
docker start nova-span
echo -n "span-brain nodes: "; docker exec $CT cypher-shell -u neo4j -p "$PW" -d span-brain "MATCH (n) RETURN count(n) AS n;"
```
Herhaal voor `brain-shared` en elke `brain-<oid>` indien nodig. Embeddings zitten in
de dump (echte store), dus vectorzoeken werkt direct. Controleer daarna een chat +
`/api/status`.

## C. Aandachtspunten

- **Off-host:** zet `NOVA_BACKUP_REMOTE` (rclone-doel, versleuteld) — nu staan dumps
  nog op dezelfde host; machine-verlies neemt anders ook de backups mee.
- **Encryptie-at-rest:** een dump bevat het hele brein incl. integratie-tokens op de
  Config-node → off-host versleuteld bewaren (rclone crypt / age).
- **Retentie:** `scripts/neo4j-backup.sh` houdt de laatste `NOVA_BACKUP_KEEP` (default 7) per db.
- **Test periodiek de drill (A)** — een ongeteste backup is geen backup.
