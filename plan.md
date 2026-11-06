# Multi-Person "Stuck at Verifying" Bug Fix Plan

## 1. Problem Summary

When two or more people appear at the monitoring camera (either simultaneously or sequentially as one person walks away), a person on the monitor can get stuck displaying `"Verifying · X.Xm"` on their face reticle and/or fail to transition to `"Verified"`.

Investigation of [`services/recognition/worker.py`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py) identified **three deterministic root causes** and **two boundary edge cases**:

1. **Bug 1 — Denied/Terminal Outcomes Display `"Verifying"` as the Overlay Label ([`resolve_overlay_stage`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L353-L374) & [`Monitor.process`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L989-L991)):**
   - `resolve_overlay_stage` sets `default_prompt = "Verifying" if candidates else "Waiting for assigned BLE tag"`.
   - For any non-verified terminal `outcome` (`"ambiguous"`, `"face_mismatch"`, `"timeout"`, `"service_unavailable"`, `"missing_ble"`, `"unknown_tag"`), `resolve_overlay_stage` returns `("denied", warning or default_prompt)` (or `f"{person['name']} · {default_prompt}"`).
   - Once a track is recorded in `self.logged` and `logged_outcomes`, Pass 2 hardcodes `effective_warning = None` ([`worker.py:991`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L991)). Whenever any unclaimed BLE candidate is in range (`candidates` is non-empty), `warning or default_prompt` evaluates to `"Verifying"`, so a denied track renders a red reticle whose text literally says **`"Verifying"`** (or **`"<Name> · Verifying"`**).
   - Similarly, during `stage="evaluating"` (`outcome is None`, [`worker.py:362-365`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L362-L365)), if a face is already confirmed as a known gallery person (`person` is not `None`) whose assigned BLE tag is **not** in `candidates`, `if person and not candidates:` evaluates to `False` whenever another person's unclaimed BLE tag is present—displaying `"Verifying"` instead of `"<Name> · Waiting for assigned BLE tag"`.

2. **Bug 2 — Non-Verified Outcomes Permanently Freeze the Track ([`Monitor._record_event`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L770) & [`Monitor.process`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L862-L991)):**
   - `_record_event` adds `track` to `self.logged` for **every** outcome (`"verified"`, `"ambiguous"`, `"timeout"`, `"face_mismatch"`, `"missing_ble"`, `"unknown_tag"`), and caches `logged_outcomes[track] = (outcome, event_person, result)`.
   - Once `track in self.logged`:
     - Line 862 (`if track in self.logged: continue`) permanently halts ArcFace feature extraction for that track.
     - Lines 928–935 (Pass 1) and lines 989–991 (Pass 2) permanently bypass `decision()` and `evaluate_verification_outcome()`, replaying the cached failure for the remainder of the track's lifetime.
   - Furthermore, when another person in the frame is verified (`active_verified_in_frame = True`), `preserve_latest=True` ([`worker.py:1080-1082`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L1080-L1082)) suppresses updating `self.latest`, so the second person's track silently locks up in the background and can never recover even after the first person leaves, after they turn toward the camera, or after their BLE beacon arrives.

3. **Bug 3 — Verified Person's BLE Claim Evaporates the Instant Their Face Leaves the Frame ([`Monitor.process` Pass 1](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L920-L935)):**
   - `candidate_claims` is rebuilt from scratch every frame by iterating only over `tracked_items` (faces detected in the current frame).
   - When Verified Person A steps out of the camera view, Person A is no longer in `tracked_items`, but Person A's physical BLE tag remains active in `BLEScanner` for up to `BLE_RESULT_TTL = 5.0s` ([`ble_scanner.py:16`](file:///Users/utok/Documents/Programming/pnp/services/recognition/ble_scanner.py#L16)).
   - On the very next frame, Person A's BLE tag becomes an `unclaimed_candidate` for Person B (who is still in front of the monitor), flipping Person B to `"Verifying"` against Person A's already-used BLE credential and triggering false `"face_mismatch"` or `"ambiguous"` outcomes.

4. **Edge Case 4 — Pass 1 BLE Claim Requires `gate["triggered"]` ([`worker.py:954`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L954)):**
   - If a confirmed BLE tag owner is visible and approaching (`kinematics_ready=True`) but slightly behind a companion (`scale_ready=False`, `face_size < 100px`), Pass 1 prevents the owner from claiming their own BLE tag, leaving it in `unclaimed_candidates` for the closer companion.

5. **Edge Case 5 — Single-Frame `gate["triggered"]` Jitter Resets `self.first[track]` ([`worker.py:845-848`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L845-L848)):**
   - If a face hovers near the 100px threshold or has a single-frame bbox shrink (`scale_velocity < -25.0px/s`), `gate["triggered"]` drops to `False` for one frame and `self.first.pop(track, None)` resets `elapsed` to `0.0`, preventing timeout progression and keeping the track in `"Verifying"` indefinitely.

---

## 2. Detailed Implementation Plan

### Step 1: Fix Overlay Label Resolution in [`resolve_overlay_stage`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L353-L374)

Update [`resolve_overlay_stage`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L353-L374) so that:
1. Terminal non-verified outcomes never fall back to `"Verifying"`. Define explicit human-readable denial labels:
   - `"missing_ble"`: `"Waiting for assigned BLE tag"` (or `f"{person['name']} · Waiting for assigned BLE tag"` when `person` is known)
   - `"unknown_tag"`: `"Unknown BLE tag"` (or `f"{person['name']} · Waiting for assigned BLE tag"` when `person` is a known gallery match whose assigned tag isn't present)
   - `"ambiguous"`: `"Multiple people · Step forward one at a time"` (or `warning` if present)
   - `"face_mismatch"`: `"Face mismatch"`
   - `"timeout"`: `warning or "Verification timed out"`
   - `"service_unavailable"`: `"Service unavailable"`
2. During active evaluation (`outcome is None` and `is_triggered` is `True`):
   - Check whether the confirmed `person` actually has their own ID in `candidate_ids = {p["id"] for p in candidates}`.
   - If `person` is confirmed (`person is not None`) and `person["id"] not in candidate_ids`, return `("evaluating", f"{person['name']} · Waiting for assigned BLE tag")` even if another person's unclaimed BLE candidate is in `candidates`.

```python
DENIED_OUTCOME_LABELS = {
    "missing_ble": "Waiting for assigned BLE tag",
    "unknown_tag": "Unknown BLE tag",
    "ambiguous": "Ambiguous · Multiple people in zone",
    "face_mismatch": "Face mismatch",
    "timeout": "Verification timed out",
    "service_unavailable": "Service unavailable",
}

def resolve_overlay_stage(outcome, person, is_triggered, gate, warning, candidates):
    """Determine UI stage and human-readable status label for a tracked face."""
    candidate_ids = {p["id"] for p in candidates}
    has_matching_candidate = (
        (person["id"] in candidate_ids) if person else bool(candidates)
    )
    default_prompt = (
        "Verifying" if has_matching_candidate else "Waiting for assigned BLE tag"
    )
    if outcome == "verified" and person:
        return "verified", person["name"]
    if outcome:
        if person and outcome in ("missing_ble", "unknown_tag"):
            return "denied", f"{person['name']} · Waiting for assigned BLE tag"
        reason = warning or DENIED_OUTCOME_LABELS.get(outcome, "Access denied")
        if person and outcome not in ("face_mismatch", "ambiguous"):
            return "denied", f"{person['name']} · {reason}"
        return "denied", reason
    if is_triggered:
        if person and not has_matching_candidate:
            return "evaluating", f"{person['name']} · Waiting for assigned BLE tag"
        return "evaluating", warning or default_prompt
    if not gate["kinematics_ready"]:
        label = (
            "Passerby ignored"
            if gate["is_lateral_passerby"]
            else "Outside approach zone"
        )
        return "bystander", label
    return "approaching", f"Approaching · {gate['distance_m']:.1f}m"
```

---

### Step 2: Allow Non-Verified Tracks to Recover Instead of Permanently Freezing in `self.logged`

In [`Monitor.process`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L858-L1086):
1. **Separate terminal `"verified"` lock from recoverable failure states:**
   - Only skip ArcFace feature extraction ([`worker.py:862`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L862)) and bypass Pass 1 / Pass 2 evaluation when a track has already achieved `outcome == "verified"` (`logged_outcomes.get(track, (None,))[0] == "verified"`).
   - If a track previously hit a non-verified outcome (`"ambiguous"`, `"missing_ble"`, `"unknown_tag"`, `"timeout"`, `"face_mismatch"`, `"service_unavailable"`):
     - Keep `track in self.logged` (or check `logged_outcomes.get(track)[0] == outcome`) so the worker **does not spam duplicate failure events** to `self.outbox` on every subsequent frame for the same failure state.
     - Continue running `decision()` (at the normal cooldown) and `evaluate_verification_outcome()` on subsequent frames.
     - If conditions improve while the person is still at the camera (e.g., the other person claims their tag or leaves, the person's BLE beacon arrives, or 3 clean face samples confirm their identity), allow the track to transition to `outcome == "verified"`, record the `"verified"` event, update `self.latest`, and lock the track in the `"verified"` state.
     - While the track has not yet verified, if a prior failure outcome was logged in `logged_outcomes[track]` and the current frame's `evaluate_verification_outcome` returns `None` (for example, because `elapsed` was reset or a new candidate appeared that matches their face), allow the track to either show its active progress or retain the logged failure outcome until conditions actually change (`outcome = current_outcome or prior_outcome` unless a matching BLE candidate is now available for recovery).
     - Specifically, when should a previously failed track return to `"evaluating"` vs stay `"denied"`?
       - If the track still fails verification (`current_outcome` is non-None, or `elapsed > 4` with no matching BLE candidate), it remains `"denied"` with the explicit denial label (and does not re-emit duplicate outbox events if `prior_outcome == current_outcome`).
       - If the track subsequently achieves `current_outcome == "verified"`, it immediately upgrades to `"verified"`, records the verification event, and locks as verified.

---

### Step 3: Persist Verified BLE Claims Across Frame Departures (`verified_claims` TTL)

In [`Monitor`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L918-L976):
1. Maintain a `self.verified_claims` dictionary mapping `person_id -> (owner_track, last_seen_monotonic)`.
2. Whenever a track is verified (either newly verified in Pass 2 or active in Pass 1), update `self.verified_claims[pid] = (track, now)`.
3. At the start of Pass 1:
   - Prune entries from `self.verified_claims` where `now - last_seen > BLE_CLAIM_RETENTION_S` (e.g., `5.0` seconds, matching `BLE_RESULT_TTL = 5.0` in [`ble_scanner.py`](file:///Users/utok/Documents/Programming/pnp/services/recognition/ble_scanner.py#L16)).
   - Seed `candidate_claims` with active `self.verified_claims` for any `pid in candidate_ids`:
     ```python
     for pid, (owner_track, seen_at) in list(verified_claims.items()):
         if now - seen_at > BLE_CLAIM_RETENTION_S:
             verified_claims.pop(pid, None)
         elif pid in candidate_ids:
             candidate_claims[pid] = (3, 1.0, 0.0, owner_track)
     ```
4. Because `owner_track` is recorded in `claimed_by_track`, any *other* track (`t != owner_track`) in Pass 2 will see `pid in claimed_by_others`, excluding the departed officer's lingering BLE advertisement from `unclaimed_candidates`. If the same verified officer re-enters or is still on the same `owner_track` (or if a new track confirms as `pid` with `result["confirmed"] == True` and `result["person_id"] == pid`), allow a newly confirmed track of the **same** `person_id` to reclaim its own `pid`.

---

### Step 4: Allow Confirmed/Approaching BLE Owners to Claim Their Tag Before `gate["triggered"]` & Add Trigger Hysteresis

1. **Pass 1 BLE Claim without requiring `gate["triggered"]` ([`worker.py:954-970`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L954-L970)):**
   - Allow any track with `gate["kinematics_ready"]` (or any confirmed face match `result.get("confirmed")`) whose `claim_id in candidate_ids` to register a claim in `candidate_claims`.
   - Rank claims by:
     1. `3` if already verified, `2` if `result.get("confirmed")` (biometrically confirmed owner), `1` if unconfirmed candidate match with `gate["triggered"]`.
     2. `1` if `gate["triggered"]` else `0`.
     3. `float(result.get("similarity") or 0.0)`.
     4. `gate["face_size"]`.
   - This guarantees that a biometrically confirmed officer standing slightly further back (`kinematics_ready=True`, `scale_ready=False`) still claims their own BLE tag over an unconfirmed stranger standing closer to the camera.

2. **Prevent Single-Frame Trigger Dropouts from Resetting `self.first[track]` ([`worker.py:845-848`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L845-L848)):**
   - Track `self.last_triggered[track] = now` whenever `gate["triggered"]` is `True`.
   - Only pop `self.first.pop(track, None)` when `not gate["kinematics_ready"]` or when `now - self.last_triggered.get(track, 0.0) > 0.75` (a 750ms grace window against 1-frame bounding-box scale jitter).
   - Also clean up `self.last_triggered` inside [`_prune_stale_tracks`](file:///Users/utok/Documents/Programming/pnp/services/recognition/worker.py#L661-L678).

---

## 3. Planned Unit Tests ([`tests/test_worker.py`](file:///Users/utok/Documents/Programming/pnp/tests/test_worker.py))

Add focused unit tests to [`tests/test_worker.py`](file:///Users/utok/Documents/Programming/pnp/tests/test_worker.py) verifying each fix:

1. **`test_resolve_overlay_stage_never_shows_verifying_on_denied_outcomes`:**
   - Verify that when `outcome` is `"ambiguous"`, `"face_mismatch"`, `"timeout"`, `"missing_ble"`, `"unknown_tag"`, or `"service_unavailable"` and `candidates` is non-empty, `resolve_overlay_stage` returns stage `"denied"` with a descriptive denial label (never `"Verifying"` or `"<Name> · Verifying"`).
   - Verify that when `outcome is None`, `is_triggered=True`, `person` is a confirmed gallery profile (`id="p2"`), and `candidates` only contains a different person (`id="p1"`), `resolve_overlay_stage` returns `("evaluating", "Officer Two · Waiting for assigned BLE tag")` rather than `"Verifying"`.

2. **`test_multi_person_track_recovers_after_initial_missing_ble_or_ambiguous_state`:**
   - Simulate two people in frame where Person B initially times out with `"missing_ble"` or `"ambiguous"` while Person A is present.
   - Verify Person B's overlay shows the denial reason (not `"Verifying"`).
   - Next, provide Person B's BLE tag and valid face samples on subsequent frames on the **same track**.
   - Verify Person B is not permanently frozen by `self.logged` and successfully transitions to `outcome="verified"` with stage `"verified"`.

3. **`test_departed_verified_person_ble_tag_does_not_leak_to_remaining_person`:**
   - Simulate Officer A (with BLE tag) and Stranger/Officer B (without BLE tag) in frame together.
   - Officer A verifies on frames 1–3 (`stage="verified"`).
   - On frame 4, Officer A leaves the camera frame (only Person B remains in `faces`), while Officer A's BLE advertisement remains in `tag_list()` (simulating the 5s `BLE_RESULT_TTL`).
   - Verify Officer A's BLE tag remains claimed during the post-verification retention window, so Person B immediately displays `"Waiting for assigned BLE tag"` (or `"<Name> · Waiting for assigned BLE tag"`) instead of getting stuck at `"Verifying"` against Officer A's lingering tag.

---

## 4. Verification & Acceptance Steps

1. Run the worker and calibration test suites:
   - `./.venv/bin/python -m unittest tests/test_worker.py tests/test_video_calibration.py`
2. Verify all existing and new unit tests pass with zero regressions.
