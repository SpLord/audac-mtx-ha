/*
 * AUDAC mixer card — one channel strip per output, like the AUDAC app.
 *
 * Served by the audac_mtx integration (no Lovelace resource needed):
 *   type: custom:audac-mixer-card
 *   entry_id: <optional, first AUDAC device otherwise>
 *   zones: [1, 2, 3]   # optional subset
 *   height: 600        # optional, px
 *
 * Layout and VU come over the websocket (audac_mtx/get_layout, /subscribe_vu);
 * volume, mute, source and tone go through the normal entity services.
 * Saving settings on the device is deliberately not offered here.
 */
const CARD_VERSION = (() => {
  try {
    return new URL(import.meta.url).searchParams.get("v") || "dev";
  } catch (e) {
    return "dev";
  }
})();

const SEND_INTERVAL_MS = 200; // max. one volume command per strip while dragging
const HOLD_AFTER_RELEASE_MS = 1200; // keep the local value until the device echo arrives
// Fader scale like a real desk: 0…−60 dB on the upper 85 % of the travel,
// the rest down to the minimum below. Linear over 100 dB made a 1 cm swipe +25 dB.
const KNEE_DB = -60;
const KNEE_POS = 0.15;

function dbToPos(db, volMax) {
  if (db === null || db <= -volMax) return 0;
  if (volMax <= -KNEE_DB) return (db + volMax) / volMax;
  if (db >= KNEE_DB) return KNEE_POS + ((db - KNEE_DB) / -KNEE_DB) * (1 - KNEE_POS);
  return ((db + volMax) / (volMax + KNEE_DB)) * KNEE_POS;
}

function posToDb(pos, volMax) {
  pos = Math.max(0, Math.min(1, pos));
  if (volMax <= -KNEE_DB) return Math.round(pos * volMax - volMax);
  if (pos >= KNEE_POS) return Math.round(KNEE_DB + ((pos - KNEE_POS) / (1 - KNEE_POS)) * -KNEE_DB);
  return Math.round((pos / KNEE_POS) * (volMax + KNEE_DB) - volMax);
}

const ICON_ON =
  "M14,3.23V5.29C16.89,6.15 19,8.83 19,12C19,15.17 16.89,17.84 14,18.7V20.77C18,19.86 21,16.28 21,12C21,7.72 18,4.14 14,3.23M16.5,12C16.5,10.23 15.5,8.71 14,7.97V16C15.5,15.29 16.5,13.76 16.5,12M3,9V15H7L12,20V4L7,9H3Z";
const ICON_OFF =
  "M12,4L9.91,6.09L12,8.18M4.27,3L3,4.27L7.73,9H3V15H7L12,20V13.27L16.25,17.53C15.58,18.04 14.83,18.46 14,18.7V20.77C15.38,20.45 16.63,19.82 17.68,18.96L19.73,21L21,19.73L12,10.73M19,12C19,12.94 18.8,13.82 18.46,14.64L19.97,16.15C20.62,14.91 21,13.5 21,12C21,7.72 18,4.14 14,3.23V5.29C16.89,6.15 19,8.83 19,12M16.5,12C16.5,10.23 15.5,8.71 14,7.97V10.18L16.45,12.63C16.5,12.43 16.5,12.21 16.5,12Z";

const STYLE = `
  :host { display: block; }
  .card {
    display: flex; flex-direction: column; box-sizing: border-box;
    padding: 8px 8px 6px; height: var(--mixer-height, 600px);
    background: var(--ha-card-background, var(--card-background-color, #1c2230));
    border-radius: var(--ha-card-border-radius, 12px);
    color: var(--primary-text-color, #e8ecf2);
  }
  .head { display: flex; align-items: baseline; gap: 8px; padding: 0 4px 6px; }
  .title { font-size: 15px; font-weight: 600; flex: 1; }
  .ver { font-size: 10px; color: var(--secondary-text-color, #8a94a6); }
  .msg { padding: 16px; color: var(--secondary-text-color, #8a94a6); }
  .strips { display: flex; gap: 6px; flex: 1; min-height: 0; overflow-x: auto; }
  .strip {
    flex: 1 1 0; min-width: 76px; display: flex; flex-direction: column; gap: 4px;
    background: rgba(127, 140, 160, 0.10); border-radius: 8px; padding: 6px 4px;
    position: relative; user-select: none; -webkit-user-select: none;
  }
  .strip.unavailable { opacity: 0.4; pointer-events: none; }
  button { font: inherit; color: inherit; }
  .name {
    all: unset; cursor: pointer; text-align: center; font-size: 13px; font-weight: 600;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding: 2px 0;
    border-radius: 4px;
  }
  .name.open { background: rgba(127, 140, 160, 0.25); }
  select {
    width: 100%; font: inherit; font-size: 12px; padding: 3px 2px; border-radius: 5px;
    color: inherit; background: rgba(0, 0, 0, 0.25); border: 1px solid rgba(127, 140, 160, 0.3);
  }
  .db { text-align: center; font-size: 13px; font-variant-numeric: tabular-nums; }
  .db.muted { color: var(--error-color, #e5534b); }
  .step {
    all: unset; cursor: pointer; text-align: center; font-size: 11px; padding: 4px 0;
    border-radius: 5px; background: rgba(127, 140, 160, 0.16);
  }
  .step:active { background: rgba(127, 140, 160, 0.35); }
  .body { flex: 1; min-height: 80px; display: flex; gap: 4px; justify-content: center; position: relative; }
  .meter { width: 7px; position: relative; border-radius: 2px; overflow: hidden;
    background: linear-gradient(to top, #2fbf5b 0%, #2fbf5b 70%, #e2c044 85%, #e5534b 100%); }
  .meter .cover { position: absolute; inset: 0; background: rgba(10, 14, 20, 0.85);
    transform-origin: top; transform: scaleY(1); transition: transform 90ms linear; }
  .meter::after { content: ""; position: absolute; inset: 0;
    background: repeating-linear-gradient(to top, transparent 0 3px, rgba(10, 14, 20, 0.9) 3px 4px); }
  .track { width: 34px; position: relative; touch-action: none; cursor: ns-resize; }
  .track::before { content: ""; position: absolute; left: 50%; top: 10px; bottom: 10px; width: 4px;
    transform: translateX(-50%); background: rgba(0, 0, 0, 0.55); border-radius: 2px; }
  .cap { position: absolute; left: 3px; right: 3px; bottom: 0; height: 20px; border-radius: 4px;
    background: linear-gradient(#dfe5ee, #9aa5b5); box-shadow: 0 2px 4px rgba(0, 0, 0, 0.5); }
  .cap::after { content: ""; position: absolute; left: 4px; right: 4px; top: 9px; height: 2px; background: #3a4352; }
  .track.dragging .cap { background: linear-gradient(#ffffff, #b8c3d4); }
  .mute { all: unset; cursor: pointer; display: flex; justify-content: center; padding: 6px 0;
    border-radius: 6px; background: rgba(127, 140, 160, 0.16); }
  .mute svg { width: 22px; height: 22px; fill: var(--primary-color, #3fa9f5); }
  .mute.on { background: rgba(229, 83, 75, 0.18); }
  .mute.on svg { fill: var(--error-color, #e5534b); }
  .tone { position: absolute; inset: 0; display: none; flex-direction: column; gap: 10px;
    justify-content: center; padding: 6px 2px; border-radius: 6px;
    background: var(--ha-card-background, var(--card-background-color, #1c2230)); z-index: 2; }
  .tone.open { display: flex; }
  .tone label { font-size: 11px; display: flex; justify-content: space-between; }
  .tone input { width: 100%; }
  .tone .reset { all: unset; cursor: pointer; text-align: center; font-size: 11px; padding: 4px 0;
    border-radius: 5px; background: rgba(127, 140, 160, 0.16); }
`;

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function svgIcon(path) {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  const p = document.createElementNS(ns, "path");
  p.setAttribute("d", path);
  s.appendChild(p);
  return { svg: s, path: p };
}

function fmtDb(db) {
  if (db === null || db === undefined || Number.isNaN(db)) return "−∞";
  const r = Math.round(db);
  return `${r > 0 ? "+" : r < 0 ? "−" : ""}${Math.abs(r)} dB`;
}

class AudacMixerCard extends HTMLElement {
  constructor() {
    super();
    this._config = {};
    this._hass = null;
    this._layout = null;
    this._loading = false;
    this._error = null;
    this._strips = new Map(); // zone → strip refs
    this._vuUnsub = null;
    this.attachShadow({ mode: "open" });
  }

  setConfig(config) {
    this._config = { ...(config || {}) };
    this._layout = null;
    this._error = null;
    this._stopVu();
    this._renderShell();
    if (this._hass) this._load();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (first || (!this._layout && !this._loading && !this._error)) this._load();
    this._update();
  }

  connectedCallback() {
    if (this._layout && !this._vuUnsub) this._startVu();
  }

  disconnectedCallback() {
    this._stopVu();
  }

  getCardSize() {
    return 9;
  }

  getGridOptions() {
    return { columns: "full", min_columns: 6, rows: 8, min_rows: 6 };
  }

  static getStubConfig() {
    return {};
  }

  // ------------------------------------------------------------ data
  async _load() {
    if (!this._hass || this._loading) return;
    this._loading = true;
    const msg = { type: "audac_mtx/get_layout" };
    if (this._config.entry_id) msg.entry_id = this._config.entry_id;
    try {
      this._layout = await this._hass.connection.sendMessagePromise(msg);
      this._error = null;
      this._buildStrips();
      this._update();
      if (this.isConnected) this._startVu();
    } catch (err) {
      this._error = (err && err.message) || "AUDAC integration not found";
      this._renderShell();
    } finally {
      this._loading = false;
    }
  }

  async _startVu() {
    if (!this._layout || !this._layout.vu || !this._hass || this._vuUnsub) return;
    const msg = { type: "audac_mtx/subscribe_vu" };
    if (this._layout.entry_id) msg.entry_id = this._layout.entry_id;
    this._vuUnsub = () => {}; // placeholder so a second call doesn't double-subscribe
    try {
      this._vuUnsub = await this._hass.connection.subscribeMessage((ev) => this._onVu(ev.vu || {}), msg);
    } catch (err) {
      this._vuUnsub = null; // meters stay dark, controls keep working
    }
  }

  _stopVu() {
    if (this._vuUnsub) {
      try {
        this._vuUnsub();
      } catch (e) {
        /* connection already gone */
      }
      this._vuUnsub = null;
    }
  }

  _onVu(vu) {
    const max = (this._layout && this._layout.vu_max) || 40;
    for (const [zone, s] of this._strips) {
      const lr = vu[zone] || vu[String(zone)] || [0, 0];
      s.meters.forEach((m, i) => {
        const v = Math.max(0, Math.min(1, (lr[i] || 0) / max));
        m.style.transform = `scaleY(${1 - v})`;
      });
    }
  }

  _call(domain, service, data) {
    return this._hass.callService(domain, service, data).catch((err) => {
      this.dispatchEvent(
        new CustomEvent("hass-notification", {
          detail: { message: `AUDAC: ${(err && err.message) || err}` },
          bubbles: true,
          composed: true,
        })
      );
    });
  }

  // ------------------------------------------------------------ render
  _renderShell() {
    const root = this.shadowRoot;
    const style = el("style");
    style.textContent = STYLE;
    const card = el("div", "card");
    if (this._config.height) card.style.setProperty("--mixer-height", `${Number(this._config.height)}px`);
    const head = el("div", "head");
    this._titleEl = el("div", "title", this._config.title || "AUDAC");
    head.append(this._titleEl, el("div", "ver", `v${CARD_VERSION}`));
    this._stripsEl = el("div", "strips");
    if (this._error) this._stripsEl.appendChild(el("div", "msg", this._error));
    else if (!this._layout) this._stripsEl.appendChild(el("div", "msg", "Loading…"));
    card.append(head, this._stripsEl);
    root.replaceChildren(style, card);
    this._strips.clear();
  }

  _buildStrips() {
    this._renderShell();
    this._stripsEl.replaceChildren();
    if (!this._config.title && this._layout.title) this._titleEl.textContent = this._layout.title;
    const only = Array.isArray(this._config.zones) ? this._config.zones.map(Number) : null;
    for (const ch of this._layout.channels) {
      if (only && !only.includes(ch.zone)) continue;
      if (!ch.media_player) continue;
      const s = this._buildStrip(ch);
      this._strips.set(ch.zone, s);
      this._stripsEl.appendChild(s.root);
    }
  }

  _buildStrip(ch) {
    const s = { ch, drag: null, local: null, holdUntil: 0, lastSent: 0, sendTimer: null, sourceKey: null };
    const root = el("div", "strip");
    const name = el("button", "name", ch.name);
    name.title = "Bass / Treble";
    const select = el("select");
    const db = el("div", "db", "");
    const up = el("button", "step", "▲");
    const body = el("div", "body");
    const meterL = el("div", "meter");
    const meterR = el("div", "meter");
    const coverL = el("div", "cover");
    const coverR = el("div", "cover");
    meterL.appendChild(coverL);
    meterR.appendChild(coverR);
    const track = el("div", "track");
    const cap = el("div", "cap");
    track.appendChild(cap);
    if (this._layout.vu) body.appendChild(meterL);
    body.appendChild(track);
    if (this._layout.vu) body.appendChild(meterR);
    const tone = this._buildTone(ch);
    body.appendChild(tone.root);
    const down = el("button", "step", "▼");
    const mute = el("button", "mute");
    const icon = svgIcon(ICON_ON);
    mute.appendChild(icon.svg);

    root.append(name, select, db, up, body, down, mute);
    Object.assign(s, { root, name, select, db, track, cap, mute, icon, tone, meters: [coverL, coverR] });

    name.addEventListener("click", () => {
      const open = !tone.root.classList.contains("open");
      tone.root.classList.toggle("open", open);
      name.classList.toggle("open", open);
    });
    select.addEventListener("change", () =>
      this._call("media_player", "select_source", { entity_id: ch.media_player, source: select.value })
    );
    up.addEventListener("click", () => this._call("media_player", "volume_up", { entity_id: ch.media_player }));
    down.addEventListener("click", () => this._call("media_player", "volume_down", { entity_id: ch.media_player }));
    mute.addEventListener("click", () => {
      const st = this._state(ch.media_player);
      const muted = !!(st && st.attributes.is_volume_muted);
      this._call("media_player", "volume_mute", { entity_id: ch.media_player, is_volume_muted: !muted });
    });
    this._wireFader(s);
    return s;
  }

  _buildTone(ch) {
    const root = el("div", "tone");
    const rows = {};
    for (const kind of ["bass", "treble"]) {
      const entity = ch[kind];
      if (!entity) continue;
      const label = el("label");
      const value = el("span", "", "");
      label.append(el("span", "", kind === "bass" ? "Bass" : "Treble"), value);
      const input = el("input");
      input.type = "range";
      input.addEventListener("input", () => (value.textContent = fmtDb(Number(input.value))));
      input.addEventListener("change", () =>
        this._call("number", "set_value", { entity_id: entity, value: Number(input.value) })
      );
      root.append(label, input);
      rows[kind] = { entity, input, value };
    }
    const reset = el("button", "reset", "0 dB");
    reset.addEventListener("click", () => {
      for (const r of Object.values(rows)) this._call("number", "set_value", { entity_id: r.entity, value: 0 });
    });
    root.appendChild(reset);
    return { root, rows };
  }

  // Relative drag: touching the fader never jumps the volume, only moving does.
  _wireFader(s) {
    const volMax = this._layout.vol_max || 100;
    s.track.addEventListener("pointerdown", (ev) => {
      const st = this._state(s.ch.media_player);
      if (!st || st.state === "unavailable") return;
      const startPos = dbToPos(this._currentDb(s, st), volMax);
      s.drag = { y: ev.clientY, startPos, h: s.track.clientHeight - 20 || 1 };
      if (s.track.setPointerCapture) s.track.setPointerCapture(ev.pointerId);
      s.track.classList.add("dragging");
      ev.preventDefault();
    });
    s.track.addEventListener("pointermove", (ev) => {
      if (!s.drag) return;
      // the cap follows the finger 1:1; the scale decides the dB value
      s.local = posToDb(s.drag.startPos + (s.drag.y - ev.clientY) / s.drag.h, volMax);
      this._paintVolume(s, s.local);
      this._scheduleSend(s, false);
    });
    const end = () => {
      if (!s.drag) return;
      const moved = s.local !== null;
      s.drag = null;
      s.track.classList.remove("dragging");
      if (moved) this._scheduleSend(s, true);
    };
    s.track.addEventListener("pointerup", end);
    s.track.addEventListener("pointercancel", end);
  }

  _currentDb(s, st) {
    if (s.local !== null) return s.local;
    const volMax = this._layout.vol_max || 100;
    const a = st.attributes;
    if (a.volume_db === null) return -volMax;
    if (typeof a.volume_db === "number") return a.volume_db;
    return Math.round((a.volume_level ?? 0) * volMax - volMax);
  }

  _scheduleSend(s, final) {
    const send = () => {
      s.sendTimer = null;
      s.lastSent = Date.now();
      if (s.local === null) return;
      const volMax = this._layout.vol_max || 100;
      const level = Math.max(0, Math.min(1, (s.local + volMax) / volMax));
      s.holdUntil = Date.now() + HOLD_AFTER_RELEASE_MS;
      this._call("media_player", "volume_set", { entity_id: s.ch.media_player, volume_level: level });
    };
    if (final) {
      clearTimeout(s.sendTimer);
      send();
      return;
    }
    if (s.sendTimer) return;
    const wait = Math.max(0, SEND_INTERVAL_MS - (Date.now() - s.lastSent));
    s.sendTimer = setTimeout(send, wait);
  }

  _paintVolume(s, db) {
    const volMax = this._layout.vol_max || 100;
    const off = db === null || db <= -volMax;
    const pos = off ? 0 : dbToPos(db, volMax);
    // percentage of the track minus the cap → no layout read needed
    s.cap.style.bottom = `calc(${pos.toFixed(4)} * (100% - 20px))`;
    s.db.textContent = off ? "−∞" : fmtDb(db);
  }

  _state(entityId) {
    return entityId && this._hass ? this._hass.states[entityId] : undefined;
  }

  _update() {
    if (!this._hass || !this._layout) return;
    const now = Date.now();
    for (const s of this._strips.values()) {
      const st = this._state(s.ch.media_player);
      const ok = st && st.state !== "unavailable";
      s.root.classList.toggle("unavailable", !ok);
      if (!st) continue;
      const a = st.attributes;

      // volume: keep the local value while dragging / until the echo arrives
      if (!s.drag && now > s.holdUntil) s.local = null;
      this._paintVolume(s, this._currentDb(s, st));

      const muted = !!a.is_volume_muted;
      s.mute.classList.toggle("on", muted);
      s.db.classList.toggle("muted", muted);
      s.icon.path.setAttribute("d", muted ? ICON_OFF : ICON_ON);

      const list = Array.isArray(a.source_list) ? a.source_list.slice() : [];
      if (a.source && !list.includes(a.source)) list.push(a.source);
      const key = list.join("\u0001");
      if (s.sourceKey !== key) {
        s.sourceKey = key;
        s.select.replaceChildren(...list.map((src) => new Option(src, src)));
      }
      if (this.shadowRoot.activeElement !== s.select) s.select.value = a.source || "";

      for (const r of Object.values(s.tone.rows)) {
        const ns = this._state(r.entity);
        if (!ns) continue;
        r.input.min = ns.attributes.min;
        r.input.max = ns.attributes.max;
        r.input.step = ns.attributes.step;
        if (this.shadowRoot.activeElement !== r.input) r.input.value = ns.state;
        r.value.textContent = fmtDb(Number(r.input.value));
      }
    }
  }
}

if (!customElements.get("audac-mixer-card")) {
  customElements.define("audac-mixer-card", AudacMixerCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "audac-mixer-card",
    name: "AUDAC Mixer",
    description: "Channel strips with faders, source, mute and VU meters for AUDAC M2 / MTX.",
  });
}
