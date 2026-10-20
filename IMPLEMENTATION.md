# PNP prototype implementation

Project: `/Users/utok/Documents/Programming/pnp`

## Open the application

- MacBook dashboard: [https://localhost:8443/](https://localhost:8443/)
- Local monitor: [https://localhost:8443/monitor](https://localhost:8443/monitor)
- Admin devices on the current network: [https://192.168.100.55:8443/](https://192.168.100.55:8443/)

The IP address may change unless reserved in the router. The MacBook must stay awake. No external hosting or internet connection is needed after setup.

## Login and HTTPS setup

The generated initial login is stored privately at `/Users/utok/Documents/Programming/pnp/runtime/initial-login.txt`. Sign in, change the password using the dashboard's account menu → Change password, and remove the initial-login file afterward.

Trust the public certificate `/Users/utok/Documents/Programming/pnp/runtime/ca/rootCA.pem` on each participating device. The Mac keychain authorization was canceled during setup, so trust is still pending. Never share the CA private key. Trusted HTTPS is needed for webcam enrollment on another device; do not bypass a certificate warning as a substitute for trust.

## Implemented

- Login, password changes, staff/operator permissions, and audit records.
- Admin overview, searchable personnel directory, editing, deactivation, and reviewed-profile activation.
- Four-step browser enrollment with isolated persisted sessions, quality and pose checks, eight samples per angle, automatic five-angle capture, retakes, review, and activation.
- Independent local recognition service with webcam ownership, RetinaFace/ArcFace, ByteTrack, BLE-first checks, bounded gallery freshness, and persistent event retries.
- Verification history with search, filters, sorting, pagination, CSV export, details, and alert review.
- Local PostgreSQL and private files; Caddy HTTPS; start/stop, backup/restore, and optional login-startup utilities.
- Existing registrations and logs imported without modifying their originals. Seven profiles need official details/BLE review before activation. Fourteen historic sightings remain labeled as legacy sightings.

## Verified

- 33 backend tests against an isolated PostgreSQL database.
- 17 worker tests, including combined verification, missing BLE, ambiguous tags, stale gallery, and durable event retries.
- 25 preserved recognition/calibration tests, including real-model blank-frame inference.
- TypeScript check and production frontend build.
- Browser sign-in, dashboard, directory, and registration layout checks.
- Authenticated HTTPS requests against the running PostgreSQL-backed application.
- Backup restored successfully into a disposable database; original runtime data preserved.

## Remaining device checks

- Trust the local certificate and verify access from a second device on the actual network.
- Complete a real person's webcam enrollment and verification with the actual BLE tag.
- Measure sustained performance with enrollment and monitoring together.
- Optional automatic startup configuration is generated but not installed.

Direction is selected by the operator. The application does not claim physical crossing, occupancy, liveness protection, or gate-relay integration. The existing calibration workflow is preserved in the legacy app; the new worker loads its reviewed settings at startup.

See [SETUP.md](SETUP.md) for operating instructions. The original Flask source remains available alongside the revision.

## Preview performance correction

Removed the one-image-per-second display path. Camera capture and continuous streaming are independent of recognition; a latest-frame buffer prevents inference backlog. Overlays and status update separately. Targeted validation passed: 24 PostgreSQL backend tests, 10 worker tests, and a production frontend build. A synthetic 30 FPS source delivered approximately 27 FPS through the multipart transport; this is not a real-webcam or browser-rendering benchmark. The webcam remains off until the operator starts it.

## Registration regression correction

- Fixed raw JPEG uploads being rejected as HTTP 415 by the API parser; authenticated browser uploads retain CSRF protection and now queue successfully.
- Installed the already-declared missing `bleak` dependency in the project virtual environment. Startup now checks dependencies before launching services.
- Scan for Beacon requests a fresh scan and waits for completion, independent of the monitor camera. Scanner errors are visible. Weak tags are visible for enrollment; access verification retains its RSSI threshold.
- Restored the original Face Studio quality/pose functions, all eight front references for identity consistency, face thumbnails with the original 20% margin, and continuous Front → Left → Right → Up → Down capture (eight accepted samples per angle). Normal quality guidance does not terminate capture.
- Localhost registration pauses the native monitor camera before requesting the same webcam. The operator restarts monitoring after registration.
- Corrected registration, shared shell, directory, log-table, and dashboard proportions using saved high-fidelity Figma contexts. Bundled Inter locally and retained the supplied SVGs at their intrinsic sizes. Full 1:1 visual sign-off is **not complete**: Figma MCP Starter quota blocks additional design/screenshot retrieval, including the login frame. The narrow browser viewport was checked; the complete desktop flow and final completion state still need a full visual comparison.
- Validation: 26 PostgreSQL tests + 14 worker tests + 25 original tests pass; TypeScript and production build pass. The browser scan button completed a real scan without an error but found no compatible nearby beacon. A real webcam enrollment and verification with the physical tag remain device checks.

## Event photos, history detail and camera lifetime

Implemented the history-page layout from the supplied screenshot: comparison, final decision, claimed identity, BLE evidence and facial-recognition panels. Log rows navigate to bookmarkable `/logs/<event-id>` routes. Every new worker event includes a bounded JPEG gate capture; known BLE identities also retain the registration-image snapshot for the profile used at capture. Both are stored as PostgreSQL binary fields and served through private image endpoints. JSON polling returns URLs, not large image bodies. Original logs without gate images retain an explicit missing-image state; only version-matched registration images can be backfilled.

Camera ownership follows a dedicated authenticated server-sent-events connection for each monitor page, independent of video delivery. A heartbeat detects a closed browser connection even when the browser sends no pagehide cleanup request. Closing the final page releases the camera after a two-second reconnect grace; video interruption alone does not stop capture. Status responses started before a camera command are ignored, so they cannot overwrite the result of Start or Stop. The proxy uses normal request cancellation with automatic streaming flushes, and the backend has 16 threads to accommodate page and video connections. Socket tests verify startup, multiple pages, video interruption and final-page disconnection. Registration and monitoring recognition models, quality checks and thresholds are unchanged.

Validation: 33 PostgreSQL backend tests, 17 worker tests and 25 original recognition/calibration tests; TypeScript and production build. Browser row-to-detail navigation and the missing-photo comparison state were checked on existing records. No new artificial gate records were added to the live database, and the physical webcam stayed off during verification. A physical gate capture with the phone beacon remains the hardware check.

Browser-specific streaming validation: preview and presence routes use authenticated Django streaming responses instead of JSON-only DRF content negotiation. Regression requests send image/* and text/event-stream Accept headers. Presence reconnects after 500 ms, within the two-second disconnect grace.

Final browser verification (2026-10-19): an isolated backend and the Caddy proxy recovered from an intentional eight-second video outage without stopping capture. Two open monitor pages retained capture after one closed; closing the final page released the camera. A bounded real-webcam check delivered 1280x720 preview at approximately 30 FPS with recognition disabled and no recorded images/events, then released the webcam after the final tab closed. Backend tests: 35 passed; worker tests: 18 passed; production frontend build and Caddy configuration validation passed.

## Camera first-start and restart fixes (2026-10-20)

Each camera start now has a distinct session ID carried by status and the preview URL. Stop removes the preview immediately, and capture releases/reopens hardware even when Stop and Start happen between capture-loop iterations. Old preview connections and in-flight recognition results cannot publish into a new session. Preview reconnects no longer depend on the browser reporting an error at multipart EOF.

The worker warms both ONNX models and the ByteTrack/supervision dependency before reporting recognition ready. The latter previously built a temporary matplotlib font cache on the first live detection, blocking face boxes for several seconds. The worker now retains that disposable cache under runtime/font_cache and reports loading explicitly. Face boxes are published before embedding decisions, and overlay freshness is measured at publication. Recognition models and access thresholds are unchanged.

Validation: 21 worker tests, 36 backend tests, production frontend build, real model warmup on blank frames, and browser stop/start cycles using synthetic detections through the real recognition loop and proxy. No production personnel records were used in browser tests.

Cold-start browser check after tracker preloading: the first simulated face rectangle appeared within the 1.5-second assertion window (approximately 0.7 seconds for the complete click/wait/state check), with no camera restart or page reload. Isolated browser test services were shut down afterward.

## Continuous BLE monitoring (2026-10-20)

The scanner keeps one BleakScanner active and publishes advertisement snapshots every 250 ms (BLE_UPDATE_INTERVAL, minimum 50 ms). It no longer performs two-second discovery windows separated by five-second waits. Each device retains the actual advertisement timestamp across worker IPC; publishing a snapshot never renews identity freshness. Missing advertisements expire independently after three seconds by default (BLE_RESULT_TTL). RSSI and identifier matching rules are unchanged. The five-second interval now applies only to recovery after scanner failure (BLE_RETRY_INTERVAL; BLE_SCAN_INTERVAL remains a fallback for existing environments).

Registration requests a fresh snapshot from the existing scanner and accepts continuous-scan updates rather than waiting for scanning to stop. Scanner errors remain visible, and stopping or losing the scanner clears cached identities.

Validation: six new BLE tests plus 21 worker and 25 original tests passed (52 total); production frontend build passed. A bounded scan on the MacBook received 12 continuous updates over three seconds and was terminated afterward. Phone-specific arrival latency still depends on beacon advertising behavior.
