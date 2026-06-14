/**
 * Benni Media Apply — Executor-Panel (Vanilla Web Component, kein Build-Step).
 *
 * Konsumiert NUR den WS-Contract benni_media_apply/get_status und rendert den
 * Apply-Plan (Soll) gegen den Geräte-Ist, Ramp-Fortschritt, Gate-Breakdown,
 * Denon-Nachlauf und ein Apply-Log. Automatik-Toggle (Shadow↔Live) entschärft
 * in der Kopfzeile. Look: Dracula-ish Dark wie die anderen Panels.
 */

const ACTION_LABEL = {
  none: "—",
  pause_homepods: "HomePods pausieren",
  resume_homepods: "HomePods fortsetzen",
  start_radio: "Radio starten",
};

const pct = (v) => (v == null ? "—" : Math.round(Number(v) * 100) + "%");
const yn = (v) => (v == null ? "—" : v ? "ja" : "nein");
const fmtTs = (s) => {
  try { return new Date(s).toLocaleTimeString("de-DE", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); }
  catch { return s || ""; }
};

const css = `
:host { display:block; font-family: ui-sans-serif, system-ui, sans-serif;
  background:#1a1b26; color:#c0caf5; min-height:100vh; padding:18px 22px; box-sizing:border-box; }
h1 { font-size:18px; margin:0 0 2px; color:#bb9af7; }
.subrow { display:flex; align-items:center; justify-content:space-between; gap:12px; margin-bottom:14px; }
.sub { color:#565f89; font-size:12px; }
.banner { background:#3a2d33; border:1px solid #f7768e55; color:#f7768e; border-radius:10px;
  padding:9px 14px; font-size:13px; margin-bottom:14px; }
.banner.live { background:#2d3a2e; border-color:#9ece6a55; color:#9ece6a; }
.grid { display:grid; gap:14px; }
.cols { grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); }
.card { background:#1f2335; border:1px solid #2a2e42; border-radius:12px; padding:14px 16px; }
.card h2 { font-size:13px; margin:0 0 10px; color:#7aa2f7; text-transform:uppercase; letter-spacing:.04em; }
.kpi { font-size:22px; font-weight:600; color:#7dcfff; }
.kpi.act { color:#bb9af7; }
.row { display:flex; justify-content:space-between; align-items:baseline; gap:12px; padding:4px 0; font-size:13px; border-bottom:1px solid #20243450; }
.row .k { color:#787c99; } .row .v { color:#c0caf5; }
.row .v.soll { color:#7dcfff; }
.badges { display:flex; flex-wrap:wrap; gap:8px; margin:10px 0 16px; }
.badge { font-size:12px; padding:4px 10px; border-radius:999px; background:#24283b; border:1px solid #2a2e42; }
.badge.on { background:#2d3a2e; border-color:#9ece6a55; color:#9ece6a; }
.badge.off { background:#3a2d33; border-color:#f7768e55; color:#f7768e; }
.badge.neutral { color:#7dcfff; }
button.tiny { padding:4px 12px; font-size:11px; border-radius:999px; cursor:pointer;
  background:#24283b; color:#c0caf5; border:1px solid #2a2e42; }
button.tiny:hover { border-color:#7aa2f7; }
button.tiny.on { background:#2d3a2e; border-color:#9ece6a55; color:#9ece6a; }
button.tiny.off { background:#3a2d33; border-color:#f7768e55; color:#f7768e; }
.ramp { height:6px; background:#24283b; border-radius:4px; overflow:hidden; margin-top:6px; }
.ramp > i { display:block; height:100%; background:#7aa2f7; transition:width .3s; }
.dev { display:flex; align-items:center; justify-content:space-between; gap:10px; padding:6px 0; border-bottom:1px solid #20243450; font-size:13px; }
.dev .nm { color:#c0caf5; } .dev .istsoll { color:#787c99; } .dev .istsoll b { color:#7dcfff; }
.gate { display:flex; align-items:center; gap:8px; font-size:13px; padding:3px 0; }
.gate .dot { width:9px;height:9px;border-radius:50%;background:#414868; } .gate .dot.on{background:#9ece6a;} .gate .dot.off{background:#f7768e;}
.log { font-size:12px; }
.log .e { display:flex; gap:10px; padding:3px 0; border-bottom:1px solid #20243450; }
.log .ts { color:#565f89; width:64px; flex:0 0 auto; }
.log .msg { color:#a9b1d6; flex:1; }
.log .x { color:#9ece6a; } .log .x.sh { color:#565f89; }
.mut { color:#565f89; font-size:11px; margin-top:6px; }
.err { color:#f7768e; padding:20px; }
pre { background:#16161e; border-radius:10px; padding:12px; overflow:auto; font-size:12px; color:#a9b1d6; margin:0; }
`;

class BmaApp extends HTMLElement {
  set hass(h) { this._hass = h; if (!this._timer) this._tick(); }

  connectedCallback() {
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `<style>${css}</style><div id="root" class="err">Lade…</div>`;
    this._timer = setInterval(() => this._tick(), 3000);
  }
  disconnectedCallback() { clearInterval(this._timer); this._timer = null; }

  async _tick() {
    if (!this._hass) return;
    try {
      this._status = await this._hass.callWS({ type: "benni_media_apply/get_status" });
      this._render();
    } catch (e) {
      this.shadowRoot.getElementById("root").innerHTML =
        `<div class="err">Media Apply nicht geladen oder keine Berechtigung.<br>${e.message || e}</div>`;
    }
  }

  async _call(type, extra = {}) {
    try { this._status = await this._hass.callWS({ type, ...extra }); this._render(); }
    catch (e) { console.error(e); }
  }

  _badge(label, on) {
    return `<span class="badge ${on ? "on" : "off"}">${label}: ${on ? "an" : "aus"}</span>`;
  }

  _render() {
    const s = this._status; if (!s) return;
    const plan = s.plan || {}, pol = s.policy || {}, dev = s.devices || {},
          gate = s.gates || {}, nl = s.nachlauf || {}, set = s.settings || {};
    const live = !!s.execute;
    const hp = dev.homepods || {}, dn = dev.denon || {}, sub = dev.subwoofer || {};

    const rampPct = s.ramp_total ? Math.round((s.ramp_step / s.ramp_total) * 100) : 0;
    const rampLine = s.ramp_active
      ? `<div class="mut">Ramp ${s.ramp_step}/${s.ramp_total} (${set.ramp_step_delay_s ?? "?"}s/Schritt)</div><div class="ramp"><i style="width:${rampPct}%"></i></div>`
      : "";

    const gates = [
      ["apply_enabled (Automatik)", gate.apply_enabled],
      ["volume_apply_allowed (policy)", gate.volume_apply_allowed],
      ["stop_latch (blockiert wenn an)", !gate.stop_latch],
      ["→ execute (fährt wirklich)", gate.execute],
    ].map(([k, on]) => `<div class="gate"><span class="dot ${on ? "on" : "off"}"></span><span>${k}: <b>${yn(on)}</b></span></div>`).join("");

    const device = (name, istV, sollV, extra = "") =>
      `<div class="dev"><span class="nm">${name}</span><span class="istsoll">${istV} → <b>${sollV}</b>${extra ? " · " + extra : ""}</span></div>`;

    const devices =
      device("HomePods", pct(hp.volume), pct(plan.homepods_target),
             `${hp.state ?? "—"}${plan.homepods_ramp ? " · Ramp" : (plan.homepods_target != null ? " · direkt" : "")}`) +
      device("Denon", pct(dn.volume), pct(plan.denon_target), `${dn.state ?? "—"}${dn.power_on === false ? " · aus" : ""}`) +
      device("Subwoofer", sub.state ?? "—", plan.subwoofer_set == null ? "—" : (plan.subwoofer_set ? "an" : "aus"),
             pol.subwoofer_allowed ? "erlaubt" : "gesperrt");

    const log = (s.log || []).length
      ? (s.log || []).map((e) => {
          const bits = [];
          if (e.action && e.action !== "none") bits.push(ACTION_LABEL[e.action] || e.action);
          if (e.homepods_target != null) bits.push("HP→" + pct(e.homepods_target));
          if (e.denon_target != null) bits.push("Denon→" + pct(e.denon_target));
          if (e.subwoofer_set != null) bits.push("Sub→" + (e.subwoofer_set ? "an" : "aus"));
          if (e.quiet) bits.push("Quiet");
          return `<div class="e"><span class="ts">${fmtTs(e.ts)}</span><span class="msg">${bits.join(", ") || "—"}</span><span class="x ${e.executed ? "" : "sh"}">${e.executed ? "live" : "shadow"}</span></div>`;
        }).join("")
      : `<div class="mut">Noch keine Apply-Entscheidungen aufgezeichnet.</div>`;

    this.shadowRoot.getElementById("root").outerHTML = `<div id="root">
      <h1>Media Apply · ${s.profile || ""}</h1>
      <div class="subrow">
        <div class="sub">Audio-Executor — ${live ? "Live (fährt Geräte)" : "Shadow (plant nur)"}</div>
        <button class="tiny ${s.apply_enabled ? "on" : "off"}" id="toggle">Automatik: ${s.apply_enabled ? "an" : "aus"}</button>
      </div>

      <div class="banner ${live ? "live" : ""}">
        ${live
          ? "Automatik aktiv — der Executor schaltet HomePods/Denon/Subwoofer real."
          : "Shadow-Modus — der Plan wird nur berechnet, NICHTS wird geschaltet. „Automatik: an" zum Scharfschalten."}
      </div>

      <div class="grid cols">
        <div class="card"><h2>Aktive Action</h2><div class="kpi act">${ACTION_LABEL[plan.homepods_action] || plan.homepods_action || "—"}</div></div>
        <div class="card"><h2>HomePods Soll</h2><div class="kpi">${pct(plan.homepods_target)}</div>${rampLine}</div>
        <div class="card"><h2>Denon Soll</h2><div class="kpi">${pct(plan.denon_target)}</div></div>
        <div class="card"><h2>Subwoofer Soll</h2><div class="kpi">${plan.subwoofer_set == null ? "—" : (plan.subwoofer_set ? "AN" : "AUS")}</div></div>
      </div>

      <div class="badges">
        ${this._badge("Apply aktiv", s.apply_enabled)}
        ${this._badge("Volume erlaubt", gate.volume_apply_allowed)}
        ${this._badge("Ramp läuft", s.ramp_active)}
        ${this._badge("Quiet/Ducking", pol.quiet_mode)}
        ${this._badge("Denon-Nachlauf", nl.active)}
        ${gate.stop_latch ? `<span class="badge off">Stop-Latch: an</span>` : `<span class="badge on">Stop-Latch: aus</span>`}
      </div>

      <div class="grid" style="grid-template-columns: 1fr 1fr;">
        <div class="card">
          <h2>Ist → Soll (Geräte)</h2>
          ${devices}
        </div>
        <div class="card">
          <h2>Gate-Breakdown</h2>
          ${gates}
          <div class="mut">Quiet-Override (sofort 0.10, kein Ramp): ${yn(plan.quiet_override)}</div>
        </div>
      </div>

      <div class="grid" style="grid-template-columns: 1fr 1fr; margin-top:14px;">
        <div class="card">
          <h2>Denon-Nachlauf (R13/R14)</h2>
          <div class="row"><span class="k">PC armed</span><span class="v">${yn(nl.pc_armed)} (PC ${nl.pc_power_on == null ? "—" : yn(nl.pc_power_on)})</span></div>
          <div class="row"><span class="k">TV armed</span><span class="v">${yn(nl.tv_armed)}${nl.tv_paused ? " · pausiert (Sleep)" : ""} (TV ${nl.tv_power_on == null ? "—" : yn(nl.tv_power_on)})</span></div>
          <div class="row"><span class="k">Denon Power</span><span class="v">${dn.power_on == null ? "—" : yn(dn.power_on)}</span></div>
          <div class="row"><span class="k">Bio sleep</span><span class="v">${nl.bio_sleep == null ? "—" : yn(nl.bio_sleep)}</span></div>
        </div>
        <div class="card">
          <h2>Apply-Log</h2>
          <div class="log">${log}</div>
        </div>
      </div>

      <div class="mut">Ramp: ${set.ramp_steps ?? "?"}×${set.ramp_step_delay_s ?? "?"}s · Tiny-Δ ${set.tiny_delta ?? "?"} · Ducked ${set.ducked_level ?? "?"}</div>
    </div>`;

    const $ = (id) => this.shadowRoot.getElementById(id);
    $("toggle").onclick = () => this._call("benni_media_apply/set_apply_enabled", { enabled: !s.apply_enabled });
  }
}

customElements.define("bma-app", BmaApp);
