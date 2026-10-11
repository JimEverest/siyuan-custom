# Azure SiYuan alpha.9 deployment procedure

This procedure applies only to the audited Azure VM and the October 2026 migration. It is not a generic unattended updater. The deployment scripts refuse an unexpected original image, mount, port, restart policy, workspace layout or existing migration state.

## Build identity

Build the official `v3.8.7-alpha.9` source at `def127fc774ab2b5ab9a0096609a15aa5ba30df4` using the four custom behavior patches and the Docker build-input correction in `patches/v3.8.7-alpha.9`. The workflow publishes a unique candidate tag and a source manifest. Deployment uses the resulting immutable registry digest, with the source revision and source-manifest digest checked against the image labels.

## Data and rollback protection

The production workspace is `/opt/siyuan/workspace`. Every part of it must be retained, including `conf`, `data`, `repo`, `history`, `temp`, database files and WAL files. Do not delete the latter directories as caches.

The private stage is `/home/nl/siyuan-upgrade-20261012-alpha9`, mode 0700. Original container inspection, configuration, file manifests and diagnostic logs belong only in this directory and must never be committed or published. The old container's writable layer is saved to a local private image; it may contain secrets and must not be pushed to a registry.

After a clean stop, copy the complete workspace to `/opt/siyuan/workspace-alpha9-candidate` without hard links. Check file bytes, permissions, ownership and modification times against the original manifest before first startup. The original workspace remains untouched during isolated testing. Copy old appearance packages removed from the new distribution into `data/icons` or `data/themes`, retaining any existing user package instead of replacing it. Verify the package bytes and the application's selected and available packages after startup.

The candidate has a Docker internal network and loopback port 16806. Sync and LAN sync are disabled before startup. Do not test a second kernel against the production workspace or let a copied workspace write to real remote sync storage.

After all isolated checks pass, stop the candidate and move the original workspace to the private stage's `workspace-before`. Move the validated candidate to the original production path, retain the stopped old container as `siyuan_custom_before_20261012_alpha9`, and create the new `siyuan_custom` using the original operational configuration and the new `serve` subcommand. Sync remains disabled through public HTTPS, authentication, browser, endpoint and Wrapper validation.

Only after production evidence passes, restore the original sync setting through `/api/sync/setSyncEnable`. The driver records this boundary before the request. Once remote writes may have occurred, an automatic rollback to the old local workspace is blocked: restoring local files alone cannot undo remote storage changes.

## Failure handling

Before sync release, rollback stops only this deployment's containers, preserves the migrated workspace and its new files, restores the untouched original workspace and old container, and checks the old version. No backup, old container, private image or failed workspace is deleted. Recovery tests exercise failures between directory renames and ensure the old kernel never opens migrated data.

The detached preparation runner uses an exclusive execution lock and an operation identifier. A duplicate invocation must fail without stopping or rolling back another run. A failed isolated test restores the old service only when the state belongs to the same runner.

## Acceptance evidence

- Exact authenticated version and immutable image/source identity.
- Readable notebook inventory and indexed documents.
- Every pre-existing note and attachment compared to its baseline SHA256.
- Three custom upload routes exercised with unique synthetic bytes, successful download/hash equality, cleanup, old-route absence and authentication checks.
- The currently deployed Wrapper's real client performs list, put/read and asset upload/read calls using its existing endpoint overrides.
- Fresh browser access-code login and an opened existing document render successfully.
- Selected icons and themes remain available; old material/ant packages retain their bytes.
- API token and existing remote storage settings remain equal without printing secrets.
- Unrelated container identities and public application health remain unchanged.

Plaintext SQLite databases, if present, receive a read-only quick check. Encrypted databases are not described as SQLite-checked; runtime document/API checks provide separate evidence. Neither a local snapshot nor a passed smoke test proves every feature or a complete remote sync round trip.

Keep the old workspace, container and private snapshot after success. Any later rollback must account for notes created since the upgrade and changes already synchronized to remote storage.

The operational scripts are retained in `ops/2026-10-12-alpha9`. `browser-smoke-local.py` runs on the operator's machine with Python Paramiko and Playwright, using an existing verified SSH host key and a password supplied through `SIYUAN_TASK_SSH_PASSWORD` for that process. It retrieves the existing access code in memory, does not save browser storage or note content, and emits only a small validation summary. Other scripts run in the private VM stage. `smoke.py` expects that stage's private `index-baseline.json`, collected from the old kernel before the migration, so its document-count assertion uses the real original index.
