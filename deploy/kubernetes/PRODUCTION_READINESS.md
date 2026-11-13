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

**Production decision: NO-GO.** The staging rehearsal is not a production
cutover. Before scheduling a cutover, rehearse state import/export preserving
revision, verify production storage and backups, and verify end-to-end FCM
with an isolated token and operational credentials.
