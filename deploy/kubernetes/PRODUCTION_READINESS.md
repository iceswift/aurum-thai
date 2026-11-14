# Production migration rehearsal

Production remains on Docker Compose behind `api.thaigoldview.com`.
The rehearsal uses kind and isolated PostgreSQL. It does not change
Cloudflare routing or use production Firebase credentials.

## Rehearsal results (2026-11-13)

- All eight read endpoints matched at the top level, including the board's
  latest-price fields, and both environments returned a bodyless HTTP 304.
- Staging passed on GHCR image digest `sha256:a83303b7e1b4afd1d7c10808912651d2e55440cc2f2c4c452bd86e8a21d2b61b`.
  A Deployment rollback restored `aurum-thai:m2`; the old API served
  revision 1 from the same PostgreSQL database.
- Staging CronJob was suspended with zero active Jobs and staging Firebase
  was disabled. The production Compose container showed scraper and FCM
  activity and retained the same identity and start time before and after.

## Verify the read contract

Port-forward the split staging API and run:

```sh
python deploy/kubernetes/check_parity.py https://api.thaigoldview.com http://127.0.0.1:18081
```

This checks eight read endpoints, board shape, readiness, and bodyless
304 responses. It does not call the token registration endpoint or
send notifications. Compare schemas, not prices or revision numbers,
because the databases are isolated.

## Cutover sequence (not executed in rehearsal)

1. Record the current Compose image digest, container identity, tunnel route,
   and latest board revision. Back up the snapshot and notification state.
2. Keep the Kubernetes CronJob suspended and its Firebase publisher off.
   Disable the Compose scheduler before importing the final file snapshot
   into PostgreSQL. Preserve the revision and timestamps during import so
   existing clients do not reject new data as older.
3. Verify Kubernetes read parity against the imported state, test TLS and
   client token registration with an isolated test token, then change the
   origin behind the existing hostname. Verify the public endpoints.
4. Enable exactly one scheduled scraper and Firebase publisher. Check
   Job status, revision progression, and successful delivery.

## Rollback sequence (not executed against production)

Suspend the Kubernetes CronJob and wait for active Jobs to finish or stop
them before re-enabling Compose scraping or FCM. If Kubernetes has advanced
the revision, export its final state back to the Compose snapshot format
and restore it atomically. Start the previous pinned Compose image with
the original data and secret mounts. Restore the tunnel origin, then
verify public readiness, ETag, and a single active writer.

## State transfer verification

With writers stopped, use a new target database and a new export path:

```sh
python migrate_snapshot.py import ./compose-snapshot.json
python migrate_snapshot.py export ./rollback-snapshot.json
```

Set `SNAPSHOT_DATABASE_URL` from the database Secret. The commands refuse
to overwrite an existing snapshot or export file. Inspect the exported
state before installing it with an atomic rename on the same filesystem.

Rehearsed in disposable PostgreSQL: full state (including both timestamps)
matched after import and export at revision 1028; a subsequent write reached
1029, and the file-backed API restored 1029 and its ETag on startup. Both
destination overwrite attempts were rejected.

## Production state backup and restore check

On 2026-11-13, both the live snapshot and notification state were copied
to a restricted backup directory on the home server. Both SHA-256
checksums passed. A separate container using the running production
image, a read-only backup mount, no network, and no Firebase credentials
restored revision 1028, verified its ETag and snapshot consistency, and
loaded the notification state. Production was not restarted.

The two files were copied separately while production was running. This
checks recoverability of each file, not a coordinated cutover checkpoint.
An off-host copy was also encrypted with Windows DPAPI (current-user scope).
Decrypting both files on that workstation reproduced the original checksums
and recovered revision 1028 and notification state. Recovery depends on that
Windows account and profile; portability to a new workstation was not tested.
Before cutover, stop writers, repeat the backup, and keep an encrypted
copy off-host. This rehearsal does not test loss of the home-server disk.

## Isolated FCM smoke check

Point a test client at the staging API and configure it with a separate
Firebase project. Set `FIREBASE_CREDENTIALS_PATH` to that project's server
credential. Run `python deploy/kubernetes/check_fcm_token.py --project <test-project>`
with a registration token from the test client. Add `--send` only for a real
device test. The helper sends to one token, never to a topic, and rejects
the production project. Verify notification receipt and board refresh on the
device; FCM acceptance alone does not prove delivery.

**Production decision: NO-GO.** State transfer and on-host and off-host
backup recovery were verified. A coordinated cutover backup and isolated
end-to-end FCM are pending.

## Android verification and cutover gates (2026-11-14)

A debug Android build used the existing Firebase project with production
topic subscription disabled. FCM accepted a direct message to one emulator
token. The matching notification appeared, and tapping it triggered a
successful board refresh against the current public API. This verifies
Android delivery and the tap handler, not the Kubernetes worker's ability
to publish with independently provisioned credentials.

Pre-cutover checklist:

- Provision a persistent cluster and PostgreSQL storage independent of the
  disposable kind lab. Verify restarts, backups, and resource limits.
- Stage the split API and worker with the schedule suspended. Verify an
  origin reachable by Cloudflare Tunnel, read parity, and direct-token
  delivery initiated by the staged runtime.
- Record the current tunnel origin, pinned Compose image, and a coordinated
  snapshot backup. Execute the writer handoff and route change only after
  these checks pass in a scheduled maintenance window.

## Home-server cutover (2026-11-14)

- The K3s production namespace runs FastAPI, PostgreSQL on a retained local
  volume under `/srv`, and a single scheduler using the existing adaptive
  market-hours loop. The experimental CronJob remains suspended.
- Cloudflare's API route now targets the production Service. Preserve its
  ClusterIP when updating the deployment; if the Service is recreated,
  verify its address and update only the API route.
- Snapshot revision 1035 was imported without renumbering. Both Compose
  state files were checksummed, and the files still matched immediately
  before stopping the old container. Compose was retained for rollback.
- Eight public read endpoints, an empty conditional 304, and the website
  were verified after cutover. The scheduler restored revision 1035,
  acquired the only advisory lock, initialized Firebase, and skipped
  scraping while the market was closed.
- A direct-token foreground FCM test from a staging pod reached the
  Android emulator. No topic message was sent during cutover. The first
  scheduled live scrape and topic delivery remain to be observed.

For rollback, scale the Kubernetes scheduler to zero and verify its pod
has terminated before starting the old writer. If PostgreSQL has advanced,
export its final snapshot and restore it to the Compose volume atomically
before starting the retained `gold-api` container. Verify its local
`/ready` and revision, then restore the Cloudflare API origin to
`http://127.0.0.1:8000` and verify the public API. Leave the Kubernetes
database intact until recovery is confirmed.
