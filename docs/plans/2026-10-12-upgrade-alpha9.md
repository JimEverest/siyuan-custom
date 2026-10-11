# SiYuan 3.8.7 alpha.9 Upgrade Implementation Plan

> **For Codex:** Execute in this session with independent patch and build review. Production access is required before backup or deployment.

**Goal:** Upgrade the existing customized SiYuan 3.5.8 service to v3.8.7-alpha.9 while retaining custom behavior and all notes.

**Architecture:** Build official source tag v3.8.7-alpha.9 at def127fc774ab2b5ab9a0096609a15aa5ba30df4 with the existing four custom patches ported to that source. Preserve the production container layer and consistent complete workspace before replacing only the SiYuan service.

**Tech Stack:** Go, Node/pnpm, Docker linux/amd64, GitHub Actions, Ubuntu/Azure, Caddy.

## 1. Reproducible source and patches

- Keep existing v3.5.8 patch set usable; add a version-specific patch set if needed.
- Port disabled updater, new-workspace defaults, local mock account, and renamed upload endpoints.
- Preserve endpoint authentication, admin and read-only checks; include all new upstream call sites.
- Verify all required patches apply to the pinned clean source and inspect generated source diff.

## 2. Build candidate

- Require all patches, validate version input and pin source revision.
- Use a unique alpha candidate image tag; do not replace latest during preparation.
- Compile frontend and kernel; build upstream OCR worker/runtime requirements.
- Verify source/patch hashes and immutable image ID/digest.

## 3. Live production preflight (requires SSH)

- Verify VM identity, target container, image, configuration, mounts, ports, capacity, and all unrelated services.
- Inspect writable-layer differences and preserve any extra custom files, themes and icons.
- Prepare a protected backup directory; never print access codes, tokens, notes or raw container inspect.
- Confirm sufficient capacity for the old container snapshot, full workspace backup and test copy.

## 4. Isolated validation

- Use a separate workspace and loopback port; disable sync and outbound jobs on any production-data copy.
- Verify alpha.9, configured defaults, local account behavior, update suppression, and endpoint auth.
- Upload synthetic assets through the three renamed routes and read back with byte/hash equality.
- Test Wrapper client asset upload and file put/read; inspect application JSON codes.
- Test startup against a consistent protected copy of existing data and verify document/asset inventory.

## 5. Backup, switch, verify, rollback

- Stop writers for a consistent full backup including conf, data, repo, history, temp/databases and WAL files.
- Preserve the old stopped container and snapshot writable layer privately; verify backup archive and restore into isolated directory.
- Start candidate with actual production configuration and new serve subcommand; never open one workspace with two kernels.
- Validate authenticated version, notes/attachments inventory, upload compatibility and public HTTPS; verify unrelated services.
- On failure stop candidate, preserve post-upgrade data, restore pre-upgrade workspace with old container; no blind in-place downgrade.
- Record actual downtime, evidence, immutable artifacts and rollback paths. Do not claim deployment until verified.

## Current access constraint

The host at 20.192.24.1 responds with the known SSH host key, but this session has no usable SSH identity. Repository configuration intentionally does not retain VM passwords. User has been asked for an existing private credential path or loaded SSH agent key. No production changes have been made.
