import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ApiError, getMeta, getRequest, getSummary, listRequests, staffLogin, staffLogout, staffMe, updateStatus } from "./api";
import type { Meta, RequestDetail, RequestPage, SessionInfo, StaffSummary } from "./types";

const PAGE_SIZE = 10;

function fmt(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export default function StaffApp() {
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [checking, setChecking] = useState(true);
  const [meta, setMeta] = useState<Meta | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getMeta().then(setMeta).catch(() => setMeta(null));
    staffMe()
      .then(setSession)
      .catch(() => setSession(null))
      .finally(() => setChecking(false));
  }, []);

  const expire = useCallback(() => {
    setSession(null);
    setNotice("Your session has ended. Please sign in again.");
  }, []);

  if (checking) return <p className="center-note">Checking your session…</p>;
  if (!session) return <Login notice={notice} onLoggedIn={(s) => { setNotice(null); setSession(s); }} />;
  return <Dashboard session={session} meta={meta} onExpired={expire} onLogout={() => setSession(null)} />;
}

// ----------------------------------------------------------------------------- login

function Login({ onLoggedIn, notice }: { onLoggedIn: (s: SessionInfo) => void; notice: string | null }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onLoggedIn(await staffLogin(username, password));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Please try again.");
      setPassword("");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page narrow">
      <div className="banner" role="note">
        <strong>Independent academic prototype.</strong> Staff area for the demonstration database only.
      </div>
      <main className="login-card">
        <h1>Staff sign in</h1>
        <p className="muted">Authorized demo staff only. Access is checked on the server.</p>
        {notice && <p className="notice info" role="status">{notice}</p>}
        <form onSubmit={submit} noValidate>
          <label htmlFor="username">Username</label>
          <input id="username" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" maxLength={64} required />
          <label htmlFor="password">Password</label>
          <input id="password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" maxLength={256} required />
          {error && <p className="notice error" role="alert">{error}</p>}
          <button type="submit" className="btn btn-primary" disabled={busy || !username || !password}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
        <p className="small"><a href="/">← Back to the customer chat</a></p>
      </main>
    </div>
  );
}

// ----------------------------------------------------------------------------- dashboard

function Dashboard({ session, meta, onExpired, onLogout }: { session: SessionInfo; meta: Meta | null; onExpired: () => void; onLogout: () => void }) {
  const [summary, setSummary] = useState<StaffSummary | null>(null);
  const [page, setPage] = useState<RequestPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [status, setStatus] = useState("");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [pageNo, setPageNo] = useState(1);
  const [selected, setSelected] = useState<string | null>(null);
  const reqSeq = useRef(0);

  const label = (s: string) => meta?.status_labels[s] ?? s;
  const statuses = meta?.statuses ?? (summary ? Object.keys(summary.counts) : []);

  useEffect(() => {
    const t = setTimeout(() => {
      setDebouncedQ(q);
      setPageNo(1);
    }, 300);
    return () => clearTimeout(t);
  }, [q]);

  const handleError = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        onExpired();
        return;
      }
      setError(e instanceof ApiError ? e.message : "Something went wrong.");
    },
    [onExpired],
  );

  const refresh = useCallback(async () => {
    const seq = ++reqSeq.current;
    setLoading(true);
    setError(null);
    try {
      const [s, p] = await Promise.all([getSummary(), listRequests({ q: debouncedQ, status, order, page: pageNo, pageSize: PAGE_SIZE })]);
      if (seq !== reqSeq.current) return; // a newer request superseded this one
      setSummary(s);
      setPage(p);
    } catch (e) {
      if (seq === reqSeq.current) handleError(e);
    } finally {
      if (seq === reqSeq.current) setLoading(false);
    }
  }, [debouncedQ, status, order, pageNo, handleError]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function logout() {
    try {
      await staffLogout();
    } catch {
      /* the cookie is cleared client-side by the server response; either way show the login screen */
    }
    onLogout();
  }

  const filtersActive = Boolean(debouncedQ || status);

  return (
    <div className="page wide">
      <div className="banner" role="note">
        <strong>Independent academic prototype.</strong> Demonstration data only. Not connected to SportFit's real service system.
      </div>
      <header className="topbar">
        <div className="brand">
          <h1>Staff dashboard</h1>
        </div>
        <div className="topbar-actions">
          <span className="muted small">Signed in as {session.username}</span>
          <a className="btn btn-ghost" href="/">Customer chat</a>
          <button type="button" className="btn btn-secondary" onClick={() => void logout()}>Sign out</button>
        </div>
      </header>

      <main className="dash">
        <section aria-label="Request counts by status" className="cards">
          <button type="button" className={`card${status === "" ? " active" : ""}`} onClick={() => { setStatus(""); setPageNo(1); }}>
            <span className="card-n">{summary ? summary.total : "–"}</span>
            <span className="card-l">All requests</span>
          </button>
          {statuses.map((s) => (
            <button key={s} type="button" className={`card${status === s ? " active" : ""}`} onClick={() => { setStatus(s); setPageNo(1); }}>
              <span className="card-n">{summary ? summary.counts[s] ?? 0 : "–"}</span>
              <span className="card-l">{label(s)}</span>
            </button>
          ))}
        </section>

        <section aria-label="Filters" className="filters">
          <div>
            <label htmlFor="search">Search</label>
            <input id="search" type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Reference, name, email, equipment…" maxLength={100} />
          </div>
          <div>
            <label htmlFor="status-filter">Status</label>
            <select id="status-filter" value={status} onChange={(e) => { setStatus(e.target.value); setPageNo(1); }}>
              <option value="">All statuses</option>
              {statuses.map((s) => (
                <option key={s} value={s}>{label(s)}</option>
              ))}
            </select>
          </div>
          <div>
            <label htmlFor="order">Sort by date</label>
            <select id="order" value={order} onChange={(e) => { setOrder(e.target.value as "asc" | "desc"); setPageNo(1); }}>
              <option value="desc">Newest first</option>
              <option value="asc">Oldest first</option>
            </select>
          </div>
        </section>

        <div className="split">
          <section aria-label="Repair requests" className="table-wrap">
            {error && (
              <p className="notice error" role="alert">
                {error} <button type="button" className="link" onClick={() => void refresh()}>Retry</button>
              </p>
            )}
            <table>
              <caption className="sr-only">Repair requests</caption>
              <thead>
                <tr>
                  <th scope="col">Reference</th>
                  <th scope="col">Created</th>
                  <th scope="col">Customer</th>
                  <th scope="col">Equipment</th>
                  <th scope="col">Problem</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {page?.items.map((r) => (
                  <tr key={r.request_reference} className={selected === r.request_reference ? "selected" : ""}>
                    <td>
                      <button type="button" className="link mono" onClick={() => setSelected(r.request_reference)} aria-label={`Open request ${r.request_reference}`}>
                        {r.request_reference}
                      </button>
                    </td>
                    <td>{fmt(r.created_at)}</td>
                    <td>{r.customer_name}</td>
                    <td>{r.equipment_type}</td>
                    <td className="problem">{r.problem_summary}</td>
                    <td><StatusPill status={r.status} label={label(r.status)} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
            {loading && !page && <p className="center-note">Loading requests…</p>}
            {loading && page && <p className="muted small">Updating…</p>}
            {!loading && !error && page && page.items.length === 0 && (
              <p className="empty">
                {filtersActive ? "No requests match your search or filter." : "No requests have been recorded yet. Requests created in the customer chat will appear here."}
              </p>
            )}
            {page && page.total > 0 && (
              <nav className="pager" aria-label="Pagination">
                <button type="button" className="btn btn-secondary" disabled={pageNo <= 1 || loading} onClick={() => setPageNo((p) => p - 1)}>Previous</button>
                <span>Page {page.page} of {page.total_pages} · {page.total} request{page.total === 1 ? "" : "s"}</span>
                <button type="button" className="btn btn-secondary" disabled={pageNo >= page.total_pages || loading} onClick={() => setPageNo((p) => p + 1)}>Next</button>
              </nav>
            )}
          </section>

          <aside aria-label="Request details" className="detail">
            {selected ? (
              <Detail key={selected} reference={selected} statuses={statuses} label={label} onExpired={onExpired} onChanged={() => void refresh()} onClose={() => setSelected(null)} />
            ) : (
              <p className="empty">Select a request to see its details.</p>
            )}
          </aside>
        </div>
      </main>
    </div>
  );
}

function StatusPill({ status, label }: { status: string; label: string }) {
  return <span className={`pill pill-${status}`}>{label}</span>;
}

// ----------------------------------------------------------------------------- detail

function Detail({ reference, statuses, label, onExpired, onChanged, onClose }: {
  reference: string;
  statuses: string[];
  label: (s: string) => string;
  onExpired: () => void;
  onChanged: () => void;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<RequestDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [newStatus, setNewStatus] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getRequest(reference)
      .then((d) => {
        if (!live) return;
        setDetail(d);
        setNewStatus(d.status);
      })
      .catch((e) => {
        if (!live) return;
        if (e instanceof ApiError && e.status === 401) onExpired();
        else setError(e instanceof ApiError ? e.message : "Could not load this request.");
      });
    return () => {
      live = false;
    };
  }, [reference, onExpired]);

  async function save() {
    if (!detail || newStatus === detail.status) return;
    setSaving(true);
    setError(null);
    setSaved(null);
    try {
      // The screen changes only after the server confirms the update.
      const updated = await updateStatus(reference, newStatus);
      setDetail(updated);
      setNewStatus(updated.status);
      setSaved(`Status updated to "${label(updated.status)}".`);
      onChanged();
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onExpired();
      else setError(e instanceof ApiError ? e.message : "The status could not be updated.");
    } finally {
      setSaving(false);
    }
  }

  if (error && !detail) return <p className="notice error" role="alert">{error}</p>;
  if (!detail) return <p className="center-note">Loading details…</p>;

  const rows: [string, string | null][] = [
    ["Customer", detail.customer_name],
    ["Email", detail.email],
    ["Phone", detail.phone],
    ["Service area", detail.service_area],
    ["Preferred contact", detail.preferred_contact_method],
    ["Preferred time", detail.preferred_appointment_time],
    ["Equipment", detail.equipment_type],
    ["Brand", detail.equipment_brand],
    ["Model", detail.equipment_model],
    ["Serial number", detail.serial_number],
    ["Created", fmt(detail.created_at)],
    ["Last updated", fmt(detail.updated_at)],
  ];

  return (
    <div>
      <div className="detail-head">
        <h2 className="mono">{detail.request_reference}</h2>
        <button type="button" className="btn btn-ghost small" onClick={onClose}>Close</button>
      </div>
      <StatusPill status={detail.status} label={label(detail.status)} />
      <dl className="facts">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt>{k}</dt>
            <dd>{v || <span className="muted">Not provided</span>}</dd>
          </div>
        ))}
      </dl>
      <h3>Problem description</h3>
      <p className="problem-full">{detail.problem_description}</p>

      <h3>Update status</h3>
      <div className="status-edit">
        <label htmlFor="new-status" className="sr-only">New status</label>
        <select id="new-status" value={newStatus} onChange={(e) => { setNewStatus(e.target.value); setSaved(null); }} disabled={saving}>
          {statuses.map((s) => (
            <option key={s} value={s}>{label(s)}</option>
          ))}
        </select>
        <button type="button" className="btn btn-primary" onClick={() => void save()} disabled={saving || newStatus === detail.status}>
          {saving ? "Saving…" : "Update status"}
        </button>
      </div>
      {saved && <p className="notice ok" role="status">{saved}</p>}
      {error && <p className="notice error" role="alert">{error}</p>}

      <h3>Status history</h3>
      {detail.history.length === 0 ? (
        <p className="muted small">No status changes yet.</p>
      ) : (
        <ol className="history">
          {detail.history.map((h, i) => (
            <li key={i}>
              {label(h.old_status)} → <strong>{label(h.new_status)}</strong>
              <span className="muted small"> · {h.changed_by} · {fmt(h.changed_at)}</span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
