const UI_VERSION = "20260725-agent-docs";

const state = {
  user: null,
  cek: null, // CryptoKey
  cekRawB64: null,
  route: location.pathname,
  lastCreatedToken: null,
};

function b64ToBytes(b64) {
  let s = b64.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}
function bytesToB64(bytes) {
  let s = "";
  bytes.forEach((b) => (s += String.fromCharCode(b)));
  return btoa(s);
}

async function importCek(b64) {
  const raw = b64ToBytes(b64);
  return crypto.subtle.importKey("raw", raw, { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
}

async function encryptBody(obj) {
  const iv = crypto.getRandomValues(new Uint8Array(12));
  const enc = new TextEncoder().encode(JSON.stringify(obj));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: "AES-GCM", iv }, state.cek, enc));
  const tag = ct.slice(ct.length - 16);
  const ciphertext = ct.slice(0, ct.length - 16);
  return { iv: bytesToB64(iv), ciphertext: bytesToB64(ciphertext), tag: bytesToB64(tag) };
}

async function decryptBody(envelope) {
  const iv = b64ToBytes(envelope.iv);
  const ciphertext = b64ToBytes(envelope.ciphertext);
  const tag = b64ToBytes(envelope.tag);
  const combined = new Uint8Array(ciphertext.length + tag.length);
  combined.set(ciphertext);
  combined.set(tag, ciphertext.length);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv }, state.cek, combined);
  return JSON.parse(new TextDecoder().decode(pt));
}

async function api(path, { method = "GET", body, encrypt = false } = {}) {
  const headers = {};
  let payload = body;
  const useEnc = Boolean(encrypt && state.cek);
  if (useEnc && body !== undefined) {
    headers["X-Payload-Encrypted"] = "1";
    headers["Content-Type"] = "application/json";
    payload = await encryptBody(body);
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  if (useEnc) headers["X-Payload-Encrypted"] = "1";

  const res = await fetch(path, {
    method,
    headers,
    credentials: "include",
    body: payload !== undefined ? JSON.stringify(payload) : undefined,
  });
  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!res.ok) {
    const msg = (data && data.detail) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  if (useEnc && data && data.iv && data.ciphertext && data.tag) {
    try {
      return await decryptBody(data);
    } catch (err) {
      // fallback: retry without layer-2 so Admin always works
      if (encrypt) {
        return api(path, { method, body, encrypt: false });
      }
      throw err;
    }
  }
  return data;
}

async function refreshMe() {
  try {
    const me = await api("/api/auth/me");
    state.user = me;
    if (me.cek) {
      state.cekRawB64 = me.cek;
      state.cek = await importCek(me.cek);
    }
  } catch {
    state.user = null;
    state.cek = null;
  }
}

function nav() {
  const u = state.user;
  return `
  <nav class="nav">
    <a href="/" data-link>Home</a>
    <a href="/guide" data-link>Docs</a>
    <a href="/guide/agent">Agents</a>
    <a href="/llms.txt" target="_blank">llms.txt</a>
    <span class="spacer"></span>
    ${
      u
        ? `<span class="meta">${u.email}</span>
           ${u.role === "system_admin" ? '<a class="btn" href="/system-admin" data-link>System admin</a>' : ""}
           <a class="btn" href="/admin" data-link>Admin</a>
           <button class="btn" id="logout">Log out</button>`
        : `<a class="btn" href="/login" data-link>Log in</a>
           <a class="btn primary" href="/register" data-link>Register</a>`
    }
  </nav>`;
}

async function viewHome() {
  const cat = await api("/api/catalog");
  const cards = cat.services
    .map(
      (s, i) => `
    <article class="service" style="animation-delay:${i * 0.06}s">
      <span class="badge">${s.status}</span>
      <h2>${s.name}</h2>
      <p>${s.description}</p>
      <div class="meta">v${s.version} · id: ${s.id}</div>
      <a class="btn" href="/guide/${s.id}">Documentation</a>
    </article>`
    )
    .join("");
  return `
  <div class="shell">
    ${nav()}
    <h1 class="brand">GLC MCP</h1>
    <p class="tagline">Public modular MCP platform. Humans request access in Admin; agents self-register and get a token immediately.</p>
    <div class="panel" id="agent-register">
      <h2>Agent registration</h2>
      <p class="meta">LLM / automation agents: register with <code>account_type=agent</code> → auto-approved services + <code>api_token</code>.</p>
      <div class="row" style="margin:0.75rem 0 1rem">
        <a class="btn primary" href="/guide/agent">Agent guide</a>
        <a class="btn" href="/register?agent=1">Register as agent</a>
        <a class="btn" href="/guide/agent.md">Markdown</a>
        <a class="btn" href="/llms.txt" target="_blank">llms.txt</a>
      </div>
      <pre class="code-block">curl -sS -X POST 'https://mcp.glc-rag.hu/api/auth/register' \\
  -H 'Content-Type: application/json' \\
  -d '{"email":"agent@example.com","password":"choose-a-strong-password","account_type":"agent"}'</pre>
      <p class="meta">Then call <code>POST /mcp</code> with <code>Authorization: Bearer mcp_…</code></p>
    </div>
    <h2 style="margin-top:2rem">Services</h2>
    <div class="grid">${cards || "<p>No listed services.</p>"}</div>
  </div>`;
}

function viewAuth(mode) {
  const title = mode === "login" ? "Log in" : "Register";
  const wantAgent = mode === "register" && new URLSearchParams(location.search).get("agent") === "1";
  const agentBox =
    mode === "register"
      ? `<label class="row" style="gap:0.5rem;align-items:center;margin-top:0.75rem">
           <input type="checkbox" id="as_agent" ${wantAgent ? "checked" : ""} />
           <span>Register as agent (auto-approve listed services + API token)</span>
         </label>
         <p class="meta">Agents: see <a href="/guide/agent">/guide/agent</a> and <a href="/llms.txt" target="_blank">llms.txt</a>.</p>`
      : "";
  return `
  <div class="shell">
    ${nav()}
    <div class="panel" style="max-width:480px">
      <h2>${title}</h2>
      <p class="meta">${mode === "register" ? "Humans need Admin + system-admin approval. Agents get a token immediately." : "Public → Admin or System admin."}</p>
      <label>Email</label>
      <input id="email" type="email" autocomplete="username" />
      <label>Password</label>
      <input id="password" type="password" autocomplete="${mode === "login" ? "current-password" : "new-password"}" />
      ${agentBox}
      <div class="row">
        <button class="btn primary" id="submit">${title}</button>
        <a class="btn" href="/" data-link>Back</a>
      </div>
      <div class="msg" id="msg"></div>
    </div>
  </div>`;
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

async function viewAdmin() {
  if (!state.user) return viewAuth("login");
  const [regs, keysData, catalog] = await Promise.all([
    api("/api/admin/registrations", { encrypt: false }),
    api("/api/admin/api-key", { encrypt: false }),
    api("/api/catalog"),
  ]);
  const options = catalog.services
    .map((s) => `<option value="${s.id}">${escapeHtml(s.name)}</option>`)
    .join("");
  const rows = (regs.registrations || [])
    .map(
      (r) =>
        `<tr><td>${escapeHtml(r.service_id)}</td><td>${escapeHtml(r.status)}</td><td>${escapeHtml(r.updated_at)}</td></tr>`
    )
    .join("");

  const keys = (keysData.keys || []).filter((k) => !k.revoked && k.token);
  const keyRows = keys
    .map((k) => {
      const id = escapeHtml(k.id);
      const token = escapeHtml(k.token);
      const when = escapeHtml(String(k.created_at).replace("T", " ").replace(/\.\d+.*/, ""));
      return `<tr>
        <td>${escapeHtml(k.name || "default")}</td>
        <td><code class="token-line" id="tok-${id}">${token}</code></td>
        <td>${when}</td>
        <td class="row">
          <button type="button" class="btn primary" data-copy-id="tok-${id}">Copy</button>
          <button type="button" class="btn danger" data-revoke="${id}">Revoke</button>
        </td>
      </tr>`;
    })
    .join("");

  return `
  <div class="shell">
    ${nav()}
    <h1 class="brand" style="font-size:2.4rem">Admin</h1>
    <div class="panel">
      <h2>API tokens</h2>
      <div class="row" style="margin-bottom:1rem">
        <button class="btn primary" id="newkey">New token</button>
      </div>
      <div class="msg" id="keymsg"></div>
      <table>
        <thead><tr><th>Name</th><th>Token</th><th>Created</th><th></th></tr></thead>
        <tbody>${
          keyRows ||
          "<tr><td colspan=4>No token yet. Click: New token.</td></tr>"
        }</tbody>
      </table>
    </div>
    <div class="panel">
      <h2>Service registration</h2>
      <label>Service</label>
      <select id="service">${options}</select>
      <div class="row"><button class="btn primary" id="req">Submit request</button></div>
      <div class="msg" id="msg"></div>
      <table><thead><tr><th>Service</th><th>Status</th><th>Updated</th></tr></thead>
      <tbody>${rows || "<tr><td colspan=3>No requests</td></tr>"}</tbody></table>
    </div>
  </div>`;
}

async function viewSysadmin() {
  if (!state.user) return viewAuth("login");
  if (state.user.role !== "system_admin") {
    return `<div class="shell">${nav()}<p class="msg err">System admin only.</p></div>`;
  }
  const [pending, allRegs, users] = await Promise.all([
    api("/api/system-admin/registrations/pending", { encrypt: false }),
    api("/api/system-admin/registrations", { encrypt: false }),
    api("/api/system-admin/users", { encrypt: false }),
  ]);
  const pend = (pending.registrations || [])
    .map(
      (r) => `<tr>
      <td>${escapeHtml(r.email)} <span class="meta">${escapeHtml(r.account_type || "human")}</span></td>
      <td>${escapeHtml(r.service_id)}</td><td>${escapeHtml(r.created_at)}</td>
      <td>
        <button class="btn primary" data-approve="${escapeHtml(r.id)}">Approve</button>
        <button class="btn danger" data-reject="${escapeHtml(r.id)}">Reject</button>
      </td></tr>`
    )
    .join("");
  const approved = (allRegs.registrations || [])
    .filter((r) => r.status === "approved")
    .map(
      (r) => `<tr>
      <td>${escapeHtml(r.email)} <span class="meta">${escapeHtml(r.account_type || "")}</span></td>
      <td>${escapeHtml(r.service_id)}</td>
      <td>${escapeHtml(r.status)}</td>
      <td>
        <button class="btn danger" data-suspend-reg="${escapeHtml(r.id)}">Suspend → pending</button>
      </td></tr>`
    )
    .join("");
  const urows = (users.users || [])
    .map((u) => {
      const id = escapeHtml(u.id);
      const isSys = u.role === "system_admin";
      const suspended = u.status === "suspended";
      const action = isSys
        ? ""
        : suspended
          ? `<button class="btn primary" data-user-status="${id}" data-next="active">Activate</button>`
          : `<button class="btn danger" data-user-status="${id}" data-next="suspended">Suspend</button>`;
      return `<tr>
        <td>${escapeHtml(u.email)}</td>
        <td>${escapeHtml(u.role)}</td>
        <td>${escapeHtml(u.account_type || "human")}</td>
        <td>${escapeHtml(u.status || "active")}</td>
        <td>${escapeHtml(u.org_name)}</td>
        <td>${action}</td>
      </tr>`;
    })
    .join("");
  return `
  <div class="shell">
    ${nav()}
    <h1 class="brand" style="font-size:2.4rem">System admin</h1>
    <div class="panel">
      <h2>Pending requests</h2>
      <table><thead><tr><th>User</th><th>Service</th><th>When</th><th></th></tr></thead>
      <tbody>${pend || "<tr><td colspan=4>No pending requests</td></tr>"}</tbody></table>
      <div class="msg" id="msg"></div>
    </div>
    <div class="panel">
      <h2>Approved access</h2>
      <p class="meta">Suspend sets the registration back to pending (MCP tools hidden until re-approved).</p>
      <table><thead><tr><th>User</th><th>Service</th><th>Status</th><th></th></tr></thead>
      <tbody>${approved || "<tr><td colspan=4>No approved registrations</td></tr>"}</tbody></table>
    </div>
    <div class="panel">
      <h2>Users</h2>
      <p class="meta">Suspend blocks login + MCP tokens and moves approved services to pending.</p>
      <table><thead><tr><th>Email</th><th>Role</th><th>Type</th><th>Status</th><th>Org</th><th></th></tr></thead>
      <tbody>${urows}</tbody></table>
    </div>
  </div>`;
}

async function render() {
  const root = document.getElementById("app");
  const path = location.pathname;
  try {
    let html;
    if (path === "/" || path === "") html = await viewHome();
    else if (path === "/login") html = viewAuth("login");
    else if (path === "/register") html = viewAuth("register");
    else if (path.startsWith("/admin")) html = await viewAdmin();
    else if (path.startsWith("/system-admin")) html = await viewSysadmin();
    else if (path.startsWith("/guide")) {
      location.href = path;
      return;
    } else html = await viewHome();

    root.innerHTML = html;
    bind(path);
  } catch (err) {
    root.innerHTML = `<div class="shell">${nav()}<div class="panel"><p class="msg err">Error: ${escapeHtml(err.message || err)}</p><p class="meta">UI ${UI_VERSION}</p></div></div>`;
  }
}

function bind(path) {
  document.querySelectorAll("[data-link]").forEach((a) => {
    a.addEventListener("click", (e) => {
      e.preventDefault();
      history.pushState({}, "", a.getAttribute("href"));
      render();
    });
  });
  const logout = document.getElementById("logout");
  if (logout) {
    logout.onclick = async () => {
      await api("/api/auth/logout", { method: "POST" });
      state.user = null;
      state.cek = null;
      history.pushState({}, "", "/");
      render();
    };
  }

  if (path === "/login" || path === "/register") {
    document.getElementById("submit").onclick = async () => {
      const email = document.getElementById("email").value;
      const password = document.getElementById("password").value;
      const msg = document.getElementById("msg");
      try {
        const endpoint = path === "/login" ? "/api/auth/login" : "/api/auth/register";
        const body = { email, password };
        if (path === "/register") {
          const asAgent = document.getElementById("as_agent");
          body.account_type = asAgent && asAgent.checked ? "agent" : "human";
        }
        const data = await api(endpoint, { method: "POST", body });
        state.user = data.user;
        state.cekRawB64 = data.cek;
        state.cek = await importCek(data.cek);
        if (data.api_token) {
          msg.className = "msg";
          msg.textContent = `Agent registered. API token created (${(data.approved_services || []).join(", ") || "no services"}).`;
        }
        history.pushState({}, "", data.redirect || (data.user.role === "system_admin" ? "/system-admin" : "/admin"));
        render();
      } catch (err) {
        msg.className = "msg err";
        msg.textContent = err.message;
      }
    };
  }

  if (path.startsWith("/admin")) {
    const req = document.getElementById("req");
    if (req) {
      req.onclick = async () => {
        const service_id = document.getElementById("service").value;
        const msg = document.getElementById("msg");
        try {
          await api("/api/admin/registrations", {
            method: "POST",
            body: { service_id },
            encrypt: Boolean(state.cek),
          });
          msg.className = "msg";
          msg.textContent = "Request submitted (pending).";
          render();
        } catch (err) {
          msg.className = "msg err";
          msg.textContent = err.message;
        }
      };
    }
    const newkey = document.getElementById("newkey");
    if (newkey) {
      newkey.onclick = async () => {
        const msg = document.getElementById("keymsg");
        try {
          const created = await api("/api/admin/api-key", {
            method: "POST",
            body: { name: "default" },
            encrypt: false,
          });
          if (!created.token || created.token.length < 20) {
            throw new Error("Server did not return a token");
          }
          state.lastCreatedToken = null;
          msg.className = "msg";
          msg.textContent = "Token created.";
          render();
        } catch (err) {
          msg.className = "msg err";
          msg.textContent = err.message;
        }
      };
    }
    document.querySelectorAll("[data-copy-id]").forEach((btn) => {
      btn.onclick = async () => {
        const el = document.getElementById(btn.dataset.copyId);
        const text = el ? el.textContent : "";
        try {
          await navigator.clipboard.writeText(text);
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1500);
        } catch {
          if (el) {
            const range = document.createRange();
            range.selectNodeContents(el);
            const sel = window.getSelection();
            sel.removeAllRanges();
            sel.addRange(range);
          }
          btn.textContent = "Selected — Ctrl+C";
        }
      };
    });
    document.querySelectorAll("[data-revoke]").forEach((btn) => {
      btn.onclick = async () => {
        await api("/api/admin/api-key", {
          method: "DELETE",
          body: { id: btn.dataset.revoke },
          encrypt: Boolean(state.cek),
        });
        render();
      };
    });
  }

  if (path.startsWith("/system-admin")) {
    document.querySelectorAll("[data-approve]").forEach((btn) => {
      btn.onclick = async () => {
        await api(`/api/system-admin/registrations/${btn.dataset.approve}`, {
          method: "POST",
          body: { status: "approved" },
          encrypt: Boolean(state.cek),
        });
        render();
      };
    });
    document.querySelectorAll("[data-reject]").forEach((btn) => {
      btn.onclick = async () => {
        await api(`/api/system-admin/registrations/${btn.dataset.reject}`, {
          method: "POST",
          body: { status: "rejected" },
          encrypt: Boolean(state.cek),
        });
        render();
      };
    });
    document.querySelectorAll("[data-suspend-reg]").forEach((btn) => {
      btn.onclick = async () => {
        await api(`/api/system-admin/registrations/${btn.dataset.suspendReg}`, {
          method: "POST",
          body: { status: "pending" },
          encrypt: Boolean(state.cek),
        });
        render();
      };
    });
    document.querySelectorAll("[data-user-status]").forEach((btn) => {
      btn.onclick = async () => {
        await api(`/api/system-admin/users/${btn.dataset.userStatus}/status`, {
          method: "POST",
          body: { status: btn.dataset.next },
          encrypt: Boolean(state.cek),
        });
        render();
      };
    });
  }
}

window.addEventListener("popstate", render);
await refreshMe();
await render();
