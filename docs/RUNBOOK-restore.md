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

> Geautomatiseerd (community, prod sinds C1): `bash scripts/neo4j-restore-drill.sh`
> — doet onderstaande in een wegwerp-container en print RTO/RPO. Onderstaande
> handmatige variant geldt voor de Enterprise-editie (`restoredrill`-database).

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

## Off-host herstel (Synology, versleutelde kopieën)

De nachtelijke backup zet AES-256-versleutelde kopieën op de Synology
(`Bas_Spaan@192.168.3.6:nova-backups/neo4j/*.dump.enc`, poort 55). Het
wachtwoordbestand staat op de z390: `~/.secrets/nova-backup-key` — **bewaar een
kopie in je wachtwoordmanager**; zonder dit bestand zijn de off-host kopieën
onbruikbaar (dat is de bedoeling bij diefstal, niet bij herstel).

Herstel wanneer de z390 zelf weg is:
```bash
# 1. haal de jongste kopie van de Synology (vanaf elke pc met ssh-toegang)
scp -P 55 Bas_Spaan@192.168.3.6:nova-backups/neo4j/span-brain-<STAMP>.dump.enc .
# 2. ontsleutel met de sleutel uit je wachtwoordmanager (zet hem in ./key)
openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
  -in span-brain-<STAMP>.dump.enc -out span-brain.dump -pass file:./key
# 3. laad in een verse Neo4j (community): zie 'Echte restore' hierboven —
#    neo4j-admin database load span-brain --from-path=. + default-db-rename
```

NB (community, sinds C1): de nachtelijke dump stopt de neo4j-container ~1
minuut (STOP DATABASE bestaat niet op community); LO's /readyz geeft die
minuut 503 en herstelt vanzelf. De Synology-klok loopt iets achter — de
remote retentie heeft daarom 2 dagen marge.

## D. Drill-checklist (maandelijks, ~15 min)

1. [ ] Drill draaien op de z390 (niet tussen 03:00-04:00, dan draaien de
       nachtdump en de nachttaken): `bash ~/nova/scripts/neo4j-restore-drill.sh`
       → eindigt met `DRILL GESLAAGD`; noteer de RTO/RPO-regel in de tabel.
2. [ ] Off-host-kopie vers: `ssh -p 55 Bas_Spaan@192.168.3.6 "ls -lt nova-backups/neo4j | head -3"`
       → jongste `.enc` is van vannacht.
3. [ ] Off-host-kopie écht terug te zetten (restore-test — bewijst sleutel én
       kopie in één keer; parameters uit scripts/neo4j-backup.sh r108-109):

   ```bash
   mkdir -p /tmp/enc-drill
   NEWEST="$(ssh -p 55 Bas_Spaan@192.168.3.6 'ls -1t nova-backups/neo4j/span-brain-*.dump.enc | head -1')"
   scp -P 55 "Bas_Spaan@192.168.3.6:$NEWEST" /tmp/enc-drill/
   openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 \
     -in "/tmp/enc-drill/$(basename "$NEWEST")" \
     -out "/tmp/enc-drill/$(basename "$NEWEST" .enc)" \
     -pass "file:$HOME/.secrets/nova-backup-key"
   NOVA_BACKUP_DIR=/tmp/enc-drill bash ~/nova/scripts/neo4j-restore-drill.sh
   rm -rf /tmp/enc-drill
   ```

   → zelfde `DRILL GESLAAGD` als punt 1, maar nu vanaf de versleutelde
   off-host kopie.
4. [ ] Sleutel-kopie: naast `~/.secrets/nova-backup-key` op de z390 (punt 3
       bewijst dat die werkt) staat er een kopie in de wachtwoordmanager.
5. [ ] Homelab-lagen buiten dit repo: backrest/restic-snapshot (03:00) en de
       dagelijkse rsync naar de externe USB (01:00) zijn < 48u oud. Een echte
       restic/rsync-restore-steekproef (backrest-UI → restore, of
       `restic check --read-data-subset=2%`) valt buiten A5-scope en hoort
       bij het homelab-onderhoud — hier alleen versheid.

| datum | RTO (drill-duur) | RPO (dump-leeftijd) | resultaat |
|-------|------------------|---------------------|-----------|
|       |                  |                     |           |
