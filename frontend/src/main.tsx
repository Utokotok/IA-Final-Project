import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/latin-700.css";
import "./style.css";
let csrf = "";
async function api(path: string, options: any = {}) {
  const headers: any = { "X-CSRFToken": csrf, ...options.headers };
  if (options.body && typeof options.body === "string")
    headers["Content-Type"] = "application/json";
  const r = await fetch("/api/" + path, {
    ...options,
    headers,
    credentials: "same-origin",
  });
  if (
    r.status === 403 &&
    !(path.startsWith("login") || path.startsWith("monitor"))
  ) {
    const s = await fetch("/api/session/").then((r) => r.json());
    if (!s.user) window.dispatchEvent(new Event("session-expired"));
  }
  const data = await r
    .json()
    .catch(() => ({ error: "Service unavailable. Please try again." }));
  if (!r.ok)
    throw new Error(
      data.error ||
        data.detail ||
        Object.entries(data)
          .map(([k, v]) => `${k}: ${v}`)
          .join(" · ") ||
        "Request failed",
    );
  return data;
}
const names: any = {
  verified: "Granted",
  face_mismatch: "Face mismatch",
  missing_ble: "No BLE",
  unknown_tag: "Unknown beacon",
  unidentified: "Unidentified",
  ambiguous: "Ambiguous",
  timeout: "Incomplete attempt",
  inactive: "Inactive",
  service_unavailable: "Service unavailable",
  legacy_sighting: "Legacy sighting",
  active: "Active",
  incomplete: "Incomplete",
  inactive_person: "Inactive",
};
const iconDimensions: Record<string, number[]> = {
  ec7e2: [17.0, 17.0],
  "70e56": [15.0, 15.0],
  "92146": [17.0, 17.0],
  "715b1": [16.0, 16.0],
  cd6eb: [16.0, 16.0],
  "59cfb": [14.0, 14.0],
  "5cdf4": [20.0, 20.0],
  "2971c": [14.0, 14.0],
  cec57: [16.0, 16.0],
  "382dc": [20.0, 20.0],
  "257a6": [16.0, 16.0],
  "91b93": [24.0, 24.0],
  "5883a": [7.0, 7.0],
  "43597": [15.0, 15.0],
  "0c017": [20.0, 20.0],
  "413bc": [19.0, 19.0],
  "8073f": [17.0, 17.0],
  f9c29: [25.0, 25.0],
  "5d3ae": [13.0, 13.0],
  "9a954": [17.0, 17.0],
  e6ed6: [16.0, 16.0],
  "1c150": [17.0, 17.0],
  "37f6e": [17.4219, 19.0],
  "5d145": [15.0, 15.0],
  d7841: [17.0, 17.0],
  "172cc": [20.0, 20.0],
  "38b09": [16.0, 16.0],
  "9e0c2": [16.0, 16.0],
  e766d: [16.0, 16.0],
  bca6f: [16.0, 16.0],
  b56f8: [17.0, 17.0],
  "13d89": [20.0, 20.0],
  "3b403": [17.0, 17.0],
  "73713": [20.0, 20.0],
  "25f6b": [20.0, 20.0],
  fce9b: [17.5625, 19.0],
  "67598": [16.0, 16.0],
  f9ba9: [15.0, 15.0],
  "07633": [16.0, 16.0],
  "3fd86": [15.0, 15.0],
  eb03d: [20.0, 20.0],
  "6c76f": [20.0, 20.0],
  "5d090": [20.0, 20.0],
  "51489": [16.0, 16.0],
  "2fcdd": [17.0, 17.0],
  b2ccc: [20.0, 20.0],
  "30e45": [20.0, 20.0],
  "47f8c": [17.0, 17.0],
  "3c031": [16.0, 16.0],
  "0563d": [20.0, 20.0],
  a1322: [17.0, 17.0],
  aa2bd: [14.0, 14.0],
};
function Icon({ file }: any) {
  const [width, height] = iconDimensions[file] || [18, 18];
  return (
    <img
      className="icon"
      style={{ width, height }}
      width={width}
      height={height}
      src={"/static/assets/" + file + ".svg"}
      alt=""
    />
  );
}

function Badge({ value }: any) {
  return (
    <span
      className={
        "badge " +
        (["verified", "active"].includes(value)
          ? "green"
          : ["missing_ble", "incomplete", "timeout"].includes(value)
            ? "amber"
            : value === "legacy_sighting"
              ? "neutral"
              : "red")
      }
    >
      {names[value] || value}
    </span>
  );
}
function Avatar({ name, url }: any) {
  return url ? (
    <img className="avatar" src={url} alt={name} />
  ) : (
    <span className="avatar">
      {name
        ?.split(" ")
        .slice(0, 2)
        .map((s: string) => s[0])
        .join("") || "?"}
    </span>
  );
}
function PersonPhoto({
  url,
  name,
  className = "avatar",
  missing = "No photo available",
}: any) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [url]);
  return url && !failed ? (
    <img
      className={className + " person-photo"}
      src={url}
      alt={name ? `Record photo linked to ${name}` : "Record photo"}
      onError={() => setFailed(true)}
    />
  ) : (
    <span
      className={className + " photo-unavailable"}
      role="img"
      aria-label={missing}
    >
      <Icon file="5d090" />
      {className !== "avatar" && <small>{missing}</small>}
    </span>
  );
}

function Empty({ children }: any) {
  return (
    <div className="empty">
      <span className="empty-mark">—</span>
      {children}
    </div>
  );
}
function Card({ title, subtitle, action, children, className = "" }: any) {
  return (
    <section className={"card " + className}>
      {title && (
        <div className="card-head">
          <div>
            <h3>{title}</h3>
            {subtitle && <p>{subtitle}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
function App() {
  const [user, setUser] = useState<any>(undefined),
    [page, setPage] = useState(location.pathname),
    [error, setError] = useState(""),
    [summary, setSummary] = useState<any>(null);
  const [selected, setSelected] = useState<any>(null),
    [passwordOpen, setPasswordOpen] = useState(false);
  const go = (p: string) => {
    history.pushState({}, "", p);
    setPage(p);
    setError("");
  };
  useEffect(() => {
    const pop = () => setPage(location.pathname);
    const expired = () => {
      setUser(null);
      setError("Your session expired. Sign in again.");
    };
    window.addEventListener("popstate", pop);
    window.addEventListener("session-expired", expired);
    api("session/")
      .then((s) => {
        csrf = s.csrf;
        setUser(s.user);
      })
      .catch((e) => setError(e.message));
    return () => {
      window.removeEventListener("popstate", pop);
      window.removeEventListener("session-expired", expired);
    };
  }, []);
  useEffect(() => {
    if (!user?.is_admin) return;
    let active = true;
    const refresh = () =>
      api("dashboard/")
        .then((s) => {
          if (active) setSummary(s);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [user]);
  if (user === undefined)
    return (
      <div className="loading">
        Loading local workspace…{error && <p role="alert">{error}</p>}
      </div>
    );
  if (!user)
    return (
      <Login
        onLogin={(u) => {
          setUser(u);
          go(u.is_admin ? "/" : "/monitor");
        }}
        message={error}
      />
    );
  const logout = async () => {
    try {
      await api("logout/", { method: "POST" });
      setUser(null);
      setSummary(null);
      setSelected(null);
    } catch (e) {
      setError(e.message);
    }
  };
  if (page === "/monitor" || !user.is_admin)
    return <Monitor user={user} go={go} logout={logout} />;
  const title = page.startsWith("/logs/")
    ? "Log Entry History"
    : page === "/logs"
      ? "Entry/Exit Logs"
      : page === "/personnel" || page === "/register"
        ? "Personnel"
        : "Dashboard";
  return (
    <div className="shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-symbol">
            <Icon file="f9c29" size={28} />
          </span>
          <div>
            <b>PNP Camp Crame</b>
            <small>Access Control</small>
          </div>
        </div>
        <nav>
          {[
            ["/", "0c017", "5cdf4", "Dashboard"],
            ["/logs", "0563d", "13d89", "Entry/Exit Logs"],
            ["/personnel", "5d090", "382dc", "Personnel"],
          ].map(([p, i, active, t]) => (
            <button
              key={p}
              className={
                page === p ||
                (p === "/logs" && page.startsWith("/logs/")) ||
                (p === "/personnel" && page === "/register")
                  ? "nav-active"
                  : ""
              }
              onClick={() => go(p)}
            >
              <Icon
                file={
                  page === p ||
                  (p === "/logs" && page.startsWith("/logs/")) ||
                  (p === "/personnel" && page === "/register")
                    ? active
                    : i
                }
              />
              {t}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <b>
            <i className={"dot " + (!summary?.online ? "offline" : "")} />{" "}
            {!summary?.online
              ? "Monitor offline"
              : summary?.station?.health?.ble_error
                ? "Bluetooth needs attention"
                : "System operational"}
          </b>
          <small>
            {!summary?.online
              ? "Start the local monitoring service"
              : summary?.station?.health?.ble_error
                ? "Check Bluetooth scanning"
                : "BLE & facial services ready"}
          </small>
        </div>
      </aside>
      <div className="content">
        <header>
          <span>{title}</span>
          <div className="header-user">
            <span className="station-label">
              <i className={"dot " + (!summary?.online ? "offline" : "")} />
              Gate 1 · {summary?.online ? "Online" : "Offline"}
            </span>
            <Avatar
              name={user.username === "admin" ? "Administrator" : user.username}
            />
            <div>
              <b>
                {user.username === "admin" ? "Administrator" : user.username}
              </b>
              <small>System Admin</small>
            </div>
            <details className="account-menu">
              <summary aria-label="Account menu">
                <Icon file="07633" />
              </summary>
              <div>
                <button onClick={() => setPasswordOpen(true)}>
                  Change password
                </button>
                <button onClick={() => go("/monitor")}>
                  Open local monitor
                </button>
                <button onClick={logout}>Log out</button>
              </div>
            </details>
          </div>
        </header>
        <main>
          {error && (
            <div className="notice error" role="alert">
              {error}
              <button onClick={() => setError("")}>Dismiss</button>
            </div>
          )}
          {page === "/personnel" ? (
            <People go={go} onSelect={setSelected} fail={setError} />
          ) : page === "/register" ? (
            <Registration
              key={selected?.id || "new"}
              initial={selected}
              done={() => {
                setSelected(null);
                go("/personnel");
              }}
              fail={setError}
            />
          ) : page.startsWith("/logs/") ? (
            <LogHistory id={page.slice(6)} go={go} />
          ) : page === "/logs" ? (
            <Logs fail={setError} go={go} />
          ) : (
            <Dashboard summary={summary} go={go} fail={setError} />
          )}
        </main>
      </div>
      {passwordOpen && <Password close={() => setPasswordOpen(false)} />}
      {selected && page === "/personnel" && (
        <EditPerson
          person={selected}
          close={() => setSelected(null)}
          fail={setError}
        />
      )}
    </div>
  );
}
function Login({ onLogin, message }: any) {
  const [username, setName] = useState(""),
    [password, setPassword] = useState(""),
    [show, setShow] = useState(false),
    [error, setError] = useState(message || ""),
    [busy, setBusy] = useState(false);
  async function submit(e: any) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const s = await api("login/", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      csrf = s.csrf;
      onLogin(s.user);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <div className="login-watermark">PNP</div>
      <form className="login-card" onSubmit={submit}>
        <div className="login-shield">
          <Icon file="f9c29" size={32} />
        </div>
        <p className="eyebrow">PNP CAMP CRAME ACCESS CONTROL</p>
        <h1>Administrator Login</h1>
        <p className="muted">Sign in to manage Gate 1 access verification.</p>
        <label>
          Username
          <input
            value={username}
            onChange={(e) => setName(e.target.value)}
            placeholder="Enter administrator username"
            autoComplete="username"
            required
          />
        </label>
        <label>
          Password
          <div className="password-field">
            <input
              type={show ? "text" : "password"}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              autoComplete="current-password"
              required
            />
            <button
              type="button"
              aria-label={show ? "Hide password" : "Show password"}
              onClick={() => setShow(!show)}
            >
              {show ? "Hide" : "Show"}
            </button>
          </div>
        </label>
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        <button className="primary full" disabled={busy}>
          {busy ? "Signing in…" : "Login"}
        </button>
        <p className="login-note">
          Authorized PNP personnel only. Activity is monitored.
        </p>
      </form>
    </div>
  );
}
function Dashboard({ summary: s, go, fail }: any) {
  if (!s) return <Empty>Loading activity…</Empty>;
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>
            {new Date().getHours() < 12
              ? "Good morning"
              : new Date().getHours() < 18
                ? "Good afternoon"
                : "Good evening"}
            , Administrator
          </h1>
          <p>Here is today’s access activity at Gate 1.</p>
        </div>
        <span className="badge green">● Live updates active</span>
      </div>
      <div className="stats">
        {[
          ["Entries Today", s.entries, "Entry verification attempts", "b2ccc"],
          ["Exits Today", s.exits, "Exit verification attempts", "73713"],
          [
            "People Currently Inside",
            "—",
            "Passage detection not configured",
            "30e45",
          ],
          [
            "Alerts Needing Review",
            s.alerts,
            "Denied or incomplete attempts",
            "eb03d",
          ],
        ].map(([t, n, sub, i]) => (
          <div className="stat card" key={t}>
            <span className="stat-icon">
              <Icon file={i} size={22} />
            </span>
            <div>
              <h4>{t}</h4>
              <strong>{n}</strong>
              <small>{sub}</small>
            </div>
          </div>
        ))}
      </div>
      <div className="dashboard-grid">
        <Card
          title="Latest Verification"
          subtitle="Most recent Gate 1 activity"
          action={
            <span className="badge neutral">
              {s.online ? "Connected" : "Offline"}
            </span>
          }
        >
          {s.latest.length ? (
            <Latest event={s.latest[0]} />
          ) : (
            <Empty>
              No verifications yet. Start the monitor to record activity.
            </Empty>
          )}
        </Card>
        <Card
          title="Alerts Requiring Review"
          subtitle="Denied or incomplete attempts"
          action={<Badge value={String(s.alerts)} />}
        >
          <Alerts fail={fail} />
        </Card>
      </div>
      <div className="dashboard-grid dashboard-lower">
        <Card
          title="Recent Activity"
          subtitle="Latest verification records"
          action={<button onClick={() => go("/logs")}>Show Records</button>}
        >
          <div className="compact-list">
            {s.recent.length ? (
              s.recent.map((e) => (
                <div
                  className="activity-row clickable"
                  key={e.id}
                  role="link"
                  tabIndex={0}
                  onClick={() => go(`/logs/${e.id}`)}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter") go(`/logs/${e.id}`);
                  }}
                >
                  <PersonPhoto name={e.name} url={e.captured_image_url} />
                  <div className="grow">
                    <b>{e.name}</b>
                    <small>{e.personnel_id || "Unknown identity"}</small>
                  </div>
                  <span>{e.direction || "—"}</span>
                  <Badge value={e.outcome} />
                </div>
              ))
            ) : (
              <Empty>No activity recorded.</Empty>
            )}
          </div>
        </Card>
        <Card
          title="Manage Personnel"
          subtitle="Enrollment and records"
          action={
            <button className="primary" onClick={() => go("/register")}>
              + Add
            </button>
          }
        >
          <div className="compact-list">
            {s.people.map((p) => (
              <div className="activity-row" key={p.id}>
                <PersonPhoto
                  name={p.name}
                  url={p.thumbnail}
                  missing="Registration photo unavailable"
                />
                <div className="grow">
                  <b>{p.name}</b>
                  <small>{p.personnel_id}</small>
                </div>
                <Badge value={p.status} />
              </div>
            ))}
            <button className="text-button" onClick={() => go("/personnel")}>
              View personnel directory →
            </button>
          </div>
        </Card>
      </div>
      <p className="footnote">
        Entry and exit counts represent verification attempts in the selected
        direction. Physical passage and occupancy are not measured.
      </p>
    </>
  );
}
function Latest({ event: e }: any) {
  return (
    <div className="latest">
      <div className="portrait">
        <PersonPhoto name={e.name} url={e.captured_image_url} />
        <small>Gate verification</small>
      </div>
      <div className="latest-info">
        <span className="eyebrow">
          {e.kind === "legacy_sighting"
            ? "LEGACY FACE SIGHTING"
            : "VERIFICATION RESULT"}
        </span>
        <h2>
          {e.rank} {e.name}
        </h2>
        <p>
          {e.personnel_id || "Unknown identity"} · {e.unit || "—"}
        </p>
        <Badge value={e.outcome} />
        <div className="evidence">
          <small>Assigned BLE beacon</small>
          <b>{e.ble_tag || "Not available"}</b>
        </div>
        <p>
          Face match score{" "}
          <strong>
            {typeof e.similarity === "number" ? e.similarity.toFixed(3) : "—"}
          </strong>
        </p>
        <small>
          {new Date(e.timestamp).toLocaleString("en-PH", {
            timeZone: "Asia/Manila",
          })}{" "}
          · {e.direction || "Unknown direction"}
        </small>
      </div>
    </div>
  );
}
function Alerts({ fail }: any) {
  const [rows, setRows] = useState<any[]>([]),
    [selected, setSelected] = useState<any>(null),
    [note, setNote] = useState("");
  const refresh = () =>
    api("logs/?alerts=1")
      .then((s) => setRows(s.results))
      .catch((e) => fail(e.message));
  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, []);
  async function submit(e: any) {
    e.preventDefault();
    try {
      await api(`logs/${selected.id}/review/`, {
        method: "POST",
        body: JSON.stringify({ note }),
      });
      setSelected(null);
      setNote("");
      refresh();
    } catch (e) {
      fail(e.message);
    }
  }
  return (
    <div className="compact-list">
      {rows.length ? (
        rows.slice(0, 5).map((e) => (
          <div className="activity-row" key={e.id}>
            <PersonPhoto name={e.name} url={e.captured_image_url} />
            <div className="grow">
              <b>{e.name}</b>
              <small>
                {new Date(e.timestamp).toLocaleTimeString("en-PH", {
                  timeZone: "Asia/Manila",
                })}
              </small>
            </div>
            <Badge value={e.outcome} />
            <button className="text-button" onClick={() => setSelected(e)}>
              Review
            </button>
          </div>
        ))
      ) : (
        <Empty>No alerts requiring review.</Empty>
      )}
      {selected && (
        <Modal title="Review verification" close={() => setSelected(null)}>
          <Latest event={selected} />
          <form onSubmit={submit}>
            <label>
              Resolution and review notes
              <textarea
                required
                maxLength={2000}
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />
            </label>
            <button className="primary">Save review</button>
          </form>
        </Modal>
      )}
    </div>
  );
}
function People({ go, onSelect, fail }: any) {
  const [data, setData] = useState<any>(null),
    [q, setQ] = useState("");
  useEffect(() => {
    let active = true;
    const t = setTimeout(
      () =>
        api("people/?q=" + encodeURIComponent(q))
          .then((d) => {
            if (active) setData(d);
          })
          .catch((e) => fail(e.message)),
      150,
    );
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [q]);
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Personnel</h1>
          <p>
            Manage personnel records, BLE assignments, and facial enrollment.
          </p>
        </div>
        <button
          className="primary"
          onClick={() => {
            onSelect(null);
            go("/register");
          }}
        >
          + Register Personnel
        </button>
      </div>
      <div className="stats three">
        {[
          ["Total personnel", data?.total, "Registered at Gate 1"],
          ["Active enrollments", data?.active, "Ready for verification"],
          [
            "Incomplete records",
            data?.incomplete,
            "Require administrator action",
          ],
        ].map(([title, n, subtitle], i) => (
          <div
            className={"stat card personnel-stat personnel-stat-" + i}
            key={title}
          >
            <div>
              <h4>{title}</h4>
              <strong>{n ?? "—"}</strong>
              <small>{subtitle}</small>
            </div>
          </div>
        ))}
      </div>
      {data?.drafts.length > 0 && (
        <div className="drafts">
          {data.drafts.map((e) => (
            <button
              key={e.id}
              onClick={() => {
                onSelect(e);
                go("/register");
              }}
            >
              Resume draft: {e.person.name}
            </button>
          ))}
        </div>
      )}
      <Card
        className="directory-card"
        title="Personnel Directory"
        subtitle="View and maintain all enrolled personnel."
        action={
          <input
            className="search"
            placeholder="Search ID, name, beacon, or unit"
            aria-label="Search personnel"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
        }
      >
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {[
                  "Personnel",
                  "Personnel ID",
                  "Rank / Position",
                  "Unit",
                  "BLE Beacon ID",
                  "Date Enrolled",
                  "Enrollment Status",
                  "Actions",
                ].map((t) => (
                  <th key={t}>{t}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.people.map((p) => (
                <tr key={p.id}>
                  <td>
                    <div className="person-cell">
                      <PersonPhoto
                        name={p.name}
                        url={p.thumbnail}
                        missing="Registration photo unavailable"
                      />
                      <b>{p.name}</b>
                    </div>
                  </td>
                  <td className="mono">{p.personnel_id}</td>
                  <td>{p.rank || "—"}</td>
                  <td>{p.unit || "—"}</td>
                  <td className="mono">{p.ble_tag || "—"}</td>
                  <td>{new Date(p.created_at).toLocaleDateString("en-PH")}</td>
                  <td>
                    <Badge value={p.status} />
                  </td>
                  <td>
                    <button className="text-button" onClick={() => onSelect(p)}>
                      Edit
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data?.people.length && (
            <Empty>No personnel found. Register a person to get started.</Empty>
          )}
        </div>
        <div className="table-footer">
          Showing {data?.people.length || 0} personnel{" "}
          <span>Gate 1 enrollment directory</span>
        </div>
      </Card>
    </>
  );
}
function Modal({ title, close, children }: any) {
  return (
    <div
      className="modal-backdrop"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <section
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
      >
        <div className="card-head">
          <h2>{title}</h2>
          <button onClick={close} aria-label="Close dialog">
            ×
          </button>
        </div>
        <div className="modal-content">{children}</div>
      </section>
    </div>
  );
}
function EditPerson({ person: p, close, fail }: any) {
  const [form, setForm] = useState(p),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function save(e: any) {
    e.preventDefault();
    setBusy(true);
    try {
      await api(`people/${p.id}/`, {
        method: "PATCH",
        body: JSON.stringify(form),
      });
      close();
      location.reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (
      !confirm(
        "Deactivate this person? Their historical logs will be preserved.",
      )
    )
      return;
    try {
      await api(`people/${p.id}/`, { method: "DELETE" });
      close();
      location.reload();
    } catch (e) {
      setError(e.message);
    }
  }
  async function activate() {
    setBusy(true);
    setError("");
    try {
      await api(`people/${p.id}/`, {
        method: "PATCH",
        body: JSON.stringify(form),
      });
      await api(`people/${p.id}/`, { method: "POST", body: "{}" });
      close();
      location.reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal title="Edit personnel" close={close}>
      <form onSubmit={save}>
        {["personnel_id", "name", "rank", "unit", "ble_tag"].map((k) => (
          <label key={k}>
            {
              {
                personnel_id: "Personnel ID",
                name: "Full Name",
                rank: "Rank / Position",
                unit: "Unit / Department",
                ble_tag: "BLE identifier",
              }[k]
            }
            <input
              required
              value={form[k] || ""}
              maxLength={k === "name" ? 120 : 60}
              onChange={(e) => setForm({ ...form, [k]: e.target.value })}
            />
          </label>
        ))}
        {error && <div className="notice error">{error}</div>}
        <div className="button-row">
          <button type="button" className="danger" onClick={remove}>
            Deactivate
          </button>
          {p.has_profile && p.status !== "active" && (
            <button type="button" onClick={activate} disabled={busy}>
              Activate reviewed profile
            </button>
          )}
          <button className="primary" disabled={busy}>
            Save changes
          </button>
        </div>
      </form>
    </Modal>
  );
}
function LogHistory({ id, go }: any) {
  const [entry, setEntry] = useState<any>(null),
    [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setEntry(null);
    setError("");
    api(`logs/${id}/`, { signal: controller.signal })
      .then(setEntry)
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [id]);
  const formatDate = (value: any, withTime = false) =>
    value
      ? new Date(value).toLocaleString("en-GB", {
          timeZone: "Asia/Manila",
          day: "2-digit",
          month: "short",
          year: "numeric",
          ...(withTime
            ? {
                hour: "2-digit",
                minute: "2-digit",
                second: "2-digit",
                hour12: false,
              }
            : {}),
        })
      : "Not recorded";
  const reasons: any = {
    verified: "Assigned beacon and facial recognition checks passed.",
    face_mismatch:
      "Beacon valid but the face did not match the registered owner.",
    missing_ble: "No assigned beacon was verified for this attempt.",
    unknown_tag:
      "The detected beacon could not be linked to enrolled personnel.",
    ambiguous: "Multiple faces or beacons prevented a clear verification.",
    service_unavailable: "The recognition data was unavailable or out of date.",
    timeout: "The verification checks did not finish.",
    inactive: "The personnel record was inactive.",
    unidentified: "The person could not be identified.",
    legacy_sighting:
      "This is an imported face sighting, without a recorded access decision.",
  };
  const back = (
    <button className="history-back" onClick={() => go("/logs")}>
      <Icon file="b56f8" />
      Back to Entry/Exit Logs
    </button>
  );
  if (!entry)
    return (
      <section className="history-page">
        {back}
        {error ? (
          <div className="notice error" role="alert">
            {error}
          </div>
        ) : (
          <Empty>Loading log entry…</Empty>
        )}
      </section>
    );
  const e = entry,
    granted = e.outcome === "verified",
    legacy = e.kind === "legacy_sighting";
  const score = typeof e.similarity === "number" ? e.similarity : null;
  const threshold =
    typeof e.evidence?.settings?.threshold === "number"
      ? e.evidence.settings.threshold
      : null;
  const below = score != null && threshold != null && score < threshold;
  const pct = (v: any) =>
    v == null ? "Not recorded" : `${(v * 100).toFixed(1)}%`;
  const status = legacy ? "Not recorded" : granted ? "Granted" : "Denied";
  const tags = Array.isArray(e.evidence?.ble_tags) ? e.evidence.ble_tags : [];
  const tag = tags.find(
    (t) =>
      e.ble_tag &&
      String(t.tag).toLowerCase().includes(e.ble_tag.toLowerCase()),
  );
  const bleValid =
    typeof e.evidence?.ble_verified === "boolean"
      ? e.evidence.ble_verified
      : null;
  const rssi = typeof tag?.rssi === "number" ? tag.rssi : null;
  const faceResult = granted
    ? "Face match confirmed"
    : e.outcome === "face_mismatch"
      ? "Face does not match"
      : score == null
        ? "Not compared"
        : "Not confirmed";
  return (
    <section className="history-page">
      {back}
      <div className="history-reference">
        <p className="eyebrow">LOG REFERENCE</p>
        <div className="history-title">
          <h1>{e.log_reference}</h1>
          <Badge value={e.outcome} />
        </div>
        <p className="history-subtitle">
          {formatDate(e.timestamp, true)} ·{" "}
          <span className="direction-label">
            {e.direction && (
              <Icon file={e.direction === "entry" ? "b2ccc" : "73713"} />
            )}
            <b>
              {e.direction
                ? e.direction[0].toUpperCase() + e.direction.slice(1)
                : "Direction not recorded"}
            </b>
          </span>
        </p>
      </div>
      <div className="history-top">
        <Card
          className="image-comparison"
          title="Image Comparison"
          subtitle="Gate capture compared with the enrolled personnel photo"
        >
          <div className="comparison-grid">
            <figure>
              <figcaption>CAPTURED IMAGE</figcaption>
              <div className="history-photo">
                <PersonPhoto
                  className="comparison-photo"
                  url={e.captured_image_url}
                  name={e.name}
                  missing="No captured image available"
                />
                {e.captured_image_url && (
                  <span className="photo-caption">
                    Gate 1 ·{" "}
                    {new Date(e.captured_at || e.timestamp).toLocaleTimeString(
                      "en-GB",
                      { timeZone: "Asia/Manila", hour12: false },
                    )}
                  </span>
                )}
              </div>
            </figure>
            <div
              className={
                "comparison-score " + (granted ? "positive" : "negative")
              }
            >
              <strong>{pct(score)}</strong>
              <b>Match similarity</b>
              <span className="score-status">
                {score == null
                  ? "Not compared"
                  : below
                    ? "Below threshold"
                    : granted
                      ? "Confirmed"
                      : "Not confirmed"}
              </span>
            </div>
            <figure>
              <figcaption>REGISTERED PERSONNEL PHOTO</figcaption>
              <div className="history-photo">
                <PersonPhoto
                  className="comparison-photo"
                  url={e.registered_image_url}
                  name={e.name}
                  missing="No registration photo available"
                />
                {e.registered_image_url && (
                  <span className="photo-caption">
                    {e.registered_at
                      ? `Enrolled ${formatDate(e.registered_at)}`
                      : "Registration photo"}
                  </span>
                )}
              </div>
            </figure>
          </div>
        </Card>
        <Card
          className="verification-decision"
          title="Verification Decision"
          subtitle="Final access-control outcome"
        >
          <div
            className={"decision-result " + (granted ? "positive" : "negative")}
          >
            <span className="decision-mark">
              {granted ? "✓" : legacy ? "—" : "!"}
            </span>
            <div>
              <Badge value={e.outcome} />
              <h2>
                {legacy
                  ? "Legacy sighting"
                  : granted
                    ? "Access granted"
                    : "Access denied"}
              </h2>
              <p>
                {reasons[e.outcome] ||
                  "The required verification checks did not pass."}
              </p>
            </div>
          </div>
          <dl className="history-facts">
            <div>
              <dt>Verification result</dt>
              <dd>{status}</dd>
            </div>
            <div>
              <dt>Required threshold</dt>
              <dd>{pct(threshold)}</dd>
            </div>
            <div>
              <dt>Gate</dt>
              <dd>Gate 1</dd>
            </div>
          </dl>
        </Card>
      </div>
      <div className="history-bottom">
        <Card className="history-info">
          <div className="history-info-heading">
            <span className="history-info-icon">
              <Icon file="5d090" />
            </span>
            <div>
              <h3>Claimed Identity</h3>
              <p>Identity broadcast by the BLE beacon</p>
            </div>
          </div>
          <dl className="history-facts">
            <div>
              <dt>Full name</dt>
              <dd>{e.name || "Unidentified"}</dd>
            </div>
            <div>
              <dt>Personnel ID</dt>
              <dd>{e.personnel_id || "Not recorded"}</dd>
            </div>
            <div>
              <dt>Rank / Position</dt>
              <dd>{e.rank || "Not recorded"}</dd>
            </div>
            <div>
              <dt>Unit / Department</dt>
              <dd>{e.unit || "Not recorded"}</dd>
            </div>
          </dl>
        </Card>
        <Card className="history-info">
          <div className="history-info-heading">
            <span className="history-info-icon">
              <Icon file="5d145" />
            </span>
            <div>
              <h3>BLE Detection</h3>
              <p>First-factor proximity verification</p>
            </div>
          </div>
          <div className="ble-evidence">
            <div className="ble-identifier">
              <div>
                <small>DETECTED IDENTIFIER</small>
                <b>{tag?.tag || e.ble_tag || "Not recorded"}</b>
              </div>
              <span className={"badge " + (bleValid ? "green" : "neutral")}>
                {bleValid === null
                  ? "Not recorded"
                  : bleValid
                    ? "✓ Valid beacon"
                    : "Not verified"}
              </span>
            </div>
            <div className="signal-evidence">
              <span>Signal strength</span>
              {rssi != null && (
                <div className="signal-bars" aria-hidden="true">
                  {[0, 1, 2, 3, 4].map((n) => (
                    <i
                      key={n}
                      style={{
                        height: 5 + n * 3,
                        background:
                          n < (rssi >= -60 ? 5 : rssi >= -75 ? 4 : 2)
                            ? "#2f8c5a"
                            : "#d8e0e9",
                      }}
                    />
                  ))}
                </div>
              )}
              <b>{rssi == null ? "Not recorded" : `${rssi} dBm`}</b>
            </div>
            <p className="history-note">
              {bleValid === null
                ? "Beacon verification status was not recorded in this older log."
                : bleValid
                  ? "Assigned beacon was verified within the configured signal range."
                  : "No valid assigned beacon was recorded for this attempt."}
            </p>
          </div>
        </Card>
        <Card className="history-info">
          <div className="history-info-heading">
            <span className="history-info-icon">
              <Icon file="5d3ae" />
            </span>
            <div>
              <h3>Facial Recognition</h3>
              <p>Final biometric confirmation</p>
            </div>
          </div>
          <div className="face-evidence">
            <div className="signal-evidence">
              <span>Match similarity</span>
              <strong className={granted ? "positive" : "negative"}>
                {pct(score)}
              </strong>
            </div>
            <div className="history-score-bar">
              <span
                style={{
                  width: `${Math.max(0, Math.min(100, (score || 0) * 100))}%`,
                  background: granted ? "#2f9d64" : "#c9565a",
                }}
              />
              {threshold != null && (
                <i style={{ left: `${threshold * 100}%` }} />
              )}
            </div>
            <small className="threshold-caption">
              {threshold == null
                ? "Threshold not recorded"
                : `${pct(threshold)} threshold`}
            </small>
            <div className="signal-evidence">
              <span>Result</span>
              <b className={granted ? "positive" : "negative"}>{faceResult}</b>
            </div>
            {!granted && (
              <p className="history-note warning">
                {below
                  ? `Similarity is ${((threshold - score) * 100).toFixed(1)} percentage points below the required threshold.`
                  : score == null
                    ? "No face-match score was recorded."
                    : "A score alone does not grant access; all verification checks must pass."}
              </p>
            )}
          </div>
        </Card>
      </div>
      {e.review_note && (
        <Card className="history-review" title="Review notes">
          <p>{e.review_note}</p>
        </Card>
      )}
    </section>
  );
}

function Logs({ fail, go }: any) {
  const [filter, setFilter] = useState({
      q: "",
      outcome: "",
      direction: "",
      rank: "",
      start: "",
      end: "",
      sort: "-timestamp",
      page: 1,
    }),
    [data, setData] = useState<any>(null);
  const query = new URLSearchParams(
    Object.entries(filter).map(([k, v]) => [k, String(v)]),
  ).toString();
  useEffect(() => {
    let active = true;
    const t = setTimeout(
      () =>
        api("logs/?" + query)
          .then((d) => {
            if (active) setData(d);
          })
          .catch((e) => fail(e.message)),
      180,
    );
    return () => {
      active = false;
      clearTimeout(t);
    };
  }, [query]);
  const change = (k: string, v: any) =>
    setFilter({ ...filter, [k]: v, page: 1 });
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Entry/Exit Logs</h1>
          <p>Search, filter, and review Gate 1 verification records.</p>
        </div>
        <a className="button" href={"/api/logs/?" + query + "&export=1"}>
          <Icon file="2fcdd" />
          Export
        </a>
      </div>
      <Card className="filters">
        <div className="filter-row">
          <input
            className="grow"
            placeholder="Search ID, name, or beacon ID"
            aria-label="Search logs"
            value={filter.q}
            onChange={(e) => change("q", e.target.value)}
          />
          <input
            placeholder="Rank / Position"
            aria-label="Rank filter"
            value={filter.rank}
            onChange={(e) => change("rank", e.target.value)}
          />
          <button
            className="text-button"
            onClick={() =>
              setFilter({
                q: "",
                outcome: "",
                direction: "",
                rank: "",
                start: "",
                end: "",
                sort: "-timestamp",
                page: 1,
              })
            }
          >
            Clear filters
          </button>
        </div>
        <div className="filter-row">
          <label>
            Status
            <select
              value={filter.outcome}
              onChange={(e) => change("outcome", e.target.value)}
            >
              <option value="">All statuses</option>
              {Object.entries(names)
                .filter(
                  ([k]) =>
                    !["active", "incomplete", "inactive_person"].includes(k),
                )
                .map(([k, v]) => (
                  <option key={k} value={k}>
                    {v as string}
                  </option>
                ))}
            </select>
          </label>
          <label>
            Direction
            <select
              value={filter.direction}
              onChange={(e) => change("direction", e.target.value)}
            >
              <option value="">Entry & Exit</option>
              <option value="entry">Entry</option>
              <option value="exit">Exit</option>
            </select>
          </label>
          <label>
            From
            <input
              type="date"
              value={filter.start}
              onChange={(e) => change("start", e.target.value)}
            />
          </label>
          <label>
            To
            <input
              type="date"
              value={filter.end}
              onChange={(e) => change("end", e.target.value)}
            />
          </label>
          <span className="record-count">
            <strong>{data?.total || 0}</strong>
            <small>records found</small>
          </span>
        </div>
      </Card>
      <Card className="logs-table">
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Profile photo</th>
                {[
                  ["personnel_id", "Personnel ID"],
                  ["name", "Name"],
                  ["rank", "Position"],
                  ["ble_tag", "BLE beacon"],
                  ["timestamp", "Date and Time"],
                  ["direction", "Entry/Exit"],
                  ["similarity", "Match score"],
                  ["outcome", "Result"],
                ].map(([k, t]) => (
                  <th key={k}>
                    <button
                      onClick={() =>
                        change("sort", filter.sort === k ? "-" + k : k)
                      }
                      disabled={["rank", "ble_tag"].includes(k)}
                    >
                      {t} ↕
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data?.results.map((e) => (
                <tr
                  key={e.id}
                  onClick={() => go(`/logs/${e.id}`)}
                  className="clickable"
                  tabIndex={0}
                  onKeyDown={(ev) => {
                    if (ev.key === "Enter" || ev.key === " ") {
                      ev.preventDefault();
                      go(`/logs/${e.id}`);
                    }
                  }}
                >
                  <td>
                    <PersonPhoto name={e.name} url={e.captured_image_url} />
                  </td>
                  <td className="mono">{e.personnel_id || "—"}</td>
                  <td>
                    <b>{e.name}</b>
                  </td>
                  <td>{e.rank || "—"}</td>
                  <td className="mono">{e.ble_tag || "—"}</td>
                  <td>
                    {new Date(e.timestamp).toLocaleString("en-PH", {
                      timeZone: "Asia/Manila",
                    })}
                  </td>
                  <td>
                    <span className={"direction-label " + e.direction}>
                      {e.direction && (
                        <Icon
                          file={e.direction === "entry" ? "b2ccc" : "73713"}
                          size={15}
                        />
                      )}
                      <span>
                        {e.direction
                          ? e.direction[0].toUpperCase() + e.direction.slice(1)
                          : "—"}
                      </span>
                    </span>
                  </td>
                  <td>
                    <div className="match-score">
                      {e.similarity?.toFixed(3) || "—"}
                      {e.similarity != null && (
                        <span>
                          <i
                            style={{
                              width:
                                Math.max(0, Math.min(100, e.similarity * 100)) +
                                "%",
                              background:
                                e.outcome === "verified"
                                  ? "#2f9d64"
                                  : "#c9565a",
                            }}
                          />
                        </span>
                      )}
                    </div>
                  </td>
                  <td>
                    <Badge value={e.outcome} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data?.results.length && (
            <Empty>No records match these filters.</Empty>
          )}
        </div>
        <div className="table-footer">
          <span>Page {filter.page} · Dates shown in Asia/Manila</span>
          <div className="button-row">
            <button
              disabled={filter.page <= 1}
              onClick={() => setFilter({ ...filter, page: filter.page - 1 })}
            >
              Previous
            </button>
            <button
              disabled={filter.page * 25 >= (data?.total || 0)}
              onClick={() => setFilter({ ...filter, page: filter.page + 1 })}
            >
              Next
            </button>
          </div>
        </div>
      </Card>
    </>
  );
}
const poses = ["front", "left", "right", "up", "down"];
function Registration({ initial, done, fail }: any) {
  const [enrollment, setEnrollment] = useState<any>(initial),
    [step, setStep] = useState(initial ? 2 : 0),
    [form, setForm] = useState<any>(
      initial?.person || {
        personnel_id: "",
        name: "",
        rank: "",
        unit: "",
        ble_tag: "",
      },
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [saved, setSaved] = useState(false),
    [tags, setTags] = useState<any[]>([]),
    [scanning, setScanning] = useState(false),
    [scanFinished, setScanFinished] = useState(false),
    [guidance, setGuidance] = useState(""),
    [customUnit, setCustomUnit] = useState(false);
  const [pose, setPose] = useState("front"),
    [capturing, setCapturing] = useState(false),
    [stream, setStream] = useState<MediaStream | null>(null);
  const video = useRef<HTMLVideoElement>(null),
    live = useRef(false),
    mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      live.current = false;
    };
  }, []);
  useEffect(() => {
    if (video.current && stream) video.current.srcObject = stream;
    return () => {};
  }, [stream, step]);
  useEffect(
    () => () => {
      stream?.getTracks().forEach((t) => t.stop());
    },
    [stream],
  );
  useEffect(() => {
    if (!enrollment || step === 0) return;
    setSaved(false);
    const t = setTimeout(
      () =>
        api(`people/${enrollment.person.id}/`, {
          method: "PATCH",
          body: JSON.stringify(form),
        })
          .then(() => setSaved(true))
          .catch((e) => setError(e.message)),
      700,
    );
    return () => clearTimeout(t);
  }, [form]);
  async function next() {
    setError("");
    setBusy(true);
    try {
      if (step === 0) {
        if (
          !form.personnel_id.trim() ||
          !form.name.trim() ||
          !form.rank.trim() ||
          !form.unit.trim()
        )
          throw new Error("Complete all personnel fields.");
        if (!enrollment) {
          const e = await api("people/", {
            method: "POST",
            body: JSON.stringify({ ...form, ble_tag: form.ble_tag || null }),
          });
          setEnrollment(e);
          setSaved(true);
        } else
          await api(`people/${enrollment.person.id}/`, {
            method: "PATCH",
            body: JSON.stringify(form),
          });
      }
      if (step === 1) {
        if (!form.ble_tag.trim())
          throw new Error("Assign a BLE identifier before continuing.");
        await api(`people/${enrollment.person.id}/`, {
          method: "PATCH",
          body: JSON.stringify(form),
        });
      }
      if (step === 2) {
        if (poses.some((p) => (enrollment?.counts[p] || 0) !== 8))
          throw new Error(
            "Capture eight accepted samples at each of the five angles.",
          );
        stopCamera();
      }
      if (step === 3) {
        await api(`enrollments/${enrollment.id}/`, {
          method: "POST",
          body: "{}",
        });
        stream?.getTracks().forEach((t) => t.stop());
        setStep(4);
        return;
      }
      setStep(step + 1);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }
  function stopCamera() {
    live.current = false;
    setCapturing(false);
    stream?.getTracks().forEach((t) => t.stop());
    setStream(null);
  }
  async function startCamera() {
    try {
      if (
        ["localhost", "127.0.0.1", "[::1]"].includes(window.location.hostname)
      ) {
        const station = await api("monitor/");
        if (station.running || station.requested) {
          await api("monitor/", {
            method: "POST",
            body: JSON.stringify({ action: "stop" }),
          });
          for (let i = 0; i < 30; i++) {
            const state = await api("monitor/");
            if (!state.running) break;
            await new Promise((resolve) => setTimeout(resolve, 100));
          }
          setGuidance(
            "Monitor camera paused for registration. Start it again on the monitoring dashboard when finished.",
          );
        }
      }
      if (!navigator.mediaDevices?.getUserMedia)
        throw new Error(
          "Camera access requires trusted HTTPS. Trust the local certificate on this device first.",
        );
      const s = await navigator.mediaDevices.getUserMedia({
        video: {
          width: { ideal: 1280 },
          height: { ideal: 720 },
          facingMode: "user",
        },
        audio: false,
      });
      setStream(s);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }
  async function take(targetPose = pose) {
    if (!video.current || !stream || !enrollment) return;
    setBusy(true);
    setError("");
    try {
      const canvas = document.createElement("canvas");
      const v = video.current;
      const width = v.videoWidth,
        height = v.videoHeight;
      if (!width || !height)
        throw new Error("Wait for the camera to become ready.");
      const scale = Math.min(1, 960 / Math.max(width, height));
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      canvas.getContext("2d")!.drawImage(v, 0, 0, canvas.width, canvas.height);
      const blob = await new Promise<Blob>((resolve) =>
        canvas.toBlob(resolve, "image/jpeg", 0.8),
      );
      const j = await api(
        `enrollments/${enrollment.id}/capture/?pose=${targetPose}`,
        {
          method: "POST",
          body: blob,
          headers: { "Content-Type": "image/jpeg" },
        },
      );
      let result: any;
      for (let i = 0; i < 450; i++) {
        await new Promise((r) => setTimeout(r, 100));
        if (!mounted.current) return;
        result = await api(`jobs/${j.job_id}/`);
        if (!["pending", "processing"].includes(result.status)) break;
      }
      if (!result || ["pending", "processing"].includes(result.status))
        throw new Error(
          "Still processing. Keep this draft and resume after checking the local service.",
        );
      setEnrollment(result.enrollment);
      if (result.status === "cancelled")
        throw new Error(
          "Capture was cancelled. Resume registration to try again.",
        );
      // Pose/quality feedback is normal: keep the camera and capture loop alive.
      setGuidance(result.status === "rejected" ? result.error : "");
      setSaved(true);
      return result.enrollment;
    } catch (e) {
      setError(e.message);
      return null;
    } finally {
      setBusy(false);
    }
  }
  async function collectAngle() {
    live.current = true;
    setCapturing(true);
    let current = enrollment;
    try {
      while (live.current && mounted.current) {
        const target = poses.find((p) => (current?.counts[p] || 0) < 8);
        if (!target) {
          setGuidance("All five angles captured. Continue to review.");
          break;
        }
        setPose(target);
        const started = performance.now();
        const result = await take(target);
        if (!result) break;
        current = result;
        await new Promise((resolve) =>
          setTimeout(resolve, Math.max(0, 400 - (performance.now() - started))),
        );
      }
    } finally {
      live.current = false;
      if (mounted.current) setCapturing(false);
    }
  }
  async function retake(p: string) {
    try {
      const e = await api(`enrollments/${enrollment.id}/poses/${p}/`, {
        method: "DELETE",
      });
      setEnrollment(e);
      setPose(p);
      setError("");
    } catch (e) {
      setError(e.message);
    }
  }
  async function scan() {
    setScanning(true);
    setError("");
    try {
      const initial = await api("ble/", { method: "POST", body: "{}" });
      const previous = initial.last_scan;
      for (let attempt = 0; attempt < 50 && mounted.current; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 300));
        const state = await api("ble/");
        if (state.error) throw new Error(state.error);
        if (!state.enabled)
          throw new Error("Bluetooth scanning is disabled on the MacBook.");
        if (
          state.last_scan != null &&
          state.last_scan !== previous &&
          (state.continuous || !state.scanning)
        ) {
          setTags(state.tags || []);
          setScanFinished(true);
          return;
        }
      }
      if (mounted.current)
        throw new Error(
          "Bluetooth scan did not finish. Check Bluetooth is on and allow your terminal to use Bluetooth in System Settings.",
        );
    } catch (e) {
      if (mounted.current) setError(e.message);
    } finally {
      if (mounted.current) setScanning(false);
    }
  }
  if (step === 4)
    return (
      <Card>
        <div className="success">
          <span className="success-check">✓</span>
          <p className="eyebrow">REGISTRATION COMPLETE</p>
          <h1>Personnel successfully enrolled</h1>
          <p>
            {form.rank} {form.name} is registered for Gate 1 verification.
          </p>
          <p>The monitor will load the new profile automatically.</p>
          <button className="primary" onClick={done}>
            Manage Personnel
          </button>
        </div>
      </Card>
    );
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Personnel Registration</h1>
          <p>
            Enroll personnel, assign a BLE beacon, and capture facial
            biometrics.
          </p>
        </div>
        <span className="badge neutral">
          {saved
            ? "Draft autosaved"
            : enrollment
              ? "Draft in progress"
              : "New registration"}
        </span>
      </div>
      <Card className="wizard">
        <div className="steps">
          {[
            "Personnel Information",
            "BLE Assignment",
            "Facial Enrollment",
            "Review & Save",
          ].map((t, i) => (
            <div
              key={t}
              className={
                "step " + (i === step ? "current" : i < step ? "complete" : "")
              }
            >
              <span>{i < step ? "✓" : i + 1}</span>
              <div>
                <small>STEP {i + 1}</small>
                <b>{t}</b>
              </div>
            </div>
          ))}
        </div>
        <div className="wizard-body">
          <div className="section-title">
            <span>{step + 1}</span>
            <div>
              <h2>
                {
                  [
                    "Personnel Information",
                    "BLE Beacon Assignment",
                    "Facial Enrollment",
                    "Review and Save",
                  ][step]
                }
              </h2>
              <p>
                {
                  [
                    "Enter the officer’s official identifying information.",
                    "Scan for the uniform beacon and verify that it is available.",
                    "Capture five clear angles for reliable gate verification.",
                    "Confirm all enrollment information before activation.",
                  ][step]
                }
              </p>
            </div>
          </div>
          {error && (
            <div className="notice error" role="alert">
              {error}
            </div>
          )}
          {step === 0 && (
            <>
              <div className="form-grid">
                <label>
                  <span>
                    Personnel ID <em>*</em>
                  </span>
                  <input
                    value={form.personnel_id}
                    maxLength={60}
                    onChange={(e) =>
                      setForm({ ...form, personnel_id: e.target.value })
                    }
                    placeholder="Personnel ID"
                    required
                  />
                  <small>Use the format shown on the official PNP ID.</small>
                </label>
                <label>
                  <span>
                    Full Name <em>*</em>
                  </span>
                  <input
                    value={form.name}
                    maxLength={120}
                    onChange={(e) => setForm({ ...form, name: e.target.value })}
                    placeholder="Full name"
                    required
                  />
                </label>
                <label>
                  <span>
                    Rank / Position <em>*</em>
                  </span>
                  <select
                    value={form.rank}
                    onChange={(e) => setForm({ ...form, rank: e.target.value })}
                    required
                  >
                    <option value="">Select rank / position</option>
                    {Array.from(
                      new Set(
                        [
                          "Patrolman / Patrolwoman",
                          "PCpl",
                          "PSSg",
                          "PMSg",
                          "PSMS",
                          "PCMS",
                          "PEMS",
                          "PLT",
                          "PCPT",
                          "PMAJ",
                          "PLTCOL",
                          "PCOL",
                          "PBGEN",
                          "PMGEN",
                          "PLTGEN",
                          "PGEN",
                          form.rank,
                        ].filter(Boolean),
                      ),
                    ).map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                </label>
                <label>
                  <span>
                    Unit / Department <em>*</em>
                  </span>
                  <select
                    value={customUnit ? "__custom" : form.unit}
                    onChange={(e) => {
                      if (e.target.value === "__custom") {
                        setCustomUnit(true);
                        setForm({ ...form, unit: "" });
                      } else {
                        setCustomUnit(false);
                        setForm({ ...form, unit: e.target.value });
                      }
                    }}
                    required
                  >
                    <option value="">Select unit / department</option>
                    {Array.from(
                      new Set(
                        [
                          "ITMS",
                          initial?.person?.unit,
                          customUnit ? null : form.unit,
                        ].filter(Boolean),
                      ),
                    ).map((v) => (
                      <option key={v as string}>{v as string}</option>
                    ))}
                    <option value="__custom">Other unit / department…</option>
                  </select>
                  {customUnit && (
                    <input
                      aria-label="Unit or department name"
                      value={form.unit}
                      maxLength={60}
                      onChange={(e) =>
                        setForm({ ...form, unit: e.target.value })
                      }
                      placeholder="Enter unit / department"
                      required
                    />
                  )}
                </label>
              </div>
              <div className="notice info">
                <Icon file="413bc" />
                <div>
                  <b>Verify official records</b>
                  <p>
                    Personnel information must match the officer’s current PNP
                    identification.
                  </p>
                </div>
              </div>
            </>
          )}
          {step === 1 && (
            <>
              <div className="beacon-heading">
                <div>
                  <h3>Nearby beacons</h3>
                  <p>Keep the beacon within 1 meter of this station.</p>
                </div>
                <button className="primary" onClick={scan} disabled={scanning}>
                  <Icon file="1c150" />
                  {scanning ? "Scanning…" : "Scan for Beacon"}
                </button>
              </div>
              <div className="beacon-list" aria-live="polite">
                {tags.map((t, i) => (
                  <div
                    className={
                      "beacon-row " + (form.ble_tag === t.tag ? "selected" : "")
                    }
                    key={t.tag + i}
                  >
                    <span className="beacon-symbol">
                      <Icon file="257a6" size={17} />
                    </span>
                    <div>
                      <b>{t.tag}</b>
                      <small>Detected just now</small>
                    </div>
                    <div className="signal-bars" aria-hidden="true">
                      {[0, 1, 2, 3, 4].map((n) => (
                        <i
                          key={n}
                          style={{
                            height: 5 + n * 3,
                            background:
                              n < (t.rssi >= -60 ? 5 : t.rssi >= -75 ? 4 : 2)
                                ? "#2f8c5a"
                                : "#d8e0e9",
                          }}
                        />
                      ))}
                    </div>
                    <div>
                      <b>{t.rssi} dBm</b>
                      <small>
                        {t.rssi >= -60
                          ? "Excellent signal"
                          : t.rssi >= -75
                            ? "Good signal"
                            : "Weak signal"}
                      </small>
                    </div>
                    <button
                      onClick={() => setForm({ ...form, ble_tag: t.tag })}
                    >
                      {form.ble_tag === t.tag ? "Selected" : "Select"}
                    </button>
                  </div>
                ))}
                {!tags.length && (
                  <div className="beacon-empty">
                    {scanning
                      ? "Scanning for nearby beacons…"
                      : scanFinished
                        ? "No compatible beacons found. Make sure your beacon is powered on and advertising its identifier."
                        : "Scan to discover nearby beacons."}
                  </div>
                )}
              </div>
              {form.ble_tag && (
                <div className="notice info assigned-beacon">
                  <Icon file="257a6" size={17} />
                  <span>
                    Assigned beacon: <b>{form.ble_tag}</b>
                  </span>
                </div>
              )}
              <details className="manual-beacon">
                <summary>Enter a known beacon identifier</summary>
                <label>
                  Assigned BLE identifier
                  <input
                    value={form.ble_tag || ""}
                    maxLength={120}
                    onChange={(e) =>
                      setForm({ ...form, ble_tag: e.target.value })
                    }
                    placeholder="Identifier advertised by your ESP32"
                  />
                </label>
              </details>
            </>
          )}
          {step === 2 && (
            <>
              {guidance && (
                <div className="enrollment-guidance" role="status">
                  <span>!</span>
                  <div>
                    <b>Capture guidance</b>
                    <p>{guidance}</p>
                  </div>
                </div>
              )}
              <div className="capture-grid">
                <div className="capture-video">
                  <div className="capture-status">
                    <span>
                      <i className={stream ? "connected" : ""} />
                      {stream ? "Camera connected" : "Camera off"}
                    </span>
                    <span>HD · Gate Enrollment Station</span>
                  </div>
                  {stream && <div className="face-oval" aria-hidden="true" />}
                  {stream ? (
                    <video ref={video} autoPlay playsInline muted />
                  ) : (
                    <div className="camera-placeholder">
                      <span>◎</span>
                      <p>Align face within the oval and look directly ahead.</p>
                      <button onClick={startCamera}>Enable camera</button>
                    </div>
                  )}
                  {stream && (
                    <div className="pose-hint">
                      {
                        {
                          front: "Look straight ahead",
                          left: "Turn gently toward your left shoulder",
                          right: "Turn gently toward your right shoulder",
                          up: "Lift your chin slightly",
                          down: "Lower your chin slightly",
                        }[pose]
                      }
                    </div>
                  )}
                </div>
                <div className="pose-list">
                  <h4>Required angles</h4>
                  {poses.map((p) => (
                    <div className="pose-row" key={p}>
                      <button
                        className={pose === p ? "pose-active" : ""}
                        onClick={() => setPose(p)}
                        disabled={busy || capturing}
                      >
                        {(enrollment?.counts[p] || 0) === 8 ? "✓" : "○"}{" "}
                        {
                          {
                            front: "Front",
                            left: "Left profile",
                            right: "Right profile",
                            up: "Look up",
                            down: "Look down",
                          }[p]
                        }
                        <small>
                          {(enrollment?.counts[p] || 0) === 8
                            ? "Captured"
                            : `${enrollment?.counts[p] || 0} / 8 samples`}
                        </small>
                      </button>
                      {(enrollment?.counts[p] || 0) > 0 && (
                        <button
                          className="text-button"
                          onClick={() => retake(p)}
                          disabled={busy || capturing}
                        >
                          Retake
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
              <div className="button-row capture-actions">
                {stream && !capturing && (
                  <button
                    className="primary"
                    disabled={
                      busy ||
                      poses.every((p) => (enrollment?.counts[p] || 0) === 8)
                    }
                    onClick={collectAngle}
                  >
                    Begin registration
                  </button>
                )}
                {capturing && (
                  <button
                    onClick={() => {
                      live.current = false;
                    }}
                  >
                    Pause capture
                  </button>
                )}
                {stream && !capturing && (
                  <button onClick={stopCamera} disabled={busy}>
                    Stop camera
                  </button>
                )}
                <small>
                  {poses.reduce(
                    (total, p) => total + (enrollment?.counts[p] || 0),
                    0,
                  )}{" "}
                  / 40 samples · Follow the angle guidance
                </small>
              </div>
              <div className="sample-grid">
                {poses.map((p) => {
                  const s = enrollment?.samples.find((s) => s.pose === p);
                  return (
                    <div className="sample-card" key={p}>
                      {s ? (
                        <img src={s.url} alt={`${p} sample`} />
                      ) : (
                        <div className="sample-placeholder">—</div>
                      )}
                      <b>{p}</b>
                      <small>
                        {(enrollment?.counts[p] || 0) === 8
                          ? "Captured"
                          : `${enrollment?.counts[p] || 0} / 8 samples`}
                      </small>
                    </div>
                  );
                })}
              </div>
            </>
          )}
          {step === 3 && (
            <div className="review-grid">
              <div className="review-panel">
                <div className="review-person">
                  <Avatar
                    name={form.name}
                    url={
                      enrollment?.samples.find((s) => s.pose === "front")?.url
                    }
                  />
                  <div>
                    <span className="eyebrow">PERSONNEL</span>
                    <h2>
                      {form.rank} {form.name}
                    </h2>
                    <p>
                      {form.personnel_id} · {form.unit}
                    </p>
                  </div>
                  <button className="text-button" onClick={() => setStep(0)}>
                    Edit
                  </button>
                </div>
                <hr />
                <span className="eyebrow">ASSIGNED BLE BEACON</span>
                <h3>{form.ble_tag}</h3>
                <small>Assignment uniqueness checked when saved.</small>
                <hr />
                <span className="eyebrow">CAPTURED FACE ANGLES</span>
                <div className="sample-grid">
                  {poses.map((p) => {
                    const s = enrollment?.samples.find((s) => s.pose === p);
                    return (
                      <div key={p}>
                        {s && (
                          <img className="review-thumb" src={s.url} alt={p} />
                        )}
                        <small>{p}</small>
                      </div>
                    );
                  })}
                </div>
              </div>
              <div className="review-panel">
                <h3>Enrollment Status</h3>
                {[
                  "Information complete",
                  "Beacon assigned",
                  "40 face samples accepted",
                ].map((t) => (
                  <div className="check-row" key={t}>
                    <span>✓</span>
                    {t}
                  </div>
                ))}
                <div className="notice info">
                  Ready to save. The profile becomes active after confirmation.
                </div>
              </div>
            </div>
          )}
        </div>
        <div className="wizard-footer">
          <button
            disabled={busy || capturing}
            onClick={() => {
              if (step === 0) {
                stopCamera();
                done();
              } else {
                if (step === 2) stopCamera();
                setStep(step - 1);
              }
            }}
          >
            <Icon file="b56f8" />
            {step === 0 ? "Cancel" : "Back"}
          </button>
          <small>Step {step + 1} of 4</small>
          <button
            className="primary"
            disabled={
              busy ||
              capturing ||
              scanning ||
              (step === 1 && !form.ble_tag) ||
              (step === 2 &&
                poses.some((p) => (enrollment?.counts[p] || 0) !== 8))
            }
            onClick={next}
          >
            {step === 3 ? (
              "✓ Save Registration"
            ) : (
              <>
                Next <Icon file="a1322" />
              </>
            )}
          </button>
        </div>
      </Card>
    </>
  );
}
function Monitor({ user, go, logout }: any) {
  const [state, setState] = useState<any>(null),
    [error, setError] = useState(""),
    [streamVersion, setStreamVersion] = useState(0),
    [previewError, setPreviewError] = useState(false),
    [clock, setClock] = useState(new Date()),
    [busy, setBusy] = useState(false);
  const commandEpoch = useRef(0);
  const commandPending = useRef(false);
  const [presenceReady, setPresenceReady] = useState(false);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    let active = true;
    let suspended = false;
    let timer: ReturnType<typeof setTimeout>;
    let presence: EventSource;
    const connect = () => {
      presence = new EventSource("/api/monitor/presence/");
      presence.onmessage = () => {
        if (active) setPresenceReady(true);
      };
      presence.onerror = () => {
        if (active) setPresenceReady(false);
      };
    };
    const refresh = async () => {
      let delay = 150;
      const epoch = commandEpoch.current;
      try {
        if (commandPending.current) return;
        const s = await api("monitor/");
        if (!active || suspended || epoch !== commandEpoch.current) return;
        setState(s);
        setError("");
        delay = s.running || s.requested ? 150 : 1000;
      } catch (e) {
        if (active && !suspended && epoch === commandEpoch.current)
          setError(e.message);
      } finally {
        if (active && !suspended) timer = setTimeout(refresh, delay);
      }
    };
    const hide = () => {
      suspended = true;
      clearTimeout(timer);
      presence.close();
      setPresenceReady(false);
    };
    const show = (event: PageTransitionEvent) => {
      if (!event.persisted) return;
      suspended = false;
      connect();
      void refresh();
    };
    window.addEventListener("pagehide", hide);
    window.addEventListener("pageshow", show);
    connect();
    refresh();
    const clockTimer = setInterval(() => setClock(new Date()), 1000);
    return () => {
      active = false;
      clearTimeout(timer);
      clearInterval(clockTimer);
      window.removeEventListener("pagehide", hide);
      window.removeEventListener("pageshow", show);
      presence.close();
      if (retryTimer.current) clearTimeout(retryTimer.current);
    };
  }, []);
  useEffect(() => {
    if (!state?.requested) {
      setStreamVersion(0);
      setPreviewError(false);
      if (retryTimer.current) clearTimeout(retryTimer.current);
    }
  }, [state?.requested, state?.running]);
  async function control(body: any) {
    commandEpoch.current += 1;
    commandPending.current = true;
    setBusy(true);
    try {
      const next = await api("monitor/", {
        method: "POST",
        body: JSON.stringify(body),
      });
      if (body.action === "start") setStreamVersion((v) => v + 1);
      setState(next);
      setError("");
    } catch (e) {
      setError(e.message);
    } finally {
      commandPending.current = false;
      commandEpoch.current += 1;
      setBusy(false);
    }
  }
  const event = state?.latest;
  return (
    <div className="monitor-page">
      <div className="monitor-header">
        <div>
          <small>
            <Icon file="f9ba9" size={14} />
            PNP Camp Crame Access Control
          </small>
          <h1>Gate 1 Live Monitor</h1>
        </div>
        <div className="button-row">
          <div className="clock">
            <small>
              {clock.toLocaleDateString("en-PH", { timeZone: "Asia/Manila" })}
            </small>
            <b>
              {clock.toLocaleTimeString("en-PH", { timeZone: "Asia/Manila" })}
            </b>
          </div>
          {user.is_admin && (
            <button onClick={() => go("/")}>Admin dashboard</button>
          )}
          <button onClick={logout}>Log out</button>
        </div>
      </div>
      <main>
        <div className="page-heading">
          <div>
            <h2>Live gate verification</h2>
            <p>
              BLE identity is verified first; face recognition provides final
              confirmation. The camera stops when the last monitoring tab
              closes.
            </p>
          </div>
          <div className="button-row">
            <label className="inline-label">
              Mode
              <select
                value={state?.direction || "entry"}
                disabled={busy}
                onChange={(e) => control({ direction: e.target.value })}
              >
                <option value="entry">Entry</option>
                <option value="exit">Exit</option>
              </select>
            </label>
            <button
              className={state?.requested ? "" : "primary"}
              disabled={busy || (!presenceReady && !state?.requested)}
              onClick={() =>
                control({
                  action: state?.requested ? "stop" : "start",
                })
              }
            >
              {state?.requested ? "Stop camera" : "Start camera"}
            </button>
          </div>
        </div>
        {!presenceReady && (
          <div className="notice" role="status">
            Connecting to the local monitoring service…
          </div>
        )}
        {error && (
          <div className="notice error" role="alert">
            {error}
          </div>
        )}
        {state?.error && <div className="notice error">{state.error}</div>}
        {state?.camera_note && (
          <div className="notice">{state.camera_note}</div>
        )}
        {state?.requested && !state?.running && (
          <div className="notice" role="status">
            Starting camera…
          </div>
        )}
        {state?.recognition_status === "loading" && !state?.error && (
          <div className="notice" role="status">
            Loading face recognition… Live verification will begin when the
            models are ready.
          </div>
        )}
        {previewError && state?.requested && (
          <div className="notice" role="status">
            Reconnecting video preview… The camera is still running.
          </div>
        )}
        <div className="monitor-grid">
          <section className="monitor-feed">
            {state?.requested ? (
              <>
                <img
                  key={`${state.camera_session}:${streamVersion}`}
                  src={`/api/monitor/stream/?session=${state.camera_session}&v=${streamVersion}`}
                  alt="Live webcam preview"
                  onLoad={() => setPreviewError(false)}
                  onError={() => {
                    setPreviewError(true);
                    if (retryTimer.current) clearTimeout(retryTimer.current);
                    retryTimer.current = setTimeout(
                      () => setStreamVersion((v) => v + 1),
                      1000,
                    );
                  }}
                />
                <svg
                  className="recognition-overlay"
                  viewBox={`0 0 ${state.overlay_size?.[0] || 1280} ${state.overlay_size?.[1] || 720}`}
                  preserveAspectRatio="xMidYMid meet"
                  aria-label="Recognition overlays"
                >
                  {(state.overlays || []).map((face, i) => (
                    <g
                      key={i}
                      className={
                        face.confirmed ? "face-confirmed" : "face-pending"
                      }
                    >
                      <rect
                        x={face.box[0]}
                        y={face.box[1]}
                        width={face.box[2]}
                        height={face.box[3]}
                      />
                      <text
                        x={Math.max(0, face.box[0])}
                        y={Math.max(22, face.box[1] - 10)}
                      >
                        {face.label}
                      </text>
                    </g>
                  ))}
                </svg>
              </>
            ) : (
              <div className="monitor-empty">
                <span>◎</span>
                <h2>Ready to monitor</h2>
                <p>Start the camera on this MacBook to begin verification.</p>
              </div>
            )}
            <div className="feed-top">
              <span className="feed-label">
                <i className={"dot " + (!state?.running ? "offline" : "")} />
                {state?.running ? "LIVE" : "CAMERA OFF"}
              </span>
              <span className="feed-label">
                Gate 1 · {state?.preview_fps || 0} FPS preview ·{" "}
                {state?.recognition_fps || 0} FPS recognition
              </span>
            </div>
            <div className="feed-bottom">
              <span>
                BLE:{" "}
                {state?.tags?.map((t) => t.tag).join(", ") || "No nearby tags"}
              </span>
              <span>{state?.direction || "entry"} mode</span>
            </div>
          </section>
          <Card className="decision-panel">
            <div className="decision-heading">
              <div>
                <span className="eyebrow">FINAL DECISION</span>
                <h2>
                  {event?.outcome === "verified"
                    ? "Access verified"
                    : event
                      ? "Verification incomplete"
                      : "Awaiting verification"}
                </h2>
              </div>
              {event && <Badge value={event.outcome} />}
            </div>
            <div className="sequence">
              <h4>VERIFICATION SEQUENCE</h4>
              <div className="check-row">
                <span className={state?.tags?.length ? "" : "pending"}>
                  {state?.tags?.length ? "✓" : "1"}
                </span>
                <div>
                  <b>BLE beacon detected</b>
                  <small>
                    {state?.tags
                      ?.map((t) => `${t.tag} · ${t.rssi} dBm`)
                      .join(", ") || "Waiting for an assigned tag"}
                  </small>
                </div>
              </div>
              <div className="check-row">
                <span
                  className={event?.outcome === "verified" ? "" : "pending"}
                >
                  {event?.outcome === "verified" ? "✓" : "2"}
                </span>
                <div>
                  <b>Face recognition confirmed</b>
                  <small>
                    {event?.outcome === "verified"
                      ? event.name
                      : "Waiting for a clear matching face"}
                  </small>
                </div>
              </div>
            </div>
            {event ? (
              <div className="monitor-person">
                <div className="person-cell">
                  <PersonPhoto
                    name={event.name}
                    url={event.captured_image_url}
                  />
                  <div>
                    <b>{event.name}</b>
                    <small>{event.personnel_id || "Unknown identity"}</small>
                  </div>
                </div>
                <dl>
                  <div>
                    <dt>UNIT</dt>
                    <dd>{event.unit || "—"}</dd>
                  </div>
                  <div>
                    <dt>BLE BEACON ID</dt>
                    <dd>{event.ble_tag || "—"}</dd>
                  </div>
                  <div>
                    <dt>MATCH SCORE</dt>
                    <dd>{event.similarity?.toFixed(3) || "—"}</dd>
                  </div>
                  <div>
                    <dt>DIRECTION</dt>
                    <dd>{event.direction}</dd>
                  </div>
                </dl>
              </div>
            ) : (
              <Empty>No completed verification yet.</Empty>
            )}
            <p className="footnote">
              {state?.gallery_count || 0} active profiles · {state?.queued || 0}{" "}
              queued events
            </p>
            <p className="footnote">
              {state?.gallery_fresh
                ? "Gallery up to date"
                : "Gallery not confirmed current"}{" "}
              ·{" "}
              {state?.backend_ok ? "Backend connected" : "Backend disconnected"}
            </p>
            <p className="footnote">
              {state?.ble_enabled
                ? state?.ble_last_scan
                  ? "BLE scanner reporting"
                  : "BLE scan pending"
                : "BLE scanner disabled"}
            </p>
          </Card>
        </div>
        <p className="footnote">
          Prototype: direction is selected by the operator. Physical passage and
          gate relay control are not connected. Face similarity is not identity
          probability.
        </p>
      </main>
    </div>
  );
}
createRoot(document.getElementById("root")!).render(<App />);

function Password({ close }: any) {
  const [old, setOld] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState("");
  async function submit(e: any) {
    e.preventDefault();
    try {
      const d = await api("password/", {
        method: "POST",
        body: JSON.stringify({ old_password: old, password }),
      });
      csrf = d.csrf;
      close();
    } catch (e) {
      setError(e.message);
    }
  }
  return (
    <Modal title="Change password" close={close}>
      <form onSubmit={submit}>
        <label>
          Current password
          <input
            type="password"
            autoComplete="current-password"
            required
            value={old}
            onChange={(e) => setOld(e.target.value)}
          />
        </label>
        <label>
          New password
          <input
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <div className="notice error">{error}</div>}
        <button className="primary">Change password</button>
      </form>
    </Modal>
  );
}
