/*
 * dom-kafelek – własna, minimalna wersja „card-mod” (bez zależności).
 *
 * Owija dowolną kartę (np. tile) i ustawia jej zmienne CSS zależnie od stanu encji.
 * Zmienne CSS przechodzą przez shadow DOM, więc nie trzeba grzebać w środku karty.
 *
 *   type: custom:dom-kafelek
 *   styl:                       # zawsze
 *     --ha-card-border-radius: 16px
 *   stany:                      # tylko w danym stanie encji z `card.entity`
 *     "off":
 *       --ha-card-background: rgba(255, 193, 7, 0.10)
 *       --state-inactive-color: rgb(201, 162, 39)
 *   card:
 *     type: tile
 *     entity: switch.kuchnia
 */
const WERSJA = "1.0.0";

class DomKafelek extends HTMLElement {
  setConfig(config) {
    if (!config || !config.card) throw new Error("dom-kafelek: brak sekcji `card`");
    this._config = config;
    this._ustawione = [];
    this._card = null;
    this._utworz();
  }

  async _utworz() {
    const config = this._config;
    const helpers = await window.loadCardHelpers();
    if (config !== this._config) return; // w międzyczasie przyszła nowa konfiguracja
    const card = helpers.createCardElement(config.card);
    if (this._hass) card.hass = this._hass;
    this.replaceChildren(card);
    this._card = card;
    this._stylizuj();
  }

  set hass(hass) {
    this._hass = hass;
    if (this._card) this._card.hass = hass;
    this._stylizuj();
  }

  _stylizuj() {
    if (!this._config) return;
    const entity = this._config.card.entity;
    const stan = entity && this._hass ? this._hass.states[entity]?.state : undefined;
    const vars = { ...(this._config.styl || {}), ...((this._config.stany || {})[stan] || {}) };
    for (const nazwa of this._ustawione) if (!(nazwa in vars)) this.style.removeProperty(nazwa);
    for (const [nazwa, wartosc] of Object.entries(vars)) this.style.setProperty(nazwa, wartosc);
    this._ustawione = Object.keys(vars);
  }

  getCardSize() {
    return this._card?.getCardSize?.() ?? 1;
  }

  getGridOptions() {
    return this._card?.getGridOptions?.() ?? { columns: 6, rows: 1 };
  }
}

customElements.define("dom-kafelek", DomKafelek);
window.customCards = window.customCards || [];
window.customCards.push({ type: "dom-kafelek", name: "Dom – kafelek z kolorami stanu" });
console.info(`dom-kafelek ${WERSJA}`);
