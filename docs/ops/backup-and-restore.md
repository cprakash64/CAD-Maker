# Backup, restore, and the executed restore drill

## Backup schedule

Production runs Postgres (`docs/deployment.md`). Two independent things need
backing up, and they have **different** recovery strategies — don't conflate
them:

| What | Backed up how | Recovery strategy |
|---|---|---|
| Database (`users`, `projects`, `designs`, `jobs`, `export_files` rows, etc.) | `pg_dump` (custom format, `-F c`) | Restore from the dump — this is the ONLY copy of prompts/specs/account data. |
| Generated artifacts (STL/STEP files) | **Not separately backed up** | **Regenerate**, not restore — see "Artifact recovery expectations" below. |

### Schedule

- **Nightly full `pg_dump`**, 03:00 server time (after the artifact-retention
  sweep at the same hour — see `docs/ops/data-retention.md` — so the backup
  reflects post-sweep state, not a larger DB than steady-state).
- **Retain 14 daily backups + 8 weekly backups** off-box (a second disk, or
  object storage separate from the app's own `STORAGE_BACKEND`/bucket — a
  backup that lives next to the thing it's protecting isn't a backup).
- If the managed Postgres provider offers point-in-time recovery (WAL
  archiving), enable it in addition to the nightly dump — it bounds data loss
  to seconds instead of "since last night's dump," at effectively no extra
  operational cost.

### Scheduling: systemd timer (committed) or cron (portability fallback)

`scripts/backup.sh` (repo root) is the real, committed backup script —
`pg_dump` to a timestamped custom-format dump, an optional off-box copy via
`rclone` (set `BACKUP_REMOTE`, e.g. `remote:lunaicad-backups/`; without it the
script still runs but logs a loud warning since a same-box backup isn't a
real backup), and local retention pruning (`BACKUP_RETAIN_DAYS`, default 14).

The preferred trigger is the committed systemd timer
(`deploy/systemd/lunaicad-backup.service` + `.timer`, installed alongside the
other units per `docs/deployment.md`'s systemd section) — it runs nightly at
03:05 server time, five minutes after the retention sweep's 03:00 slot (see
`docs/ops/data-retention.md`) so the backup reflects post-sweep state. Verify
it's actually scheduled with `systemctl list-timers lunaicad-backup.timer`.

If a box can't run systemd timers, the equivalent cron line (source the env
file first, since `DATABASE_URL` isn't in cron's environment by default):

```cron
0 3 * * *  . /opt/lunaicad/backend/.env && /opt/lunaicad/src/scripts/backup.sh >> /var/log/lunaicad-backup.log 2>&1
```

## Restore procedure (production)

```bash
# 1. Stop both services so nothing writes during restore.
sudo systemctl stop lunaicad-backend lunaicad-worker

# 2. Restore into a FRESH database (never pg_restore over a live one you
#    still need — restore to a new name, verify, then cut over).
createdb -U lunaicad lunaicad_restored
pg_restore -U lunaicad -d lunaicad_restored --no-owner --no-privileges \
    /opt/lunaicad/backups/lunaicad_<STAMP>.dump

# 3. Verify (see the executed drill below for the exact checks) before
#    pointing DATABASE_URL at it.
psql -U lunaicad -d lunaicad_restored -c "select count(*) from designs;"

# 4. Cut over: update DATABASE_URL (env or .env), then:
alembic upgrade head        # confirms schema is current; no-ops if it already is
sudo systemctl start lunaicad-backend lunaicad-worker
curl -m 5 https://your-domain.example/ready
```

## Artifact recovery expectations

STL/STEP files are **not** individually backed up. This is a deliberate
design choice, not a gap:

- Every export is **deterministically regenerable** from the design's
  `spec_json` (`app.export.exporter.generate` — "same spec in → same
  geometry out, no LLM"). If a design's row survives (via the DB backup
  above), its exports can always be rebuilt on demand by re-opening the
  design in the studio or calling `POST /api/designs/{id}/export`.
- **Local storage** (`STORAGE_BACKEND=local`): `storage_data/` is a cache of
  regenerable files, not a source of truth. If the disk is lost, restore the
  DB and let exports regenerate lazily — no separate artifact restore step
  exists or is needed.
- **S3-compatible storage** (`STORAGE_BACKEND=s3`): enable the bucket's own
  versioning/replication if the provider offers it (cheap insurance against
  an accidental delete), but this is optional — the regeneration path above
  is the actual recovery mechanism either way.
- **Uploaded drawings** (`job_uploads/...`) are the one exception worth
  calling out: a drawing is deleted from storage as soon as its job reaches
  a terminal state (`app.worker.supervisor._cleanup_job_storage`) — it was
  never meant to be retained past the job that consumed it. If a drawing
  job fails and needs a retry, re-upload the source file.

**Recovery Time Objective (RTO) implication**: restoring the DB alone brings
the product back to a fully functional state (users can log in, see their
design history, re-export). There is no scenario where "restore the
database" leaves exports permanently unrecoverable while the design row
exists.

## Executed restore drill (evidence)

Run 2026-07-29, against a real Postgres 16 instance (`postgres:16-alpine`,
Docker), using the actual `alembic` migration chain and the actual
SQLAlchemy models (`app.models`) — not a toy schema. This is the exact
procedure in "Restore procedure" above, exercised end to end.

### 1. Provisioned a clean Postgres instance and applied migrations

```
$ docker run -d --name lunaicad-restore-drill -e POSTGRES_PASSWORD=drillpass \
    -e POSTGRES_USER=lunaicad -e POSTGRES_DB=lunaicad_drill -p 15432:5432 postgres:16-alpine
$ docker exec lunaicad-restore-drill pg_isready -U lunaicad
/var/run/postgresql:5432 - accepting connections

$ DATABASE_URL="postgresql+psycopg://lunaicad:drillpass@127.0.0.1:15432/lunaicad_drill" alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade  -> 7fbf0c6446aa, initial schema
INFO  [alembic.runtime.migration] Running upgrade 7fbf0c6446aa -> b1c4e7a92f38, drop designs.program_code
INFO  [alembic.runtime.migration] Running upgrade b1c4e7a92f38 -> 557ca72e7e96, add calibration profiles + measurements
INFO  [alembic.runtime.migration] Running upgrade 557ca72e7e96 -> 7a0975b01cf8, add jobs
INFO  [alembic.runtime.migration] Running upgrade 7a0975b01cf8 -> 3f2c9a7d1b44, add design_versions
```

### 2. Seeded realistic data through the real ORM models

A user, project, design (with real `prompt`/`spec_json`), export file record,
and job row — via `app.models.*` directly (`hash_password` for the user, so
even the password hashing path is exercised).

```
SEEDED: user_id= bd47e41b4ba943ecb80e9d2bde6b5ba5
SEEDED: design_id= bef2d890295b4633bf9b110607e815cb
SEEDED: design_prompt= a bracket 80x40x6mm with two M6 holes
```

Baseline row counts confirmed before backup:

```
 users | projects | designs | export_files | jobs
-------+----------+---------+--------------+------
     1 |        1 |       1 |            1 |    1
```

### 3. Took the backup

```
$ docker exec lunaicad-restore-drill pg_dump -U lunaicad -d lunaicad_drill -F c -f /tmp/lunaicad_drill_backup.dump
$ docker cp lunaicad-restore-drill:/tmp/lunaicad_drill_backup.dump ./lunaicad_drill_backup.dump
$ ls -la lunaicad_drill_backup.dump
-rw-r--r--  1 cprakash  wheel  28751 Jul 28 21:59 lunaicad_drill_backup.dump
```

Backup taken at **2026-07-29T01:59:42Z**.

### 4. Simulated total data loss

```
$ docker exec lunaicad-restore-drill psql -U lunaicad -d postgres -c "DROP DATABASE lunaicad_drill;"
DROP DATABASE
$ docker exec lunaicad-restore-drill psql -U lunaicad -d postgres -c "CREATE DATABASE lunaicad_drill;"
CREATE DATABASE
$ docker exec lunaicad-restore-drill psql -U lunaicad -d lunaicad_drill -c "\dt"
Did not find any relations.
```

Every table, every row — gone. This is a harder scenario than "lost some
rows"; it proves the backup alone (no other state) is sufficient to rebuild
the database from nothing.

### 5. Restored from the backup

```
$ docker cp lunaicad_drill_backup.dump lunaicad-restore-drill:/tmp/restore_input.dump
$ docker exec lunaicad-restore-drill pg_restore -U lunaicad -d lunaicad_drill \
    --no-owner --no-privileges -v /tmp/restore_input.dump
pg_restore: creating TABLE "public.users"
pg_restore: creating TABLE "public.projects"
pg_restore: creating TABLE "public.designs"
pg_restore: creating TABLE "public.export_files"
pg_restore: creating TABLE "public.jobs"
... (schema, indexes, FK constraints all recreated) ...
pg_restore: creating FK CONSTRAINT "public.projects projects_user_id_fkey"
```

Restore completed at **2026-07-29T02:00:00Z** — **18 seconds** after the
backup was taken (dominated by container/DDL overhead at this tiny data
size; the dump itself is 28 KB).

### 6. Verified — row counts, content, schema version, and app-level reads

Row counts, back to exactly what they were before the simulated loss:

```
 users | projects | designs | export_files | jobs
-------+----------+---------+--------------+------
     1 |        1 |       1 |            1 |    1
```

Content-level parity (not just counts — the actual data):

```
$ psql ... -c "SELECT id, prompt, object_type, spec_hash FROM designs;"
                id                |                prompt                 |     object_type     |  spec_hash
-----------------------------------+---------------------------------------+---------------------+--------------
 bef2d890295b4633bf9b110607e815cb | a bracket 80x40x6mm with two M6 holes | rectangular_bracket | drillhash123

$ psql ... -c "SELECT email FROM users;"
         email
------------------------
 drill-user@example.com
```

Schema/migration state survived the restore intact:

```
$ psql ... -c "SELECT version_num FROM alembic_version;"
 version_num
--------------
 3f2c9a7d1b44

$ DATABASE_URL=... alembic upgrade head
(no output -- already at head, confirming the restored schema matches the
 current migration chain exactly)
```

And, the check that matters most — **the actual application** (not raw SQL)
reads the restored row correctly:

```
$ python -c "... db.query(Design).first() ..."
App-level read via ORM -> id: bef2d890295b4633bf9b110607e815cb | prompt: a bracket 80x40x6mm with two M6 holes | object_type: rectangular_bracket
```

Verification completed at **2026-07-29T02:00:36Z**.

### Result

| Metric | Value |
|---|---|
| Backup → restore-verified wall-clock (RTO, this dataset) | 54 seconds |
| Data loss (RPO) | 0 rows — full parity, byte-for-byte on checked fields |
| Backup artifact size | 28,751 bytes for a 1-user/1-design DB (~8.2 MB allocated DB size) |
| Migration chain applied cleanly | Yes — 5/5 revisions, `7fbf0c6446aa` → `3f2c9a7d1b44` |
| Post-restore `alembic upgrade head` | No-op (schema already current) |
| App-level (ORM) read of restored data | Correct |

Environment cleaned up afterward (`docker stop && docker rm`) — this was
ephemeral drill infrastructure, not a standing resource.

**Caveat, stated plainly**: this drill ran against a fresh single-tenant
dataset sized for demonstration, not a production-scale database. The
mechanics (pg_dump → pg_restore → alembic → app read) are exactly what
production uses, and the procedure is identical regardless of scale — but
restore TIME will scale with data volume in production
(`docs/ops/deployment-runbook.md` → "Production environment validation"
includes re-running this drill against a production-sized copy before
launch, and periodically thereafter, as an open action item).
