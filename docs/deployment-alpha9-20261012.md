# Azure SiYuan upgrade — completed 2026-10-12

The complete Chinese retrospective and future upgrade checklist are in [定制思源升级复盘与后续 Agent 操作指南](upgrade-retrospective-3.5.8-to-3.8.7-alpha9.zh-CN.md).

The Azure service was upgraded from the customized 3.5.8 installation to **3.8.7-alpha.9**. It is running at the existing HTTPS address, with the original authentication, upload endpoint overrides, local account behavior, appearance resources, settings and notes retained. This is the explicitly selected prerelease version.

- Production URL: https://obslinux.centralindia.cloudapp.azure.com/
- Production ready: **2026-10-12 00:17:02 UTC / 08:17:02 Asia/Taipei**.
- Final verification: **2026-10-12 00:19:50 UTC / 08:19:50 Asia/Taipei**.
- Production container: `siyuan_custom`, ID `96a9ae752f23f50fd8aee1e434187a7836c7b902d72e647bcb50aa1b61a04fd4`.
- Existing mount `/opt/siyuan/workspace → /siyuan/workspace`, loopback port `127.0.0.1:6806`, and restart policy `always` are retained. Docker logging remains the original `json-file` configuration without rotation options.

## Build provenance

| Item | Verified value |
| --- | --- |
| Official source tag | `v3.8.7-alpha.9` |
| Official source commit | `def127fc774ab2b5ab9a0096609a15aa5ba30df4` |
| Custom image build commit | `15ebf14380cbb0f5f009b38b54e3f9f9245c46ba` |
| Candidate tag | `ttqwer1/siyuan-custom:v3.8.7-alpha.9-candidate-38162570597-1` |
| Deployed immutable image | `ttqwer1/siyuan-custom@sha256:b722afa234debc64c2a9a3de2ae62001281212c31c26417132fefd7da7841b02` |
| Source manifest SHA256 | `04c8e87aef738c3375f42ddc3d2baef0ed92c2f83749229ccc7a725cc7d51c15` |
| Platform actually built and deployed | `linux/amd64` |

[CI run 38162570597](https://github.com/JimEverest/siyuan-custom/actions/runs/38162570597) passed the Go/API checks, frontend and kernel build, native ONNX/OCR build, image publication and provenance generation. [PR #1](https://github.com/JimEverest/siyuan-custom/pull/1) retains the upgrade branch for review. Later operational-script and documentation commits do not change the deployed image's build revision.

## Actual acceptance results

| Check | Result |
| --- | --- |
| Version, startup and notebook inventory | `3.8.7-alpha.9`, boot complete, **33 notebooks** readable |
| Original note files | **1,858** `.sy` files have unchanged SHA256 after migration and again after production browser validation |
| Original attachment files | **3,954** files have unchanged SHA256 in candidate and production |
| Existing indexed documents | Original index baseline of **1,840** document records remains readable; file and index counts are different measurements |
| Custom upload routes | `/u_OCR`, `/api/file/p_ocr`, `/api/asset/ass_ocr` passed unique synthetic upload/download/hash tests, authorization checks, and cleanup |
| Local account and updater behavior | Cached local user and disabled update/download behavior passed |
| Existing Wrapper | The deployed Wrapper image's real client passed notebook listing, file put/read and asset upload/read using its current endpoint overrides |
| Native OCR | Actual candidate worker recognized `SiYuan OCR 123`; inference 0.382 seconds, synthetic input removed. An independent image-only probe also passed |
| Configuration identity | **95 checks passed** in candidate and again in production: API token, repository key, device identity, UI layout, original storage settings, and AI migration semantics |
| Preferences | **115 existing fields unchanged**, with the source-defined graph default normalization explicitly accepted |
| Appearance | Original icon resources preserved; selected `legacy-material`, `daylight` and `midnight`; all original appearance preferences verified under documented migrations |
| Fresh browser session | Original access-code login succeeded, existing document rendered with 68 blocks, **0 page errors and 0 API server errors** |
| Other services | **7 other running container IDs/images unchanged**; JSON validator and formatter public checks returned HTTP 200 |
| Final runtime | Running, no OOM, no fatal/panic/out-of-memory log match in the observed post-start interval |
| Sync | Original enabled state and S3 provider/configuration restored; one successful sync phase observed, zero `sync data repo failed` messages in the observation window |

No real note was edited for acceptance. Synthetic upload files were unique and removed. Configuration values, credentials, note content and raw logs remain in the private VM stage; [the committed evidence](deployment-alpha9-evidence.json) contains only sanitized verification results and deployment identifiers.

Host SQLite completed `quick_check` for one database. Three SiYuan FTS5 databases require the application's custom `siyuan` tokenizer, which host Python SQLite does not provide; their checks are explicitly recorded as unsupported, **not passed**. Application SQL/index and document reads passed separately. No second-client bidirectional remote-sync round trip was performed, and the native OCR probe is not a test of every HTTP OCR provider.

## Compatibility work and recovery

The new appearance cache schema caused partial decoding of the 3.5.8 configuration. Recovery preserved each failed candidate configuration, restored the complete original `conf`, removed only obsolete derived caches, and allowed the pinned upstream migrations to run. The old `material` and `ant` icon packages are preserved, with additional `legacy-material` and `legacy-ant` aliases that change only the metadata name. The selected icon's visual resources retain their original bytes. Language `en_US → en` and graph default `10240 → 16384` follow the new upstream schema.

Three isolated preparation attempts restored the old service automatically while compatibility and verification issues were resolved. The fourth attempt passed all checks and was promoted. Its final stop-to-ready interval was 00:12:33–00:17:02 UTC (4 minutes 29 seconds); the previous isolated attempts had separate maintenance intervals. No old kernel opened migrated data. [The operations procedure](operations-alpha9.md) records the reviewed recovery rules and the exact SQLite validation limitation.

## Protected rollback material

- Original complete workspace: `/home/nl/siyuan-upgrade-20261012-alpha9/workspace-before`.
- Original stopped container: `siyuan_custom_before_20261012_alpha9`, restart policy `no`.
- Latest private runtime-layer snapshot: `siyuan-custom-private:before-20261012-alpha9-retry4`, image ID `sha256:14e55fd302769797aed655476304e1166764ce774f0efc2ee33d80e424be81a0`.
- Full original manifest: **25,456 files**, SHA256 `d4f6c5796ca0aa527ff2def75c49ba703bb4e5ff9dc10cd5249aeb4ac104f732`; final verification matched all recorded file bytes, metadata and links.
- Private state, prior attempts, configuration and evidence: `/home/nl/siyuan-upgrade-20261012-alpha9`, mode `0700`.

These are **VM-local** recovery materials, not an off-VM backup. They include `repo`, history, temporary databases and WAL files; none should be treated as disposable cache. Private container snapshots may contain secrets and must not be pushed to a registry. The owned test proxy on port 16806 was stopped after final validation. Approximately 4.63 GB of filesystem space remained at final verification.

The sync-release boundary has been crossed. A subsequent rollback must account for new local notes and remote storage changes; do not simply start the old kernel against the current workspace or overwrite current data with this older backup.
