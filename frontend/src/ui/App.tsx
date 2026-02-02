import React, { useCallback, useEffect, useMemo, useState } from "react";

const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000";
const TOKEN_KEY = "agentsocials_token";

type ExecutionSummary = {
  execution_id: string;
  user_id: string;
  url: string;
  status: string;
  updated_at: string;
  interrupt?: unknown;
};

type ExecutionStateResponse = {
  execution_id: string;
  status: string;
  state: Record<string, unknown>;
};

type Connection = {
  id: number;
  provider: string;
  account_id: string;
  display_name: string;
  label: string;
  is_default: boolean;
};

type MeResponse = { user_id: string; email: string };

function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

function apiHeaders(token: string | null): HeadersInit {
  const h: HeadersInit = { "Content-Type": "application/json" };
  if (token) (h as Record<string, string>)["Authorization"] = `Bearer ${token}`;
  return h;
}

async function apiGet<T>(path: string, token: string | null): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, { headers: apiHeaders(token), credentials: "include" });
  if (!r.ok) throw new Error(await r.text());
  return (await r.json()) as T;
}

async function apiPost<T>(path: string, body: unknown, token: string | null): Promise<T> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "POST",
    headers: apiHeaders(token),
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
  return (await r.json()) as T;
}

async function apiPatch(path: string, body: unknown, token: string | null): Promise<void> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "PATCH",
    headers: apiHeaders(token),
    credentials: "include",
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(await r.text());
}

async function apiDelete(path: string, token: string | null): Promise<void> {
  const r = await fetch(`${API_BASE}${path}`, {
    method: "DELETE",
    headers: apiHeaders(token),
    credentials: "include",
  });
  if (!r.ok) throw new Error(await r.text());
}

// ---- Auth gate ----
function AuthView({ onLogin }: { onLogin: (token: string) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signup, setSignup] = useState(false);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit() {
    setErr("");
    setLoading(true);
    try {
      const path = signup ? "/v1/auth/signup" : "/v1/auth/login";
      const res = await apiPost<{ access_token: string }>(path, { email, password }, null);
      localStorage.setItem(TOKEN_KEY, res.access_token);
      onLogin(res.access_token);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div style={{ maxWidth: 400, margin: "40px auto", padding: 24 }}>
      <div className="card">
        <div style={{ fontWeight: 800, marginBottom: 16 }}>{signup ? "Sign up" : "Sign in"}</div>
        {err ? <div style={{ color: "#b91c1c", marginBottom: 8, fontSize: 14 }}>{err}</div> : null}
        <div style={{ display: "grid", gap: 10 }}>
          <input
            type="email"
            placeholder="Email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <input
            type="password"
            placeholder="Password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button disabled={loading || !email || !password} onClick={submit}>
            {signup ? "Sign up" : "Sign in"}
          </button>
          <button type="button" className="secondary" onClick={() => { setSignup(!signup); setErr(""); }}>
            {signup ? "Already have an account? Sign in" : "Need an account? Sign up"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ---- Main app (after login) ----
function MainApp({ token }: { token: string }) {
  const [user, setUser] = useState<MeResponse | null>(null);
  const [connections, setConnections] = useState<Connection[]>([]);
  const [inbox, setInbox] = useState<ExecutionSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selected, setSelected] = useState<ExecutionStateResponse | null>(null);
  const [err, setErr] = useState("");
  const [loading, setLoading] = useState(false);
  const [url, setUrl] = useState("");
  const [editedTwitter, setEditedTwitter] = useState("");
  const [editedLinkedin, setEditedLinkedin] = useState("");
  const [approveContent, setApproveContent] = useState(false);
  const [rejectContent, setRejectContent] = useState(false);
  const [approveImage, setApproveImage] = useState(false);
  const [rejectImage, setRejectImage] = useState(false);
  const [regenTwitter, setRegenTwitter] = useState(false);
  const [regenLinkedin, setRegenLinkedin] = useState(false);
  const [twitterConnectionId, setTwitterConnectionId] = useState<number | null>(null);
  const [linkedinConnectionId, setLinkedinConnectionId] = useState<number | null>(null);
  const [editingLabelId, setEditingLabelId] = useState<number | null>(null);
  const [editingLabelValue, setEditingLabelValue] = useState("");
  const [showDebug, setShowDebug] = useState(false);

  const selectedState = selected?.state || {};
  const img = (selectedState.image_metadata as Record<string, unknown>) || {};

  const interrupt = useMemo(() => {
    const intr = selectedState.__interrupt__ as unknown[] | undefined;
    return intr?.[0] && typeof intr[0] === "object" && "value" in intr[0] ? (intr[0] as { value: unknown }).value : null;
  }, [selectedState]);

  const userId = user?.user_id ?? "";

  const refreshConnections = useCallback(() => {
    if (!token) return;
    apiGet<Connection[]>("/v1/connections", token).then(setConnections).catch((e) => setErr(String(e)));
  }, [token]);

  const refreshInbox = useCallback(() => {
    if (!token) return;
    setErr("");
    apiGet<ExecutionSummary[]>("/v1/inbox", token)
      .then((data) => {
        setInbox(data);
        if (!selectedId && data[0]?.execution_id) setSelectedId(data[0].execution_id);
      })
      .catch((e) => setErr(String(e)));
  }, [token, selectedId]);

  const loadExecution = useCallback(
    (executionId: string) => {
      setErr("");
      apiGet<ExecutionStateResponse>(`/v1/executions/${executionId}`, token).then((data) => {
        setSelected(data);
        setSelectedId(executionId);
        const state = data?.state || {};
        setEditedTwitter((state.twitter_draft as string) || "");
        setEditedLinkedin((state.linkedin_draft as string) || "");
        setApproveContent(false);
        setRejectContent(false);
        setApproveImage(false);
        setRejectImage(false);
        setRegenTwitter(false);
        setRegenLinkedin(false);
      }).catch((e) => setErr(String(e)));
    },
    [token]
  );

  useEffect(() => {
    apiGet<MeResponse>("/v1/auth/me", token).then(setUser).catch(() => localStorage.removeItem(TOKEN_KEY));
  }, [token]);
  useEffect(() => {
    if (userId) refreshConnections();
  }, [userId, refreshConnections]);
  useEffect(() => {
    refreshInbox();
  }, [refreshInbox]);
  useEffect(() => {
    if (selectedId) loadExecution(selectedId);
  }, [selectedId, loadExecution]);

  const RUNNING_POLL_MS = 8000;
  useEffect(() => {
    if (selected?.status !== "running" || !selectedId) return;
    const interval = setInterval(() => loadExecution(selectedId), RUNNING_POLL_MS);
    return () => clearInterval(interval);
  }, [selected?.status, selectedId, loadExecution]);

  async function createExecution() {
    setLoading(true);
    setErr("");
    try {
      const res = await apiPost<ExecutionStateResponse>("/v1/executions", { url }, token);
      await refreshInbox();
      await loadExecution(res.execution_id);
      setUrl("");
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  /** Load image via Image with CORS, draw to canvas, return base64. Works when the host allows CORS for the image. */
  function imageUrlToBase64ViaCanvas(url: string): Promise<string | null> {
    return new Promise((res) => {
      const img = new Image();
      img.onload = () => {
        try {
          const canvas = document.createElement("canvas");
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          const ctx = canvas.getContext("2d");
          if (!ctx) {
            res(null);
            return;
          }
          ctx.drawImage(img, 0, 0);
          const dataUrl = canvas.toDataURL("image/png");
          const i = dataUrl.indexOf(",");
          res(i >= 0 ? dataUrl.slice(i + 1) : dataUrl);
        } catch {
          res(null);
        }
      };
      img.onerror = () => res(null);
      img.crossOrigin = "anonymous"; // must be before src
      img.src = url;
    });
  }

  /** Fetch image as base64: try fetch; else canvas (CORS); else backend proxy. */
  async function fetchImageAsBase64(imageUrl: string, articleUrl?: string): Promise<string | null> {
    try {
      const r = await fetch(imageUrl, { mode: "cors" });
      if (r.ok) {
        const blob = await r.blob();
        return new Promise((res) => {
          const reader = new FileReader();
          reader.onload = () => {
            const s = reader.result as string;
            const i = s.indexOf(",");
            res(i >= 0 ? s.slice(i + 1) : s);
          };
          reader.onerror = () => res(null);
          reader.readAsDataURL(blob);
        });
      }
    } catch {
      /* fall through */
    }
    const viaCanvas = await imageUrlToBase64ViaCanvas(imageUrl);
    if (viaCanvas) return viaCanvas;
    try {
      const params = new URLSearchParams({ url: imageUrl });
      if (articleUrl) params.set("referer", articleUrl);
      const proxy = await apiGet<{ base64: string }>(`/v1/proxy-image?${params.toString()}`, token);
      return proxy?.base64 ?? null;
    } catch {
      return null;
    }
  }

  async function submitActions() {
    if (!selectedId) return;
    setLoading(true);
    setErr("");
    try {
      let image_base64: string | null = null;
      if (approveImage && img?.image_url && typeof img.image_url === "string") {
        const articleUrl = typeof selectedState.url === "string" ? selectedState.url : undefined;
        image_base64 = await fetchImageAsBase64(img.image_url, articleUrl);
      }
      const res = await apiPost<ExecutionStateResponse>(`/v1/executions/${selectedId}/actions`, {
        approve_content: approveContent,
        reject_content: rejectContent,
        approve_image: approveImage,
        reject_image: rejectImage,
        regenerate_twitter: regenTwitter,
        regenerate_linkedin: regenLinkedin,
        edited_twitter: editedTwitter,
        edited_linkedin: editedLinkedin,
        twitter_connection_id: twitterConnectionId ?? undefined,
        linkedin_connection_id: linkedinConnectionId ?? undefined,
        ...(image_base64 ? { image_base64 } : {}),
      }, token);
      setSelected(res);
      await refreshInbox();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  async function deleteConnection(id: number) {
    try {
      await apiDelete(`/v1/connections/${id}`, token);
      refreshConnections();
      if (twitterConnectionId === id) setTwitterConnectionId(null);
      if (linkedinConnectionId === id) setLinkedinConnectionId(null);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function setDefaultConnection(id: number) {
    try {
      await apiPatch(`/v1/connections/${id}`, { is_default: true }, token);
      refreshConnections();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  async function saveLabel(id: number) {
    try {
      await apiPatch(`/v1/connections/${id}`, { label: editingLabelValue }, token);
      setEditingLabelId(null);
      refreshConnections();
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  const twitterConnections = connections.filter((c) => c.provider === "twitter");
  const linkedinConnections = connections.filter((c) => c.provider === "linkedin");

  if (!user) return <div className="muted">Loading...</div>;

  return (
    <div className="layout">
      <div className="sidebar">
        <div className="card" style={{ marginBottom: 14 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span className="muted">{user.email}</span>
            <button type="button" className="secondary" onClick={() => { localStorage.removeItem(TOKEN_KEY); window.location.reload(); }}>Logout</button>
          </div>
        </div>

        <div className="card" style={{ marginBottom: 14 }}>
          <div style={{ fontWeight: 700, marginBottom: 10 }}>New URL</div>
          <div style={{ display: "grid", gap: 10 }}>
            <input value={url} onChange={(e) => setUrl(e.target.value)} type="text" placeholder="https://example.com/article" />
            <button disabled={loading || !url} onClick={createExecution}>Create execution</button>
          </div>
        </div>

        <div className="card" style={{ marginBottom: 14 }}>
          <div style={{ fontWeight: 700, marginBottom: 8 }}>Connections</div>
          <div className="muted" style={{ marginBottom: 8 }}>Add multiple Twitter/LinkedIn accounts; set labels and default.</div>
          {connections.length === 0 ? (
            <div className="muted" style={{ marginBottom: 8 }}>No accounts connected yet.</div>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: "0 0 10px 0" }}>
              {connections.map((c) => (
                <li key={c.id} style={{ marginBottom: 8, padding: "6px 0", borderBottom: "1px solid #eee" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600 }}>{c.provider === "twitter" ? "Twitter" : "LinkedIn"}</span>
                    {editingLabelId === c.id ? (
                      <>
                        <input
                          value={editingLabelValue}
                          onChange={(e) => setEditingLabelValue(e.target.value)}
                          placeholder="Label"
                          style={{ width: 100 }}
                        />
                        <button type="button" onClick={() => saveLabel(c.id)}>Save</button>
                        <button type="button" className="secondary" onClick={() => setEditingLabelId(null)}>Cancel</button>
                      </>
                    ) : (
                      <>
                        <span className="muted">{c.label || c.display_name || c.account_id}</span>
                        {c.is_default && <span style={{ fontSize: 11, color: "#059669" }}>default</span>}
                        <button type="button" className="secondary" style={{ fontSize: 12 }} onClick={() => { setEditingLabelId(c.id); setEditingLabelValue(c.label); }}>Edit label</button>
                        {!c.is_default && <button type="button" className="secondary" style={{ fontSize: 12 }} onClick={() => setDefaultConnection(c.id)}>Set default</button>}
                        <button type="button" className="secondary" style={{ fontSize: 12, color: "#b91c1c" }} onClick={() => deleteConnection(c.id)}>Delete</button>
                      </>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
          <div style={{ display: "flex", gap: 8 }}>
            <button type="button" className="secondary" onClick={() => window.open(`${API_BASE}/v1/oauth/twitter/start?user_id=${encodeURIComponent(userId)}`, "_blank")}>Add Twitter</button>
            <button type="button" className="secondary" onClick={() => window.open(`${API_BASE}/v1/oauth/linkedin/start?user_id=${encodeURIComponent(userId)}`, "_blank")}>Add LinkedIn</button>
          </div>
        </div>

        <div className="topBar">
          <div style={{ fontWeight: 700 }}>Agent Inbox</div>
          <button className="secondary" onClick={() => refreshInbox().catch((e) => setErr(String(e)))}>Refresh</button>
        </div>
        {inbox.map((x) => (
          <div
            key={x.execution_id}
            className={`listItem ${x.execution_id === selectedId ? "listItemActive" : ""}`}
            onClick={() => setSelectedId(x.execution_id)}
          >
            <div style={{ fontWeight: 700 }}>{x.status}</div>
            <div className="muted">{x.updated_at}</div>
            <div className="muted" style={{ marginTop: 6, wordBreak: "break-word" }}>{x.url}</div>
          </div>
        ))}
      </div>

      <div className="main">
        <div className="card">
          <div className="topBar">
            <div>
              <div style={{ fontWeight: 800 }}>Execution</div>
              <div className="muted">{selectedId || "None selected"}</div>
            </div>
            <div className="muted">{selected?.status ?? ""}</div>
          </div>

          {err ? (
            <div className="card" style={{ borderColor: "#fecaca", background: "#fff1f2", marginBottom: 14 }}>
              <div style={{ fontWeight: 800, marginBottom: 6 }}>Error</div>
              <div className="muted" style={{ color: "#7f1d1d", whiteSpace: "pre-wrap" }}>{err}</div>
            </div>
          ) : null}

          {selected ? (
            <>
              <div className="muted" style={{ marginBottom: 10 }}>Source URL: <span style={{ wordBreak: "break-word" }}>{String(selectedState.url)}</span></div>

              {selected.status === "running" ? (
                <div className="card" style={{ marginBottom: 14, borderColor: "#93c5fd", background: "#eff6ff" }}>
                  <div style={{ fontWeight: 800, marginBottom: 6 }}>Execution in Progress</div>
                  <div className="muted">Scraping → Analyzing → Generating posts → Selecting image. This may take 1–3 minutes.</div>
                  <button className="secondary" style={{ marginTop: 10 }} onClick={() => loadExecution(selectedId!)}>Refresh Status</button>
                </div>
              ) : null}

              {selected.status === "terminated" && selectedState.terminate_reason ? (
                <div className="card" style={{ marginBottom: 14, borderColor: "#fecaca", background: "#fff1f2" }}>
                  <div style={{ fontWeight: 800, marginBottom: 6 }}>Execution Terminated</div>
                  <div className="muted" style={{ color: "#7f1d1d" }}>{String(selectedState.terminate_reason)}</div>
                </div>
              ) : null}

              {interrupt && typeof interrupt === "object" && (interrupt as { type?: string }).type === "reauth_required" ? (
                <div className="card" style={{ marginBottom: 14, borderColor: "#fde68a", background: "#fffbeb" }}>
                  <div style={{ fontWeight: 800, marginBottom: 6 }}>Re-auth required</div>
                  <div style={{ display: "flex", gap: 10 }}>
                    <button type="button" className="secondary" onClick={() => window.open(`${API_BASE}/v1/oauth/twitter/start?user_id=${encodeURIComponent(userId)}`, "_blank")}>Connect Twitter</button>
                    <button type="button" className="secondary" onClick={() => window.open(`${API_BASE}/v1/oauth/linkedin/start?user_id=${encodeURIComponent(userId)}`, "_blank")}>Connect LinkedIn</button>
                  </div>
                </div>
              ) : null}

              {(selected.status === "awaiting_human" || selected.status === "awaiting_auth" || selected.status === "completed") ? (
                <div className="row" style={{ marginBottom: 14 }}>
                  <div className="card">
                    <div style={{ fontWeight: 800, marginBottom: 8 }}>Generated Twitter post</div>
                    <textarea value={editedTwitter} onChange={(e) => setEditedTwitter(e.target.value)} placeholder="No draft yet" />
                  </div>
                  <div className="card">
                    <div style={{ fontWeight: 800, marginBottom: 8 }}>Generated LinkedIn post</div>
                    <textarea value={editedLinkedin} onChange={(e) => setEditedLinkedin(e.target.value)} placeholder="No draft yet" />
                  </div>
                </div>
              ) : null}

              {(selected.status === "awaiting_human" || selected.status === "awaiting_auth") ? (
                <div className="row" style={{ marginBottom: 14 }}>
                  <div className="card">
                    <div style={{ fontWeight: 800, marginBottom: 10 }}>Selected image</div>
                    {img?.image_url ? (
                      <>
                        <img className="imgPreview" src={String(img.image_url)} alt="" />
                        <div className="muted" style={{ marginTop: 8, wordBreak: "break-word" }}>{String(img.image_url)}</div>
                      </>
                    ) : (
                      <div className="muted">No image.</div>
                    )}
                  </div>

                  <div className="card">
                    <div style={{ fontWeight: 800, marginBottom: 10 }}>Actions</div>
                    {twitterConnections.length > 0 ? (
                      <div style={{ marginBottom: 10 }}>
                        <label className="muted" style={{ display: "block", marginBottom: 4 }}>Post to Twitter</label>
                        <select
                          value={twitterConnectionId ?? ""}
                          onChange={(e) => setTwitterConnectionId(e.target.value ? Number(e.target.value) : null)}
                        >
                          <option value="">Default</option>
                          {twitterConnections.map((c) => (
                            <option key={c.id} value={c.id}>{c.label || c.display_name}</option>
                          ))}
                        </select>
                      </div>
                    ) : null}
                    {linkedinConnections.length > 0 ? (
                      <div style={{ marginBottom: 10 }}>
                        <label className="muted" style={{ display: "block", marginBottom: 4 }}>Post to LinkedIn</label>
                        <select
                          value={linkedinConnectionId ?? ""}
                          onChange={(e) => setLinkedinConnectionId(e.target.value ? Number(e.target.value) : null)}
                        >
                          <option value="">Default</option>
                          {linkedinConnections.map((c) => (
                            <option key={c.id} value={c.id}>{c.label || c.display_name}</option>
                          ))}
                        </select>
                      </div>
                    ) : null}
                    <div className="checks" style={{ marginBottom: 12 }}>
                      <label className="check"><input type="checkbox" checked={approveContent} onChange={(e) => setApproveContent(e.target.checked)} /> Approve and post</label>
                      <label className="check"><input type="checkbox" checked={rejectContent} onChange={(e) => setRejectContent(e.target.checked)} /> Reject content</label>
                      <label className="check"><input type="checkbox" checked={approveImage} onChange={(e) => setApproveImage(e.target.checked)} /> Approve image</label>
                      <label className="check"><input type="checkbox" checked={rejectImage} onChange={(e) => setRejectImage(e.target.checked)} /> Reject image</label>
                      <label className="check"><input type="checkbox" checked={regenTwitter} onChange={(e) => setRegenTwitter(e.target.checked)} /> Regenerate Twitter</label>
                      <label className="check"><input type="checkbox" checked={regenLinkedin} onChange={(e) => setRegenLinkedin(e.target.checked)} /> Regenerate LinkedIn</label>
                    </div>
                    <div style={{ display: "flex", gap: 10 }}>
                      <button disabled={loading} onClick={submitActions}>Submit actions</button>
                      <button className="secondary" disabled={loading} onClick={() => loadExecution(selectedId!)}>Reload</button>
                    </div>
                  </div>
                </div>
              ) : null}

              <div className="card" style={{ marginTop: 14 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
                  <div style={{ fontWeight: 800 }}>Debug</div>
                  <button className="secondary" onClick={() => setShowDebug(!showDebug)}>{showDebug ? "Hide" : "Show"}</button>
                </div>
                {showDebug ? (
                  <pre style={{ fontSize: 12, overflow: "auto", maxHeight: 400 }}>{JSON.stringify(selectedState, null, 2)}</pre>
                ) : null}
              </div>
            </>
          ) : (
            <div className="muted">Select an execution from the Inbox.</div>
          )}
        </div>
      </div>
    </div>
  );
}

export function App() {
  const [token, setToken] = useState<string | null>(() => getToken());

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("oauth") && params.get("status") === "ok") {
      window.history.replaceState({}, "", window.location.pathname);
      setToken(getToken());
    }
  }, []);

  if (!token) {
    return <AuthView onLogin={(t) => setToken(t)} />;
  }
  return <MainApp token={token} />;
}
