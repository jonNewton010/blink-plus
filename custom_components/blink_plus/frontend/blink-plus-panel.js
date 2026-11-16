// Blink Plus sidebar page: every Blink Plus camera with its live view and controls, ready after
// installation with nothing to configure. Registered by the integration (panel.py); the option
// "Show the Blink Plus page in the sidebar" removes it.
//
// Live video starts when a camera's "Start live view" is pressed, never on its own: opening this page
// must not wake every camera at once (battery cameras, and Blink allows one command per system at a time).
const CARD = "blink-plus-camera-card";

class BlinkPlusPanel extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({mode: "open"});
    this.shadowRoot.innerHTML =
      "<style>" +
      ":host{display:block;min-height:100vh;background:var(--primary-background-color);color:var(--primary-text-color);" +
      "font-family:var(--paper-font-body1_-_font-family,inherit)}" +
      "header{display:flex;align-items:center;gap:8px;height:var(--header-height,56px);padding:0 12px;box-sizing:border-box;" +
      "background:var(--app-header-background-color,var(--primary-color));color:var(--app-header-text-color,#fff);" +
      "position:sticky;top:0;z-index:2}" +
      "h1{flex:1;margin:0;font-size:20px;font-weight:400}" +
      ".arm{display:flex;flex-wrap:wrap;gap:8px}" +
      ".arm button{font:inherit;font-size:13px;padding:7px 12px;border-radius:16px;cursor:pointer;color:inherit;" +
      "background:rgba(255,255,255,.12);border:1px solid rgba(255,255,255,.4)}" +
      ".arm button.on{background:rgba(255,255,255,.3)}" +
      ".arm button:disabled{opacity:.55;cursor:default}" +
      "main{display:grid;gap:16px;padding:16px;grid-template-columns:repeat(auto-fill,minmax(min(100%,420px),1fr))}" +
      ".empty{padding:32px 16px;text-align:center;color:var(--secondary-text-color)}" +
      "</style>" +
      "<header><ha-menu-button></ha-menu-button><h1>Blink Plus</h1><div class=\"arm\"></div></header>" +
      "<main></main><div class=\"empty\" hidden></div>";
    this.menu = this.shadowRoot.querySelector("ha-menu-button");
    this.arm = this.shadowRoot.querySelector(".arm");
    this.main = this.shadowRoot.querySelector("main");
    this.empty = this.shadowRoot.querySelector(".empty");
    this.cards = new Map();
    this.armButtons = new Map();
    this.shown = "";
  }

  set hass(hass) {
    this._hass = hass;
    this.menu.hass = hass;
    this.render();
  }

  set narrow(narrow) {
    this.menu.narrow = narrow;
  }

  set panel(_panel) {
    // No panel configuration is used.
  }

  set route(_route) {
    // A single page: nothing to route.
  }

  entitiesOf(domain) {
    const hass = this._hass;
    const registry = hass.entities || {};
    return Object.keys(hass.states)
      .filter((id) => id.startsWith(domain + ".") && registry[id] && registry[id].platform === "blink_plus" && !registry[id].hidden)
      .sort((a, b) => this.label(a).localeCompare(this.label(b)));
  }

  label(id) {
    const state = this._hass.states[id];
    return (state && state.attributes && state.attributes.friendly_name) || id;
  }

  render() {
    const hass = this._hass;
    if (!hass) return;
    const cameras = this.entitiesOf("camera");
    const key = cameras.join(",");
    if (key !== this.shown) {
      this.shown = key;
      for (const [id, card] of this.cards) {
        if (!cameras.includes(id)) { card.remove(); this.cards.delete(id); }
      }
      if (cameras.length && !customElements.get(CARD)) {
        // Normally loaded already by the integration; this covers a page opened before that finished.
        import("./blink-plus-camera-card.js").then(() => { this.shown = ""; this.render(); }).catch(() => {});
        return;
      }
      for (const id of cameras) {
        if (this.cards.has(id)) continue;
        const card = document.createElement(CARD);
        card.setConfig({entity: id, autoplay: false});
        this.cards.set(id, card);
      }
      this.main.replaceChildren(...cameras.map((id) => this.cards.get(id)));
    }
    for (const card of this.cards.values()) card.hass = hass;
    this.empty.hidden = cameras.length > 0;
    if (!cameras.length) {
      this.empty.textContent = hass.entities
        ? "No Blink Plus cameras yet. Add your Blink account under Settings > Devices & services."
        : "Loading…";
    }

    // Arm / disarm each Blink system
    const switches = this.entitiesOf("switch").filter((id) => id.endsWith("_armed"));
    for (const [id, button] of this.armButtons) {
      if (!switches.includes(id)) { button.remove(); this.armButtons.delete(id); }
    }
    for (const id of switches) {
      let button = this.armButtons.get(id);
      if (!button) {
        button = document.createElement("button");
        button.type = "button";
        button.addEventListener("click", () => this.toggleArm(id, button));
        this.armButtons.set(id, button);
        this.arm.append(button);
      }
      const state = hass.states[id];
      const on = state.state === "on";
      const name = this.label(id).replace(/\s*armed$/i, "").trim() || "System";
      if (!button.dataset.busy) {
        button.textContent = name + (on ? ": armed" : ": disarmed");
        button.disabled = state.state === "unavailable";
      }
      button.classList.toggle("on", on);
      button.setAttribute("aria-pressed", on ? "true" : "false");
    }
  }

  async toggleArm(id, button) {
    if (button.dataset.busy) return;
    const on = this._hass.states[id].state === "on";
    button.dataset.busy = "1";
    button.disabled = true;
    button.textContent = on ? "Disarming…" : "Arming…";
    try {
      await this._hass.callService("switch", on ? "turn_off" : "turn_on", {entity_id: id});
    } catch (error) {
      // The state shown next comes from Home Assistant either way.
    } finally {
      delete button.dataset.busy;
      this.render();
    }
  }
}

if (!customElements.get("blink-plus-panel")) customElements.define("blink-plus-panel", BlinkPlusPanel);
