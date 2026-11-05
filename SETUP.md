# PNP same-network prototype

PNP Access Control uses React, Django, local PostgreSQL, and a native Python recognition service. No external account or hosting subscription is required.

## First use

1. Open `https://localhost:8443/` on the MacBook after starting the services.
2. Read the generated administrator login in `runtime/initial-login.txt`. This file is private and excluded from Git. Do not share it publicly.
3. Sign in, open the account menu and choose **Change password**, set a new password, and delete `runtime/initial-login.txt` afterward.
4. Open **Personnel** to review imported registrations or register a new person.
5. Open **local monitor** on the MacBook, then choose **Start camera**. Allow macOS camera permission for the process/terminal running Python if prompted. The camera is not started automatically. It stops about two seconds after the last live monitoring preview disconnects; closing an admin-only page does not stop another open monitor.
6. Admins on the same network open the LAN HTTPS address printed by `scripts/start.py`. The current address is also recorded in `runtime/config.json` under `hosts`.

### Certificate trust on each admin device

Browser webcam enrollment needs trusted HTTPS. Install **only** `runtime/ca/rootCA.pem` as a trusted root certificate on each participating device. The certificate is public; the CA private key `runtime/ca/rootCA-key.pem` and server key `runtime/lan-key.pem` must never be shared.

On the MacBook, trust the CA in the login keychain with:

```sh
security add-trusted-cert -r trustRoot -k "$HOME/Library/Keychains/login.keychain-db" runtime/ca/rootCA.pem
```

On another Mac, import the CA certificate into Keychain Access and explicitly trust it for SSL. On iPhone/iPad, install the profile and enable root certificate trust in Settings. On Windows, install it into the current user's Trusted Root Certification Authorities. Browser-specific trust may also need configuration, especially Firefox. Verify there is no HTTPS warning and that webcam permission works on the actual device before the demonstration. Bypassing a certificate warning is not a substitute for trust.

Use a router DHCP reservation for a stable MacBook IP. If the IP changes, stop the application, run setup again to refresh the host list and certificate, and restart. The same CA is reused. Guest Wi-Fi/client isolation may prevent other devices reaching the MacBook even if the Wi-Fi name matches.

## Install and build

Prerequisites: native ARM64 Python compatible with dependencies, Node.js 22+ or 24 LTS and pnpm, Homebrew PostgreSQL 17, Caddy, and mkcert.

```sh
brew install postgresql@17 caddy mkcert
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
.venv/bin/python scripts/setup.py
```

Setup creates a project-specific PostgreSQL cluster at `runtime/postgres`, listening only at `127.0.0.1:55432`. It does not use the Homebrew default cluster. It generates random secrets and an initial admin account. Running setup again preserves existing credentials and database records and refreshes LAN names/certificates.

The frontend and runtime use no required CDN assets. Download the model weights with the existing `scripts/download_models.py` utility before an offline demonstration.

## Start and stop

```sh
.venv/bin/python scripts/start.py
.venv/bin/python scripts/stop.py
```

Start prints the usable HTTPS addresses. Internal backend and worker ports are `127.0.0.1:8000` and `127.0.0.1:8766`. Only the HTTPS reverse proxy listens on the LAN. PostgreSQL remains running after `stop.py` for maintenance. Stop it separately if needed:

```sh
/opt/homebrew/opt/postgresql@17/bin/pg_ctl -D runtime/postgres stop
```

Logs are in `runtime/backend.log`, `runtime/recognition.log`, `runtime/https.log`, and `runtime/postgres.log`. Keep the MacBook awake during use. Internet loss is harmless after setup, but loss of local networking disconnects other admin devices. MacBook sleep/shutdown stops the entire prototype.

## Import existing data

```sh
.venv/bin/python backend/manage.py import_legacy
```

This command is repeatable and leaves `registration/`, existing photos, model archives, and `data/entries.jsonl` unchanged. Imported records are **Incomplete** because the old app has no official personnel ID, rank, unit, or reliable BLE assignment. Complete those fields through **Edit**. Choose **Activate reviewed profile** in Edit to approve an imported profile after completing the required fields. The equivalent local command is:

```sh
.venv/bin/python backend/manage.py activate_import OFFICIAL-PERSONNEL-ID
```

Old sightings retain their own `legacy_sighting` type and are excluded from current verification counts. Import does not pretend the old sightings are successful face-plus-BLE checks.

## Enrollment and monitoring

- Registration uses the admin device's browser camera, not a remote view of the MacBook camera.
- Enable the camera, then choose **Begin registration**. As in the original Face Studio, capture continues through Front, Left, Right, Up, and Down automatically, collecting eight accepted samples per angle (40 total). Follow the guidance; pose/lighting feedback does not stop capture. Use **Pause capture** or **Retake** when needed. Pose, quality, and identity consistency use the original Face Studio checks on the MacBook. On localhost, starting registration releases the monitor camera first; start the monitor camera again when registration finishes.
- Resuming a draft retains accepted samples. Retaking Front resets all samples because it changes baseline pose and identity evidence.
- BLE scanning always occurs near the MacBook. Enter the identifier flashed on the ESP32 or choose a discovered tag. Wi-Fi proximity does not imply Bluetooth proximity.
- A single native service schedules both enrollment inference and monitoring. Processing does not occur inside web request workers. Enrollment jobs persist across service restarts.
- The host camera has one owner. Use another device on the LAN for simultaneous enrollment so the host monitor does not compete for webcam access.
- Final attempt decisions require fresh gallery information and both assigned BLE evidence and matching face evidence. Multiple faces/tags produce ambiguity rather than selecting the strongest radio signal.
- Direction is an operator-selected prototype mode. Approval is not proof of physical entry/exit. Occupancy and relay-control status are not fabricated.
- The old calibration implementation and saved settings remain intact. The new worker reads reviewed `data/recognition_settings.json` values at startup; the original calibration UI remains in the legacy app. Stop the new camera before using legacy camera/calibration workflows, and restart the new worker after changing reviewed settings.

## Accounts

Create additional accounts locally:

```sh
.venv/bin/python backend/manage.py createsuperuser
```

Staff accounts manage personnel and logs. Non-staff authenticated accounts can access local monitoring only. There is no public sign-up page. Admins can change their own password in the dashboard. For an operator password reset, use `manage.py changepassword USERNAME` on the MacBook.

## Optional automatic startup

```sh
.venv/bin/python scripts/install_launchd.py
```

This generates `runtime/local.pnp.supervisor.plist` without installing it. To opt in, first stop manually started services, then copy the generated file into `~/Library/LaunchAgents/` and bootstrap it with `launchctl`. Unload it before using manual start/stop commands. The supervisor restarts failed application services; the camera still requires Start camera. Test macOS camera/Bluetooth permissions under the intended launch environment before relying on unattended startup.

## Backup and restore

Stop application services, then run:

```sh
.venv/bin/python scripts/backup.py /path/to/protected/pnp-backup.tar.gz
```

The archive contains a PostgreSQL custom-format dump, private images, gallery cache, and event outbox. It contains private biometric data. Keep it outside the project and protect access. Validate a trusted archive without overwriting live data using `.venv/bin/python scripts/verify_backup.py /path/to/pnp-backup.tar.gz`; this restores into a disposable database, checks linked sample files, and removes the validation database.

Restore into a separate freshly configured installation for verification, with application services stopped. Inspect/extract the trusted backup into a temporary directory; restore `database.dump` using PostgreSQL `pg_restore` against the fresh database, then copy `media/`, `gallery.json`, and `outbox.sqlite3` into that installation's runtime directory. Keep the destination installation's config, CA, and server keys. Verify records and media agree before using it. Do not restore over live data without a separate backup.

## Validation

```sh
PNP_TEST_SQLITE=1 .venv/bin/python backend/manage.py test access
.venv/bin/python -m unittest tests.test_worker -v
BLE_DISABLED=1 .venv/bin/python -m unittest discover -s tests -v
cd frontend && pnpm run build
```

The application uses PostgreSQL in normal operation. The isolated unit-test command uses temporary SQLite to avoid changing the local PostgreSQL role permissions. Run `.venv/bin/python scripts/test_postgres.py` for the same backend suite against a disposable PostgreSQL database without granting the application role database-creation privileges. The script refuses to overwrite a pre-existing test database.

Hardware verification still requires an actual person, camera permission, and an ESP32 broadcasting the assigned BLE identifier. The system does not provide liveness detection or cryptographic BLE ownership verification.

### Current verification bounds

The gallery refreshes approximately every two seconds. Grants require a successful gallery refresh within the previous 30 seconds; older caches can still support detection, but final attempts become service-unavailable until the backend recovers. A service restart leaves the camera off until the operator starts it again.

The deployment checker retains the HTTPS redirect/HSTS suggestions intentionally: LAN traffic enters through the TLS-only Caddy listener, while authenticated internal APIs use loopback HTTP. HSTS is not forced for local certificate setup. PostgreSQL and backend/worker listeners are not exposed to other network devices.

## Smooth monitor preview

The monitor uses a continuous authenticated local MJPEG stream. A dedicated capture thread publishes the newest camera frame independently of face inference, enrollment processing, and backend synchronization. Recognition consumes the newest available frame without building a frame queue; SVG face overlays update separately and expire when stale. Preview FPS and recognition FPS are displayed separately. Refresh an already open monitor tab after upgrading. The face models and decision thresholds are unchanged by this preview fix.

### If registration cannot scan or capture

- **Scan for Beacon** now requests a fresh scan from the MacBook and waits for its result. It works with the monitor camera off. A remote admin still scans beacons near the MacBook, using that MacBook's Bluetooth receiver.
- Install the full dependencies with `.venv/bin/python -m pip install -r requirements.txt`; `bleak` and its macOS dependencies are required. Startup checks for missing dependencies.
- If a scan reports a permission error, enable Bluetooth and allow the app running Python (VS Code or Terminal) under **System Settings → Privacy & Security → Bluetooth**. The scanner displays the actual error instead of silently showing no beacons.
- Weak beacons remain visible for enrollment, like the original version. Live access verification still enforces the existing RSSI limit.
- On the same Mac, registration and monitoring share one physical webcam. Registration on `localhost` pauses the monitor camera before opening the browser webcam. Start the monitor camera again afterward.
- For another computer on the LAN, open the HTTPS admin address and trust the local certificate using the instructions above. Browser webcam access requires a secure context.

## Entry photos and record history

- Each new entry/exit verification attempt stores a cropped gate photo and the registration photo for the claimed BLE identity directly in PostgreSQL. Unknown people still receive a gate capture; no registration photo is invented for them.
- Click a log row to open **Log Entry History**. It shows the two photos, the stored decision, claimed identity, BLE evidence, face similarity, and the threshold recorded with that attempt. The percentage is the similarity score scaled for display, not a calibrated identity probability. Recognition thresholds and decision rules are unchanged.
- Log-row circles, recent activity, alerts, and the monitor use the gate photo. Personnel-directory circles use the enrollment photo. Missing images show a silhouette instead of initials.
- The registration photo is frozen with each event. Editing or replacing personnel data does not rewrite old comparisons. Outbox retries retain both photos during a backend outage.
- Existing gate captures cannot be recovered when they were never saved. The migration leaves these explicitly missing. `backfill_profile_photos` imports existing enrollment photos and restores historic registration snapshots only when the saved profile version exactly matches; it never creates a gate capture.
- Image URLs require login and staff access; current-monitor images are available only to authenticated clients on the MacBook. Images are not published as static assets.
- Camera lifetime follows active MJPEG preview connections rather than browser timers. One remaining monitor keeps the camera open. Closing the last monitor or navigating away stops it after a short reconnect grace period; reopening the monitor requires **Start camera** again. BLE scanning and the admin server stay available with the camera off.
- Backups include the database image blobs and the offline photo cache/outbox. A pre-migration backup was saved under `runtime/backups/` before adding photo columns.

### BLE monitoring timing

BLE scanning is continuous. Default settings are BLE_UPDATE_INTERVAL=0.25 seconds and BLE_RESULT_TTL=3 seconds. BLE_RETRY_INTERVAL=5 seconds applies only after scanner failure; it does not pause a healthy scanner. The beacon's own advertising interval still affects detection latency. Set these environment variables before starting services if adjustments are needed, and restart services after changing them.
