let currentTab = 'today';
let companyDrill = false;      // true while viewing a single company's contacts
let _companies = [], _prospects = [], _campaigns = [];
let _signals = null;           // last /api/signals payload, for the cohort builder
let _desk = { items: [], i: 0 };

// ── Appearance ──
//
// Three states, not two: "auto" follows the OS and is the default, so the
// dashboard matches the rest of your machine until you deliberately override
// it. Stored per-browser; nothing is sent anywhere.

const THEMES = ['auto', 'light', 'dark'];
const THEME_LABELS = {auto: 'Auto', light: 'Hell', dark: 'Dunkel'};

function applyTheme(mode) {
  const root = document.documentElement;
  if (mode === 'auto') root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', mode);
  const btn = document.getElementById('theme-btn');
  if (btn) btn.textContent = THEME_LABELS[mode] || mode;
}

function cycleTheme() {
  const now = localStorage.getItem('harvey-theme') || 'auto';
  const next = THEMES[(THEMES.indexOf(now) + 1) % THEMES.length];
  try { localStorage.setItem('harvey-theme', next); } catch { /* private mode */ }
  applyTheme(next);
}

(function initTheme() {
  let saved = 'auto';
  try { saved = localStorage.getItem('harvey-theme') || 'auto'; } catch { /* ignore */ }
  applyTheme(THEMES.includes(saved) ? saved : 'auto');
})();

// ── Utilities ──

function escHtml(s) {
  if (s === null || s === undefined || s === '') return '';
  return String(s).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]
  ));
}

// A value placed inside an onclick handler as a JS string: JSON-quote it,
// then escape for the attribute (escHtml alone is undone before the JS runs).
function jsq(v) {
  return escHtml(JSON.stringify(String(v ?? '')));
}

// The reason a failed action gave, or a fallback when it gave none.
function failMsg(data, fallback) {
  return (data && (data.message || data.error || data.detail)) || fallback;
}

// Lookup tables keyed by stored strings: "constructor" must not hit Object's.
function own(table, key) {
  return Object.prototype.hasOwnProperty.call(table, key) ? table[key] : undefined;
}

// Links built from collected data (websites, profiles) open only as http(s):
// a javascript: URL from a scraped page must never run in the dashboard.
function safeHref(u) {
  const s = String(u || '').trim();
  return /^https?:\/\//i.test(s) ? escHtml(s) : '';
}

// ── One status vocabulary ──
//
// Harvey's tables each carry their own words for state: a prospect is `new`,
// a campaign is `draft`, an email is `pending_review`, a signal is `proposed`.
// Five of those mean "waiting on you" and the old dashboard styled every one
// differently. Everything now resolves through this map, so a status reads the
// same way no matter which table it came out of.
//
// tone: waiting (needs a human) | active (in flight) | good | bad | idle
const STATUS = {
  // prospects
  new:            ['Neu', 'active'],
  contacted:      ['Kontaktiert', 'active'],
  replied:        ['Geantwortet', 'good'],
  interested:     ['Interessiert', 'good'],
  meeting:        ['Termin gebucht', 'note'],
  not_interested: ['Kein Interesse', 'bad'],
  bounced:        ['Unzustellbar', 'bad'],
  write_failed:   ['Schreiben fehlgeschlagen', 'bad'],
  queued:         ['In Warteschlange', 'active'],
  opted_out:      ['Abgemeldet', 'bad'],
  // campaigns
  draft:          ['Entwurf', 'waiting'],
  active:         ['Aktiv', 'good'],
  completed:      ['Abgeschlossen', 'idle'],
  paused:         ['Pausiert', 'bad'],
  // conversations
  open:           ['Offen', 'active'],
  closed:         ['Geschlossen', 'idle'],
  closed_won:     ['Gewonnen', 'good'],
  closed_lost:    ['Verloren', 'bad'],
  objection:      ['Einwand', 'waiting'],
  question:       ['Frage', 'active'],
  ooo:            ['Abwesend', 'idle'],
  wrong_person:   ['Falsche Person', 'waiting'],
  unsubscribe:    ['Abgemeldet', 'bad'],
  escalate:       ['Braucht einen Menschen', 'bad'],
  needs_human:    ['Braucht dich', 'waiting'],
  // outbox
  pending_review: ['Wartet auf dich', 'waiting'],
  approved:       ['Freigegeben', 'good'],
  sending:        ['Wird gesendet', 'active'],
  scheduled:      ['Geplant', 'active'],
  sent:           ['Gesendet', 'good'],
  awaiting_reply: ['Wartet auf Antwort', 'active'],
  failed:         ['Fehlgeschlagen', 'bad'],
  rejected:       ['Abgelehnt', 'idle'],
  cancelled:      ['Abgebrochen', 'idle'],
  // signals
  proposed:       ['Wartet auf dich', 'waiting'],
  confirmed:      ['Bestätigt', 'good'],
  // email deliverability
  verified:       ['Verifiziert', 'good'],
  risky:          ['Catch-all', 'waiting'],
  guess:          ['Nicht geprüft', 'idle'],
  invalid:        ['Ungültig', 'bad'],
  // runs
  running:        ['Läuft', 'active'],
  stale:          ['Veraltet', 'bad'],
  stalled:        ['Gestoppt', 'bad'],
};

function statusMeta(status) {
  const key = String(status || '').toLowerCase().replace(/[^a-z0-9]+/g, '_');
  const hit = own(STATUS, key);
  if (hit) return { label: hit[0], tone: hit[1] };
  const label = String(status || 'unbekannt').replace(/_/g, ' ');
  return { label: label.charAt(0).toUpperCase() + label.slice(1), tone: 'idle' };
}

function badge(status) {
  const m = statusMeta(status);
  return '<span class="badge t-' + m.tone + '">' + escHtml(m.label) + '</span>';
}

function formatDate(d) {
  if (!d) return '';
  try {
    let t = String(d).trim().replace(' ', 'T');
    if (/^\d{4}-\d\d-\d\dT/.test(t) && !/[zZ]$|[+-]\d\d:?\d\d$/.test(t)) t += 'Z';  // stored UTC
    const dt = new Date(t);
    if (isNaN(dt)) return escHtml(d);
    // German style: "8. Nov., 15:42"
    return dt.toLocaleString('de-DE', {day:'numeric',month:'short',hour:'2-digit',minute:'2-digit'});
  } catch { return escHtml(d); }
}

// ── Small render helpers ──

// The sprite in index.html holds every icon; this just references one.
function icon(name, cls) {
  return '<svg class="icon' + (cls ? ' ' + cls : '') + '" aria-hidden="true"><use href="#i-' + name + '"/></svg>';
}

function initials(first, last) {
  const a = String(first || '').trim(), b = String(last || '').trim();
  const ch = x => Array.from(x)[0] || '';  // whole characters, not UTF-16 halves
  const s = (ch(a) + ch(b)) || '?';
  return escHtml(s.toUpperCase());
}

// Stored times are UTC, often without a zone ("2026-10-21 03:07:57").
function parseTs(d) {
  let t = String(d || '').trim().replace(' ', 'T');
  if (t && !/[zZ]$|[+-]\d\d:?\d\d$/.test(t)) t += 'Z';
  return new Date(t);
}

// "vor 3 Min.", "vor 5 Std.", "Gestern", then a date: recent things read faster.
function relTime(d) {
  if (!d) return '';
  const dt = parseTs(d);
  if (isNaN(dt)) return escHtml(d);
  const mins = Math.round((Date.now() - dt) / 60000);
  if (mins < -1 && mins > -60) return 'in ' + -mins + ' Min.';
  if (mins < -1 && mins > -60 * 24) return 'in ' + Math.round(-mins / 60) + ' Std.';
  if (mins >= -1 && mins < 1) return 'gerade eben';
  if (mins >= 1 && mins < 60) return 'vor ' + mins + ' Min.';
  if (mins >= 60 && mins < 60 * 24) return 'vor ' + Math.round(mins / 60) + ' Std.';
  if (mins >= 60 * 24 && mins < 60 * 48) return 'Gestern';
  const opts = {day: 'numeric', month: 'short'};
  if (dt.getFullYear() !== new Date().getFullYear()) opts.year = 'numeric';
  return dt.toLocaleDateString('de-DE', opts);
}

function dayLabel(d) {
  const dt = parseTs(d);
  if (isNaN(dt)) return '';
  const today = new Date(); today.setHours(0, 0, 0, 0);
  const that = new Date(dt); that.setHours(0, 0, 0, 0);
  const diff = Math.round((today - that) / 86400000);
  if (diff === 0) return 'Heute';
  if (diff === 1) return 'Gestern';
  return dt.toLocaleDateString('de-DE', {weekday: 'long', day: 'numeric', month: 'short'});
}

function timeOfDay(d) {
  const dt = parseTs(d);
  return isNaN(dt) ? '' : dt.toLocaleTimeString('de-DE', {hour: '2-digit', minute: '2-digit'});
}

// Columns of a tiny bar chart; the last day is "today".
function sparkBars(values, title) {
  const vals = (values || []).map(v => Number(v) || 0);
  if (!vals.length) return '';
  const max = Math.max(...vals, 1);
  return '<span class="spark" role="img" aria-label="' + escHtml(title || 'Verlauf') + '">' +
    vals.map((v, i) => '<i class="' + (v ? 'on' : '') + '" style="height:' +
      Math.max(2, Math.round(v / max * 28)) + 'px" title="' + v + '"></i>').join('') + '</span>';
}

// A segmented meter, 0-100.
function meterHtml(pct, segments) {
  const n = segments || 10;
  const v = Math.max(0, Math.min(100, Number(pct) || 0));
  const on = Math.round(v / 100 * n);
  return '<span class="meter-row"><span class="meter" role="meter" aria-valuemin="0" ' +
    'aria-valuemax="100" aria-valuenow="' + v + '">' +
    Array.from({length: n}, (_, i) => '<i class="' + (i < on ? 'on' : '') + '"></i>').join('') +
    '</span><span class="v">' + v + '</span></span>';
}

const SOURCE_LABELS = {
  company_website: 'Teamseite', known_company_backfill: 'Teamseite',
  scraped_mailto: 'Veröffentlichte Adresse', hunter_domain_search: 'Hunter', linkedin_search: 'LinkedIn',
  job_board: 'Jobbörse', web_search: 'Websuche', company_inbox: 'Allgemeines Postfach der Firma',
  osm: 'OpenStreetMap', dataforseo_listings: 'DataForSEO', dataforseo_serp: 'DataForSEO SERP', dataforseo_maps: 'DataForSEO Maps',
  serper: 'Serper', tinyfish: 'TinyFish', scavio_maps: 'Scavio Maps', scavio_search: 'Scavio Search',
};
function sourceLabel(s) {
  if (!s) return '';
  // "discover:osm" is a company found by the discover stage through OSM.
  const key = String(s).replace(/^discover:/, '');
  return escHtml(Object.prototype.hasOwnProperty.call(SOURCE_LABELS, key) ? SOURCE_LABELS[key]
    : key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase()));
}

// A company inbox is stored with the English title "General inbox".
function titleLabel(t) {
  return t === 'General inbox' ? 'Allgemeines Postfach' : (t || '');
}

// Raw OSM values and old English industries, readable in German
// (mirrors harvey.collectors.discover.OSM_VALUE_LABELS for the common ones).
const INDUSTRY_LABELS = {
  yes: '', no: '', general: 'Allgemein', healthcare: 'Gesundheit', medical: 'Gesundheit',
  therapist: 'Therapeut', physiotherapist: 'Physiotherapie', psychotherapist: 'Psychotherapie',
  doctor: 'Arztpraxis', doctors: 'Arztpraxis', physician: 'Arztpraxis', dentist: 'Zahnarzt',
  alternative: 'Heilpraktiker', clinic: 'Klinik', pharmacy: 'Apotheke', veterinary: 'Tierarzt',
  craft: 'Handwerk', office: 'Dienstleistung', shop: 'Einzelhandel',
  roofer: 'Dachdecker', roofing: 'Dachdecker', plumber: 'Sanitär', hvac: 'Heizung und Klima',
  electrician: 'Elektriker', carpenter: 'Tischler', painter: 'Maler', gardener: 'Gärtner',
  builder: 'Bau', lawyer: 'Rechtsanwalt', tax_advisor: 'Steuerberatung', accountant: 'Buchhaltung',
  advertising_agency: 'Werbeagentur', estate_agent: 'Immobilien', insurance: 'Versicherung',
  construction_company: 'Bauunternehmen', hairdresser: 'Friseur', beauty: 'Kosmetik',
  bakery: 'Bäckerei', car_repair: 'Autowerkstatt', company: 'Firma', consulting: 'Beratung',
};
function industryLabel(v) {
  const raw = String(v || '').split(';')[0].trim();
  const low = raw.toLowerCase();
  return Object.prototype.hasOwnProperty.call(INDUSTRY_LABELS, low) ? INDUSTRY_LABELS[low]
    : raw.replace(/_/g, ' ');
}

// Campaigns from before the German names were "healthcare-inbox-outreach".
function campaignLabel(name) {
  const m = /^(.*?)-(inbox-outreach|outreach)$/.exec(String(name || ''));
  if (!m) return name || '';
  return (industryLabel(m[1]) || 'Allgemein') + ' · ' + (m[2] === 'inbox-outreach' ? 'Postfach' : 'Kontakte');
}

const AGENT_ICONS = {scout: 'discover', writer: 'pen', sender: 'campaigns', handler: 'message',
                     analyst: 'usage', user: 'contacts', main: 'refresh'};
// One plain sentence per logged action, from the details each agent records.
function actionText(a) {
  const d = a.details || {};
  const who = escHtml(d.prospect_email || d.prospect || d.to || '');
  const camp = escHtml(campaignLabel(d.campaign_name || d.campaign || ''));
  const intent = d.intent ? ' · ' + escHtml(statusMeta(d.intent).label) : '';
  const n = (v, one, many) => (Number(v) || 0) + ' ' + ((Number(v) || 0) === 1 ? one : many);
  switch (a.action_type) {
    case 'prospect': return n(d.prospects_found, 'neuer Kontakt', 'neue Kontakte') + ' gefunden';
    case 'write_campaign': return '„' + camp + '“ für ' + n(d.prospect_count, 'Kontakt', 'Kontakte') + ' geschrieben';
    case 'write_failed': return 'Konnte für einige Kontakte nicht schreiben' + (camp ? ' in „' + camp + '“' : '');
    case 'stage_campaign': return n(d.staged, 'E-Mail', 'E-Mails') + ' aus „' + camp + '“ eingereiht' +
      (d.mode ? ' · ' + escHtml(d.mode) : '');
    case 'send_campaign': return '„' + camp + '“ an Instantly gesendet · ' + n(d.leads_added, 'Lead', 'Leads');
    case 'email_sent': return d.kind === 'reply' ? 'Antwort an ' + who + ' gesendet'
      : 'E-Mail ' + escHtml(String(d.step || 1)) + ' an ' + who + ' gesendet';
    case 'reply': return who + ' geantwortet' + intent;
    case 'reply_queued': return 'Antwort an ' + who + ' entworfen' + intent;
    case 'opt_out': return who + ' hat sich abgemeldet' + (d.why ? ' · ' + escHtml(d.why) : '');
    case 'opt_out_undone': return 'Du hast die Abmeldung von ' + (who || 'einem Kontakt') + ' zurückgenommen';
    case 'reply_after_opt_out': return who + ' hat nach der Abmeldung erneut geschrieben · keine Antwort gesendet';
    case 'escalation': return who + ' für dich markiert' + (d.reason ? ' · ' + escHtml(d.reason) : '');
    case 'bounce': return 'E-Mail an ' + who + ' unzustellbar';
    case 'manual_send': return 'Du hast einen Kontakt als von Hand angeschrieben markiert';
    case 'manual_send_undone': return 'Du hast eine Markierung „von Hand gesendet“ zurückgenommen';
    case 'outbox_edited': return 'Du hast einen Entwurf bearbeitet';
    case 'conversation_status':
      return d.to === 'closed' ? 'Du hast ein Gespräch geschlossen'
        : d.from === 'needs_human' ? 'Du hast ein Gespräch an Harvey zurückgegeben' : 'Du hast ein Gespräch wieder geöffnet';
  }
  const text = escHtml(String(a.action_type || '').replace(/_/g, ' '));
  return a.agent === 'main' ? 'Herzschlag: ' + text : text;
}

function emptyState(glyph, title, copy) {
  return '<div class="empty"><div class="glyph">' + glyph + '</div>' +
    '<div class="title">' + title + '</div>' +
    '<div class="copy">' + copy + '</div></div>';
}

function offlineState() {
  return emptyState('&#9888;', 'Das Dashboard erreicht den Server nicht',
    'Der Dashboard-Prozess wurde vielleicht beendet. Starte ihn mit <b>harvey dashboard</b> neu und lade diese Seite neu.');
}

async function api(path, opts) {
  try {
    const r = await authFetch(path, opts);
    if (await lockIfRefused(r)) return null;
    if (!r.ok) {
      // An action the server refused says why ("a run is already going");
      // pass that body on so the toast can show it. Reads stay null.
      if (!opts || !opts.method || opts.method === 'GET') return null;
      const body = await r.json().catch(() => null);
      return body && typeof body === 'object' ? {success: false, ...body} : null;
    }
    return await r.json();
  } catch {
    return null;
  }
}

// ── Locked: no token, a wrong one, or the server can't load its own ──

let _locked = false;

async function lockIfRefused(r) {
  if (r.status !== 401 && r.status !== 503) {
    if (r.ok && _locked) hideLock();
    return false;
  }
  let why = '';
  if (r.status === 503) {
    try { why = ((await r.json()) || {}).message || ''; } catch { /* ignore */ }
  }
  showLock(r.status === 503
    ? 'Der Dashboard-Server kann seine Token-Datei nicht nutzen und lehnt deshalb jede Anfrage ab. ' + why
    : authToken()
      ? 'Das gespeicherte Token wurde nicht akzeptiert (das Dashboard hat vielleicht ein neues).'
      : 'Dieser Tab hat das Dashboard-Token noch nicht.');
  return true;
}

function showLock(reason) {
  _locked = true;
  const el = document.getElementById('lock-screen');
  if (!el) return;
  document.getElementById('lock-reason').textContent = reason;
  el.style.display = 'flex';
  const dot = document.getElementById('header-dot');
  const text = document.getElementById('header-status-text');
  if (dot) dot.className = 'status-dot offline';
  if (text) text.textContent = 'Gesperrt';
}

function hideLock() {
  _locked = false;
  const el = document.getElementById('lock-screen');
  if (el) el.style.display = 'none';
}

function unlockWithToken(event) {
  event.preventDefault();
  const input = document.getElementById('lock-token');
  const value = (input.value || '').trim();
  input.value = '';
  if (!setAuthToken(value)) {
    document.getElementById('lock-reason').textContent =
      'Das sieht nicht nach einem Dashboard-Token aus. Kopiere es noch einmal aus dem Link oder aus data/dashboard-token.';
    return;
  }
  hideLock();
  loadCurrentTab();
  loadHarveyStatus();
}

// Downloads go through the same authorised fetch and are handed to the
// browser as a local blob, so no download URL ever carries the token.
async function downloadCsv(path, filename, btn) {
  if (btn) btn.disabled = true;
  try {
    const r = await authFetch(path);
    if (await lockIfRefused(r)) return;
    if (!r.ok) { showToast('Export fehlgeschlagen.', 'error'); return; }
    const href = URL.createObjectURL(await r.blob());
    const a = document.createElement('a');
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(href), 10000);
  } catch {
    showToast('Export fehlgeschlagen. Läuft das Dashboard noch?', 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

function showToast(msg, type) {
  const t = document.createElement('div');
  t.className = 'toast ' + type;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 2600);
}

function toggleVisibility(inputId) {
  const el = document.getElementById(inputId);
  el.type = el.type === 'password' ? 'text' : 'password';
}

// ── Tabs ──

function showTab(id, btn) {
  currentTab = id;
  if (id === 'companies') companyDrill = false;
  document.querySelectorAll('.section').forEach(s => s.classList.remove('active'));
  document.querySelectorAll('nav button').forEach(b => b.classList.remove('active'));
  document.getElementById(id).classList.add('active');
  if (btn) btn.classList.add('active');
  loadCurrentTab();
}

function loadCurrentTab() {
  switch (currentTab) {
    case 'today': loadLiveStatus(); loadToday(); loadTrend(); loadFunnel(); loadSetupStatus(); loadRuns(); loadTodayActivity(); break;
    case 'signals': loadSignals(); break;
    case 'discover': loadDiscoverProviders(); break;
    case 'companies': if (!companyDrill) loadCompanies(); break;
    case 'prospects': loadProspects(); break;
    case 'campaigns': loadCampaigns(); break;
    case 'outbox': loadOutbox(); break;
    case 'conversations': loadConversations(); break;
    case 'activity': loadActivity(); break;
    case 'usage': loadUsage(); break;
    case 'settings': loadSettings(); loadSendingModeCard(); break;
    case 'controls': loadHarveyStatus(); loadLogs(); break;
  }
}

// ── Today: what needs a human ──

async function loadToday() {
  const data = await api('/api/today');
  const el = document.getElementById('today-queue');
  if (!data) { el.innerHTML = offlineState(); return; }
  if (data.error) {
    // A failed check must never read as "Nothing needs you".
    el.innerHTML = emptyState(icon('alert'), 'Konnte nicht prüfen, was dich braucht',
      'Beim Lesen von Harveys Daten ist ein Fehler aufgetreten: ' + escHtml(data.error));
    return;
  }

  navCount('nav-today', (data.items || []).filter(i => i.tone !== 'good').length);
  navCount('nav-outbox', (data.stats || {}).outbox_pending || 0);
  navCount('nav-signals', (data.stats || {}).signals_proposed || 0);
  navCount('nav-conversations', (data.stats || {}).needs_human || 0);
  _todayStats = data.stats || {};
  renderFigures(_todayStats);

  const items = data.items || [];
  if (!items.length) {
    el.innerHTML = '<div class="queue-clear">' +
      '<div class="queue-icon">' + icon('check') + '</div><div>' +
      '<div class="qtitle">Nichts braucht dich</div>' +
      '<div class="qdetail">Harvey hat alles, was er braucht. Alles, was eine Entscheidung ' +
      'braucht, etwa eine E-Mail zum Freigeben oder ein Signal zum Bestätigen, erscheint hier.</div>' +
      '</div></div>';
    return;
  }

  const glyph = {bad: 'alert', warn: 'flag', good: 'check'};
  el.innerHTML = '<div class="queue">' + items.map(it => {
    const tone = escHtml(it.tone || 'warn');
    return '<div class="queue-item ' + tone + '">' +
      '<div class="queue-icon">' + icon(glyph[it.tone] || 'flag') + '</div>' +
      '<div class="qtext">' +
        '<div class="qtitle">' + escHtml(it.title) + '</div>' +
        '<div class="qdetail">' + escHtml(it.detail) + '</div>' +
      '</div>' +
      '<button class="btn btn-secondary btn-sm" onclick="goTab(' + jsq(it.tab) + ')">' +
        escHtml(it.action) + icon('arrow') + '</button>' +
    '</div>';
  }).join('') + '</div>';
}

// ── Live status: the "what's happening?" answer, kept on screen ──

async function loadLiveStatus() {
  const el = document.getElementById('live-status');
  if (!el) return;
  const d = await api('/api/live-status');
  if (!d || !Array.isArray(d.steps)) {
    // Keep the last good numbers on screen, but never let them pass for live.
    const flag = el.querySelector('.live-flag');
    if (flag) flag.textContent = 'Harvey ist gerade nicht erreichbar. Die Zahlen unten sind vielleicht veraltet.';
    else el.innerHTML = '<p class="muted live-flag">Harvey ist gerade nicht erreichbar. ' +
      'Ist das Dashboard-Fenster noch offen?</p>';
    return;
  }
  const glyph = {done: icon('check'), running: '<span class="pulse"></span>', partial: '', waiting: ''};
  const fill = {done: 'var(--good)', running: 'var(--accent)', partial: 'var(--accent)', waiting: 'var(--line-strong)'};
  const rows = d.steps.map(s => {
    const st = Object.prototype.hasOwnProperty.call(glyph, s.state) ? s.state : 'waiting';
    const pct = s.total ? Math.min(100, Math.round(100 * s.done / s.total)) : 0;
    const bar = s.total
      ? '<div class="bar"><span style="width:' + pct + '%;background:' + fill[st] + '"></span></div>'
      : '';
    return '<div class="step">' +
      '<span class="step-icon ' + st + '">' + glyph[st] + '</span>' +
      '<span class="step-label">' + escHtml(s.label) + '</span>' +
      '<span class="step-detail">' + escHtml(s.detail) + '</span>' + bar +
    '</div>';
  }).join('');
  const jobName = r => r.stage === 'profile' ? 'Websites lesen'
    : r.stage === 'discover' ? 'Firmen suchen (' + (r.provider || '') + ')'
    : String(r.stage || 'Auftrag');
  const running = d.running || [];
  const runningHtml = running.length
    ? running.map(r => '<span class="live-now"><span class="pulse"></span>' + escHtml(jobName(r)) +
        ' · ' + r.elapsed_min + ' Min.' + (r.eta_text ? ' · ' + escHtml(r.eta_text) : '') + '</span>').join('')
    : '<span class="live-now">Gerade läuft nichts</span>';
  const stoppedHtml = (d.stopped || []).length
    ? '<div class="banner bad" style="margin:8px 0 0">' + icon('alert') + '<p style="color:inherit">Vor dem Ende gestoppt: ' +
      (d.stopped || []).map(r => escHtml(jobName(r))).join(', ') + '.</p></div>'
    : '';
  el.innerHTML =
    '<div class="live-head">' + runningHtml +
      '<span class="setup-pct">Bisherige Kosten $' + Number(d.cost_usd || 0).toFixed(2) + '</span></div>' +
    stoppedHtml +
    '<div class="steps">' + rows + '</div>' +
    (d.next ? '<div class="live-next">' + icon('arrow') + '<span><b>Als Nächstes</b> · ' + escHtml(d.next) + '</span>' +
      (d.next_tab ? '<button class="btn btn-secondary btn-sm" onclick="goTab(' + jsq(d.next_tab) + ')">Los</button>' : '') +
      '</div>' : '') +
    '<div class="live-flag">Aktualisiert ' + new Date().toLocaleTimeString('de-DE') + ' · lädt alle 10 Sekunden neu</div>';
}

// KPI cards. Totals come from /api/today; the 14-day bars from /api/trend.
function renderFigures(stats) {
  const el = document.getElementById('today-kpis');
  if (!el) return;
  const t = _trend || {};
  const sum = a => (a || []).reduce((x, y) => x + (Number(y) || 0), 0);
  const last7 = a => sum((a || []).slice(-7));
  const card = (label, value, spark, note) =>
    '<div class="kpi"><div class="kpi-label">' + label + '</div>' +
    '<div class="kpi-main"><span class="kpi-value' + (value ? '' : ' zero') + '">' + value + '</span>' + spark + '</div>' +
    '<span class="kpi-note">' + note + '</span></div>';

  // Instantly sends from its own servers, so the Outbox never sees them.
  const sent = stats.email_provider === 'instantly'
    ? card('Gesendete E-Mails', '&mdash;', '', 'Instantly zählt die Sendungen')
    : card('Gesendete E-Mails', sum(t.sent), sparkBars(t.sent, 'Gesendete E-Mails pro Tag, letzte 14 Tage'),
           'letzte 14 Tage · <b>' + (stats.outbox_approved || 0) + '</b> geplant');
  const waiting = (stats.outbox_pending || 0) + (stats.needs_human || 0);
  const waitNote = [
    stats.outbox_pending ? stats.outbox_pending + ' zum Freigeben' : '',
    stats.needs_human ? stats.needs_human + ' an dich übergeben' : '',
  ].filter(Boolean).join(' · ') || 'nichts freizugeben';
  const facts = document.getElementById('today-facts');
  if (facts) facts.innerHTML =
    '<span><b>' + (stats.companies || 0) + '</b> Firmen</span>' +
    '<span><b>' + (stats.unprofiled || 0) + '</b> Websites noch nicht gelesen</span>' +
    '<span><b>' + (stats.signals_confirmed || 0) + '</b> Signale aktiv</span>';

  el.innerHTML = '<div class="kpis">' +
    card('Gefundene Kontakte', stats.prospects || 0, sparkBars(t.found, 'Gefundene Kontakte pro Tag, letzte 14 Tage'),
         '<b>+' + last7(t.found) + '</b> diese Woche') +
    sent +
    card('Gespräche', stats.open_conversations || 0, sparkBars(t.replies, 'Neue Antworten pro Tag, letzte 14 Tage'),
         '<b>' + sum(t.replies) + '</b> neu in 14 Tagen') +
    card('Wartet auf dich', waiting, '', waitNote) +
  '</div>';
}

let _todayStats = {};
let _trend = null;

async function loadTrend() {
  const t = await api('/api/trend?days=14&tz=' + -new Date().getTimezoneOffset());
  if (t && Array.isArray(t.days)) { _trend = t; renderFigures(_todayStats); }
}

// Contacts by stage: one bar, one legend, from /api/stats.
async function loadFunnel() {
  const el = document.getElementById('today-figures');
  if (!el) return;
  const data = await api('/api/stats');
  if (!data) return;  // offline: keep the last numbers; Today's queue says so
  if (data.error) { el.innerHTML = '<p class="muted" style="font-size:13px">Konnte die Pipeline nicht lesen.</p>'; return; }
  const total = (data.prospects || {}).total || 0;
  const pipe = data.pipeline || {};
  const stages = [
    ['Zu schreiben', pipe.to_write || 0, 'c-accent-soft'],
    ['Kontaktiert', pipe.contacted || 0, 'c-accent'],
    ['Geantwortet', pipe.replied || 0, 'c-active'],
    ['Interessiert', pipe.interested || 0, 'c-good'],
    ['Bereit für Termin', pipe.ready_to_meet || 0, 'c-note'],
    ['Abgeschlossen', pipe.closed_out || 0, 'c-idle'],
  ];
  if (!total) {
    el.innerHTML = '<p class="muted" style="font-size:13px">Noch keine Kontakte. Scout fügt sie hinzu, sobald er Leute findet.</p>';
    return;
  }
  el.innerHTML =
    '<div class="funnel-bar" role="img" aria-label="Kontakte nach Phase">' +
      stages.filter(s => s[1] > 0).map(s =>
        '<span class="seg ' + s[2] + '" style="flex:' + s[1] + '" title="' + s[0] + ': ' + s[1] + '"></span>').join('') +
    '</div>' +
    '<div class="legend-rows">' + stages.map(s =>
      '<div class="legend-row"><span class="legend-key"><i class="' + s[2] + '"></i>' + s[0] + '</span>' +
      '<span class="legend-val">' + s[1] + '</span>' +
      '<span class="legend-pct">' + Math.round(100 * s[1] / total) + ' %</span></div>'
    ).join('') + '</div>';
}

async function loadTodayActivity() {
  const el = document.getElementById('today-activity');
  if (!el) return;
  const data = await api('/api/activity');
  if (!Array.isArray(data)) return;  // offline: Today's queue says so
  // Heartbeats are noise here; the Activity tab keeps them.
  const shown = data.filter(a => a.agent !== 'main').slice(0, 8);
  if (!shown.length) {
    el.innerHTML = '<p class="muted" style="font-size:13px">' +
      'Noch nichts. Jede Aktion von Harvey erscheint hier.</p>';
    return;
  }
  el.innerHTML = '<div class="timeline">' + shown.map(a =>
    '<div class="tl-item">' +
      '<span class="tl-time">' + relTime(a.created_at) + '</span>' +
      '<span class="tl-icon">' + icon(own(AGENT_ICONS, a.agent) || 'activity') + '</span>' +
      '<span class="tl-text">' + actionText(a) + '</span>' +
      '<span class="tl-agent">' + escHtml(a.agent) + '</span>' +
    '</div>').join('') + '</div>';
}

function navCount(id, n) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = n > 0 ? n : '';
  el.className = 'nav-count' + (n > 0 ? ' on' : '');
}

// Jump to a tab from a link that isn't itself a nav button.
function goTab(id) {
  const btns = [...document.querySelectorAll('nav button')];
  const btn = btns.find(b => (b.getAttribute('onclick') || '').includes("'" + id + "'"));
  showTab(id, btn);
}

async function loadRuns() {
  const el = document.getElementById('today-runs');
  const block = document.getElementById('today-runs-block');
  if (!el) return;
  const runs = await api('/api/runs');
  if (!Array.isArray(runs) || !runs.length) {
    if (block) block.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  if (block) block.style.display = '';
  el.innerHTML = '<div class="runs">' + runs.slice(0, 5).map(r =>
    '<div class="run">' +
      '<span class="name">' + escHtml(r.stage) + '</span>' + badge(r.status) +
      '<span class="when">' + relTime(r.started_at) + '</span>' +
      '<span class="stats">' + (r.records || 0) + ' Datensätze · ' +
        (r.cost_usd ? '$' + Number(r.cost_usd).toFixed(4) : 'kostenlos') + '</span>' +
    '</div>').join('') + '</div>';
}

// ── Setup checklist (lives on Today, and disappears once it's done) ──

async function loadSetupStatus() {
  const el = document.getElementById('today-setup');
  if (!el) return;
  const data = await api('/api/setup-status');
  if (!data || !data.checks) { el.innerHTML = ''; return; }

  const pct = data.percent || 0;
  // Finished setup disappears completely rather than greeting you forever.
  if (pct === 100) { el.innerHTML = ''; return; }

  // The first unfinished required step is the one to do now.
  const next = data.checks.find(c => c.required && !c.done);
  const renderCheck = (c, optional) =>
    '<div class="check-item' + (c === next ? ' current' : '') + '">' +
      (c.done ? '<span class="check-icon done">' + icon('check') + '</span>'
              : '<span class="check-icon pending"></span>') +
      '<div class="check-info">' +
        '<div class="check-label ' + (c.done ? 'done' : '') + '">' + escHtml(c.label) +
          (optional ? '<span class="optional-tag">optional</span>' : '') + '</div>' +
        (!c.done ? '<div class="check-help">' + escHtml(c.help) + '</div>' : '') +
        (!c.done && c.id === 'warmup_service' ? warmupServiceActions(c) : '') +
      '</div></div>';

  const required = data.checks.filter(c => c.required);
  const optional = data.checks.filter(c => !c.required);
  const done = required.filter(c => c.done).length;

  el.innerHTML =
    '<div class="card setup-card">' +
      '<div class="setup-head"><div><h3>Harvey fertig einrichten</h3>' +
        '<p class="muted">Sobald das erledigt ist, sucht Harvey Kontakte und versendet E-Mails.</p></div>' +
        '<span class="setup-pct">' + done + ' von ' + required.length + ' erledigt</span></div>' +
      '<div class="bar thick" style="margin-bottom:8px"><span style="width:' + pct + '%;background:var(--accent)"></span></div>' +
      required.map(c => renderCheck(c, false)).join('') +
      (optional.length
        ? '<details style="margin-top:10px"><summary class="muted" ' +
          'style="font-size:12px">' + optional.length + ' optional</summary>' +
          '<div style="margin-top:6px">' +
          optional.map(c => renderCheck(c, true)).join('') + '</div></details>'
        : '') +
    '</div>';
}

// The warmup step: get TrulyInbox, paste its key, or say you use another service.
function warmupServiceActions(c) {
  return '<div class="btn-group warmup-actions">' +
      '<a class="btn btn-primary btn-sm" href="' + safeHref(c.link) + '" target="_blank" ' +
        'rel="noopener sponsored">TrulyInbox holen</a>' +
      '<input class="form-input" id="trulyinbox-key" type="password" autocomplete="off" ' +
        'placeholder="Deinen TrulyInbox-API-Schlüssel einfügen" aria-label="TrulyInbox-API-Schlüssel" ' +
        'onkeydown="if (event.key === \'Enter\') connectTrulyInbox()">' +
      '<button class="btn btn-secondary btn-sm" id="trulyinbox-go" onclick="connectTrulyInbox()">Verbinden</button>' +
      '<button class="btn btn-ghost btn-sm" onclick="useOtherWarmup()">Ich nutze einen anderen Dienst</button>' +
    '</div>';
}

async function connectTrulyInbox() {
  const input = document.getElementById('trulyinbox-key');
  const go = document.getElementById('trulyinbox-go');
  if (!input || !input.value.trim()) return;
  if (go) go.disabled = true;
  const data = await api('/api/trulyinbox/connect', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({key: input.value.trim()}),
  });
  if (go) go.disabled = false;
  if (data && data.success) {
    input.value = '';
    showToast(data.message || 'TrulyInbox verbunden', 'success');
    loadSetupStatus();
  } else {
    showToast((data && data.message) || 'TrulyInbox konnte nicht verbunden werden', 'error');
  }
}

async function useOtherWarmup() {
  const data = await api('/api/trulyinbox/other', {method: 'POST'});
  if (data && data.success) loadSetupStatus();
}

// ── Signals: Harvey proposes, you confirm ──

async function loadSignals() {
  const data = await api('/api/signals');
  const sumEl = document.getElementById('signals-summary');
  const grpEl = document.getElementById('signals-groups');
  if (!data || data.error) { grpEl.innerHTML = offlineState(); sumEl.innerHTML = ''; return; }
  _signals = data;

  const s = data.summary || {};
  navCount('nav-signals', s.proposed || 0);
  const parts = [['Bestätigt', s.confirmed || 0, 'c-good'], ['Wartet auf dich', s.proposed || 0, 'c-wait'],
                 ['Ausgeschaltet', s.rejected || 0, 'c-idle']];
  const all = parts.reduce((x, p) => x + p[1], 0) || 1;
  sumEl.innerHTML = '<div class="card sig-summary"><div class="funnel-bar">' +
    parts.filter(p => p[1]).map(p => '<span class="seg ' + p[2] + '" style="flex:' + p[1] + '"></span>').join('') +
    '</div><div class="funnel-legend">' + parts.map(p =>
      '<div class="legend-item"><span class="legend-key"><i class="' + p[2] + '"></i>' + p[0] + '</span>' +
      '<span class="legend-val">' + p[1] + '<small> · ' + Math.round(100 * p[1] / all) + ' %</small></span></div>'
    ).join('') + '</div></div>';

  grpEl.innerHTML = (data.groups || []).map(g => {
    // Only the undecided ones: "Confirm all" must never re-enable a signal
    // you turned off (or "Skip all" turn off one you confirmed).
    const pending = escHtml(JSON.stringify(g.signals.filter(x => x.status === 'proposed').map(x => x.code)));
    const undecided = g.signals.filter(x => x.status === 'proposed').length;
    return '<div class="sig-group">' +
      '<div class="sig-group-head"><div>' +
        '<h3>' + escHtml(g.label) + '</h3><p>' + escHtml(g.blurb) + '</p>' +
      '</div>' +
      (undecided
        ? '<div style="display:flex;gap:6px;flex-shrink:0">' +
            '<button class="btn btn-primary btn-sm" onclick="setSignals(' + pending +
              ', \'confirmed\')">Alle ' + undecided + ' bestätigen</button>' +
            '<button class="btn btn-secondary btn-sm" onclick="setSignals(' + pending +
              ', \'rejected\')">Alle ' + undecided + ' überspringen</button>' +
          '</div>'
        : '') +
      '</div>' +
      '<div class="card flush">' + g.signals.map(sigRow).join('') + '</div>' +
    '</div>';
  }).join('');

  renderCohortBuilder();
}

function sigRow(sig) {
  const free = /free|included|kostenlos|enthalten/i.test(sig.cost_note || '');   // signals.FREE_COST_PREFIXES
  const costCls = free ? 'free' : (/\$/.test(sig.cost_note || '') ? 'paid' : '');
  const seen = sig.companies
    ? '<span class="cost-chip">bisher bei ' + sig.companies +
      (sig.companies === 1 ? ' Firma' : ' Firmen') + ' gesehen</span>' : '';
  const floor = sig.confidence_floor > 0
    ? '<span class="cost-chip">nur ab ' +
      Math.round(sig.confidence_floor * 100) + ' % Sicherheit erfasst</span>' : '';

  const decide = sig.status === 'confirmed'
    ? '<button class="btn btn-secondary btn-sm" onclick="setSignals([' + jsq(sig.code) +
        '],\'rejected\')">Ausschalten</button>'
    : sig.status === 'rejected'
      ? '<button class="btn btn-secondary btn-sm" onclick="setSignals([' + jsq(sig.code) +
          '],\'confirmed\')">Einschalten</button>'
      : '<button class="btn btn-primary btn-sm" onclick="setSignals([' + jsq(sig.code) +
          '],\'confirmed\')">Bestätigen</button>' +
        '<button class="btn btn-secondary btn-sm" onclick="setSignals([' + jsq(sig.code) +
          '],\'rejected\')">Überspringen</button>';

  return '<div class="sig-row ' + (sig.status === 'rejected' ? 'rejected' : '') + '">' +
    '<div class="sig-main">' +
      '<div class="sig-label">' + escHtml(sig.label || sig.code) + ' ' +
        '<span class="sig-code">' + escHtml(sig.code) + '</span></div>' +
      '<div class="sig-desc">' + escHtml(sig.description) + '</div>' +
      '<div class="sig-meta">' +
        '<span class="cost-chip ' + costCls + '">' + escHtml(sig.cost_note || 'Kosten unbekannt') + '</span>' +
        floor + seen + badge(sig.status) +
      '</div>' +
    '</div>' +
    '<div class="sig-decide">' + decide + '</div>' +
  '</div>';
}

async function setSignals(codes, status) {
  const data = await api('/api/signals/status', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({codes: codes, status: status}),
  });
  if (data && data.success) {
    showToast(
      status === 'confirmed'
        ? (data.changed === 1 ? '1 Signal bestätigt. Harvey erfasst es ab jetzt.'
          : data.changed + ' Signale bestätigt. Harvey erfasst sie ab jetzt.')
        : data.changed + (data.changed === 1 ? ' Signal ausgeschaltet.' : ' Signale ausgeschaltet.'),
      'success');
  } else {
    showToast(failMsg(data, 'Das Signal konnte nicht geändert werden.'), 'error');
  }
  loadSignals();
}

// ── Discover: pick a source, price it, then run it ──

let _provider = null;
let _discoverPoll = null;

async function loadDiscoverProviders() {
  const el = document.getElementById('discover-providers');
  const data = await api('/api/discover/providers');
  if (!data || !data.providers) { el.innerHTML = offlineState(); return; }

  _provider = _provider || data.selected || data.default;
  el.innerHTML = '<div class="prov-grid">' + data.providers.map(p => {
    const ready = p.configured
      ? '<span class="badge t-good">bereit</span>'
      : '<span class="badge t-waiting">braucht einen Schlüssel</span>';
    return '<div class="prov-card ' + (p.key === _provider ? 'selected' : '') +
      '" onclick="pickProvider(' + jsq(p.key) + ')">' +
      '<div class="prov-head"><h3>' + escHtml(p.label) + '</h3>' + ready + '</div>' +
      '<div class="blurb">' + escHtml(p.blurb) + '</div>' +
      '<div class="row"><span class="k">Kosten</span><span class="v">' +
        escHtml(p.cost_note) + '</span></div>' +
      '<div class="row"><span class="k">Gratis</span><span class="v">' +
        escHtml(p.free_tier) + '</span></div>' +
      (p.needs_key
        ? '<div class="row"><span class="k">Einrichtung</span><span class="v">' +
          escHtml(p.env_keys.join(', ')) + ' in .env &middot; ' +
          '<a href="' + safeHref(p.signup_url) + '" target="_blank" rel="noopener noreferrer">Schlüssel holen</a>' +
          '</span></div>'
        : '') +
      (p.caveat ? '<div class="caveat">' + escHtml(p.caveat) + '</div>' : '') +
    '</div>';
  }).join('') + '</div>';

  const running = data.running;
  // Reading sites can't be stopped part-way; only a discovery run can.
  document.getElementById('disc-stop').style.display = running && data.running_kind !== 'profile' ? '' : 'none';
  renderProfileCard(data.unprofiled || 0, running);
  if (running && !_discoverPoll) {
    _discoverPoll = setInterval(loadDiscoverProviders, 4000);
  } else if (!running && _discoverPoll) {
    clearInterval(_discoverPoll);
    _discoverPoll = null;
    // A finished run leaves the button stuck on "Läuft…" otherwise.
    const btn = document.getElementById('disc-run');
    btn.disabled = true;
    btn.textContent = 'Erst Kosten schätzen';
    showToast(data.running_kind === 'profile' ? 'Websites fertig gelesen.' : 'Suche abgeschlossen.', 'success');
  }
  renderDiscoverResult(data.last_report, running);
}

function pickProvider(key) {
  _provider = key;
  document.getElementById('disc-run').disabled = true;
  document.getElementById('disc-run').textContent = 'Erst Kosten schätzen';
  document.getElementById('discover-estimate').innerHTML = '';
  loadDiscoverProviders();
}

function discoverBody() {
  const raw = document.getElementById('disc-cities').value.trim();
  return {
    provider: _provider,
    // Semicolons or newlines, never commas: "Denver, CO" is one city.
    cities: raw ? raw.split(/[;\n]/).map(c => c.trim()).filter(Boolean) : [],
    depth: Number(document.getElementById('disc-depth').value) || 30,
    limit: Number(document.getElementById('disc-limit').value) || 100,
    max_spend: Number(document.getElementById('disc-cap').value) || 1,
    preset: (document.getElementById('disc-preset') || {}).value || '',
  };
}

async function estimateDiscovery() {
  const el = document.getElementById('discover-estimate');
  el.innerHTML = '<p class="muted" style="font-size:13px">Kosten werden berechnet…</p>';
  const data = await api('/api/discover/estimate', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(discoverBody()),
  });
  if (!data || data.error) {
    el.innerHTML = '<div class="test-result error">' +
      escHtml((data && data.error) || 'Schätzung nicht möglich.') + '</div>';
    return;
  }

  const cap = discoverBody().max_spend;
  const over = data.estimated_cost > cap;
  el.innerHTML = '<div class="estimate-box">' +
    '<div class="amount ' + (data.free ? 'free' : 'paid') + '">' +
      (data.free ? 'Kostenlos' : '$' + data.estimated_cost.toFixed(4)) + '</div>' +
    '<div class="muted" style="font-size:13px;margin-top:2px">' +
      data.query_count + (data.query_count === 1 ? ' Suchanfrage' : ' Suchanfragen') +
      (data.free ? '' : ' &middot; Limit $' + cap.toFixed(2)) + '</div>' +
    (over ? '<div class="test-result error" style="margin-top:12px">' +
      'Über deinem Limit. Erhöhe das Limit oder grenze die Suche ein.</div>' : '') +
    '<div class="query-list">' +
      data.queries.slice(0, 40).map(escHtml).join('<br>') +
      (data.queries.length > 40 ? '<br>… und ' + (data.queries.length - 40) + ' weitere' : '') +
    '</div></div>';

  const btn = document.getElementById('disc-run');
  btn.disabled = over;
  btn.textContent = over ? 'Über dem Limit'
    : (data.free ? 'Starten (kostenlos)' : 'Starten, bis zu $' + data.estimated_cost.toFixed(2));
}

async function runDiscovery() {
  const btn = document.getElementById('disc-run');
  btn.disabled = true;
  btn.textContent = 'Läuft…';
  const data = await api('/api/discover/run', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(discoverBody()),
  });
  if (data && data.success) {
    showToast('Suche gestartet: ' + data.queries + ' Suchanfragen.', 'success');
  } else {
    showToast((data && data.message) || 'Start nicht möglich.', 'error');
    btn.disabled = false;
  }
  loadDiscoverProviders();
}

async function stopDiscovery() {
  const data = await api('/api/discover/stop', {method: 'POST'});
  showToast(data && data.success ? 'Stoppt nach der laufenden Suchanfrage.'
                                 : failMsg(data, 'Stoppen nicht möglich.'), data && data.success ? 'success' : 'error');
  loadDiscoverProviders();
}

// Companies found but not looked at: reading their sites is free and
// model-free, and it is what makes an email specific.
function renderProfileCard(pending, running) {
  const el = document.getElementById('discover-profile');
  if (!el) return;
  if (!pending) { el.innerHTML = ''; return; }
  el.innerHTML = '<div class="subhead">Websites lesen</div><div class="card"><div class="queue-item good" style="padding:0;border:0;box-shadow:none">' +
    '<div class="qtext"><div class="qtitle">' + pending + (pending === 1 ? ' Firma' : ' Firmen') +
    ' noch nicht angesehen</div><div class="qdetail">Kostenlos, kein Konto nötig. Harvey liest jede Website und schaut, ' +
    'welche Agentur sie betreut, was ihnen fehlt und ob sie Werbung schalten. Bis zu 200 pro Lauf.</div></div>' +
    '<button class="btn btn-primary btn-sm" onclick="runProfile(this)"' + (running ? ' disabled' : '') + '>' +
    (running ? 'Beschäftigt…' : 'Websites lesen') + '</button></div></div>';
}

async function runProfile(btn) {
  btn.disabled = true;
  btn.textContent = 'Startet…';
  const data = await api('/api/profile/run', {method: 'POST'});
  if (data && data.success) {
    showToast(data.pending ? Math.min(data.pending, 200) + ' Websites werden gelesen.' : 'Alle Websites sind schon gelesen.', 'success');
    if (data.pending && !_discoverPoll) _discoverPoll = setInterval(loadDiscoverProviders, 4000);
  } else {
    showToast(failMsg(data, 'Start nicht möglich.'), 'error');
  }
  loadDiscoverProviders();
}

function renderDiscoverResult(report, running) {
  const el = document.getElementById('discover-result');
  if (running) {
    el.innerHTML = '<div class="card"><h2>Läuft…</h2>' +
      '<p class="muted" style="font-size:13px">Harvey arbeitet die Suchanfragen ab. ' +
      'Ergebnisse landen unter Firmen, das Laufprotokoll steht unter Heute.</p></div>';
    return;
  }
  if (!report) { el.innerHTML = ''; return; }

  const stat = (label, value, tone) =>
    '<div class="stat-card"><div class="label">' + label + '</div>' +
    '<div class="value"' + (tone ? ' style="color:var(--' + tone + ')"' : '') + '>' +
    value + '</div></div>';

  if (report.profiled_companies !== undefined && report.new_companies === undefined) {
    el.innerHTML = '<div class="subhead">Letzter Lauf</div><div class="stats-grid">' +
      stat('Gelesene Websites', report.profiled_companies || 0, 'accent') +
      stat('Erfasste Fakten', report.profile_observations || 0) +
      stat('Kosten', 'kostenlos') + '</div>' +
      (report.stopped ? '<div class="test-result error" style="margin-top:14px">Vorzeitig gestoppt: ' + escHtml(report.stopped) + '</div>' : '');
    return;
  }
  el.innerHTML = '<div class="subhead">Letzter Lauf</div>' +
    '<div class="stats-grid">' +
      stat('Neue Firmen', report.new_companies || 0, 'accent') +
      stat('Schon bekannt', report.known_companies || 0) +
      stat('Beobachtungen', report.observations || 0) +
      stat('Als Müll aussortiert', report.junk || 0) +
      (report.profiled_companies ? stat('Gelesene Websites', report.profiled_companies) : '') +
      stat('Tatsächliche Kosten', report.actual_cost ? '$' + report.actual_cost.toFixed(4) : 'kostenlos') +
    '</div>' +
    (report.stopped ? '<div class="test-result error" style="margin-top:14px">' +
      'Vorzeitig gestoppt: ' + escHtml(report.stopped) + '</div>' : '') +
    ((report.errors || []).length
      ? '<div class="card" style="margin-top:14px"><h2>Probleme</h2>' +
        (report.errors || []).slice(0, 8).map(e =>
          '<div class="check-help">' + escHtml(e) + '</div>').join('') + '</div>'
      : '');
}

// ── Cohort builder: a prospect list is a query ──

let _cohort = { require: new Set(), exclude: new Set(), ranges: {} };
let _cohortOffset = 0;
let _cohortGeneration = 0;
const COHORT_PAGE = 50;

function renderCohortBuilder() {
  const el = document.getElementById('cohort-builder');
  if (!el || !_signals) return;

  const confirmed = (_signals.groups || [])
    .flatMap(g => g.signals)
    .filter(s => s.status === 'confirmed');

  if (!confirmed.length) {
    el.innerHTML = '<p class="muted" style="font-size:13px">' +
      'Bestätige oben einige Signale, dann werden sie hier zu Bausteinen.</p>';
    document.getElementById('cohort-result').innerHTML = '';
    return;
  }

  const col = (title, key, hint) =>
    '<div><div class="subhead" style="margin-top:0">' + title +
      ' <span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">' +
      hint + '</span></div><div class="cohort-grid">' +
    confirmed.map(s =>
      '<label class="cohort-pick"><input type="checkbox" ' +
        (_cohort[key].has(s.code) ? 'checked ' : '') +
        'onchange="toggleCohort(\'' + key + '\',' + jsq(s.code) + ',this.checked)">' +
        '<span>' + escHtml(s.label || s.code) + '</span>' +
        '<span class="n">' + (s.companies || 0) + '</span></label>'
    ).join('') + '</div></div>';

  // Numeric signals (ranks, ratings) take a range: "organic rank 11-30".
  const numeric = confirmed.filter(s => s.value_type === 'num');
  const bound = (code, side) => {
    const v = (_cohort.ranges[code] || {})[side];
    return '<input type="number" class="cohort-bound" placeholder="' + side + '" ' +
      'value="' + (v === undefined || v === null ? '' : escHtml(String(v))) + '" ' +
      'aria-label="' + escHtml(code) + ' ' + side + '" ' +
      'onchange="setCohortRange(' + jsq(code) + ',' + jsq(side) + ',this.value)">';
  };
  const rangeCol = numeric.length ?
    '<div style="height:18px"></div><div><div class="subhead" style="margin-top:0">Im Bereich ' +
      '<span class="muted" style="font-weight:400;text-transform:none;letter-spacing:0">' +
      'z. B. organischer Rang 11 bis 30; lass eine Seite leer für kein Limit</span></div>' +
      '<div class="cohort-grid">' +
      numeric.map(s =>
        '<div class="cohort-pick cohort-range"><span>' + escHtml(s.label || s.code) + '</span>' +
          bound(s.code, 'min') + '<span class="muted">bis</span>' + bound(s.code, 'max') + '</div>'
      ).join('') + '</div></div>' : '';

  el.innerHTML = col('Muss haben', 'require', 'jede Firma der Kohorte hat alle davon') +
    '<div style="height:18px"></div>' +
    col('Darf nicht haben', 'exclude', 'Ausschlusskriterien') + rangeCol;
}

function setCohortRange(code, side, value) {
  const r = _cohort.ranges[code] || {min: null, max: null};
  r[side] = value === '' || isNaN(Number(value)) ? null : Number(value);
  if (r.min === null && r.max === null) delete _cohort.ranges[code];
  else _cohort.ranges[code] = r;
  _cohortOffset = 0;
  runCohort();
}

function toggleCohort(key, code, on) {
  if (on) _cohort[key].add(code); else _cohort[key].delete(code);
  // A company can't be both required and excluded on the same signal.
  const other = key === 'require' ? 'exclude' : 'require';
  if (on) _cohort[other].delete(code);
  _cohortOffset = 0;  // a new cohort starts on its first page
  runCohort();
  renderCohortBuilder();
}

async function runCohort() {
  const generation = ++_cohortGeneration;
  const offset = _cohortOffset;
  const el = document.getElementById('cohort-result');
  if (!el) return;
  const require = [..._cohort.require], exclude = [..._cohort.exclude];
  const ranges = _cohortRanges();
  if (!require.length && !ranges.length) { el.innerHTML = ''; return; }
  // Remove the prior export button as soon as its criteria become stale.
  el.innerHTML = '<p class="muted">Kohorte wird geladen…</p>';

  const data = await api('/api/cohort', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({require: require, exclude: exclude, ranges: ranges,
                          offset: offset, limit: COHORT_PAGE}),
  });
  if (generation !== _cohortGeneration) return;
  if (!data || data.error) {
    el.innerHTML = data ? '<div class="test-result error">' + escHtml(data.error) + '</div>' : '';
    return;
  }

  let html = '<div style="border-top:1px solid var(--border);margin-top:20px;padding-top:20px">' +
    '<div class="cohort-size">' + data.size + '</div>' +
    '<div class="muted" style="font-size:13px;margin-top:2px">' +
      (data.size === 1 ? 'Firma passt' : 'Firmen passen') + ' gerade zu dieser Kohorte</div>';

  if (data.companies && data.companies.length) {
    html += '<div class="table-card" style="margin-top:16px"><table><thead><tr>' +
      '<th>Firma</th><th>Domain</th><th>Branche</th><th>Ort</th>' +
      '</tr></thead><tbody>' +
      data.companies.map(c =>
        '<tr><td>' + escHtml(c.name) + '</td><td class="muted">' + escHtml(c.domain) + '</td>' +
        '<td class="muted">' + escHtml(industryLabel(c.industry)) + '</td>' +
        '<td class="muted">' + escHtml(c.location) + '</td></tr>'
      ).join('') + '</tbody></table></div>';

    const first = data.offset + 1, last = data.offset + data.companies.length;
    const hasPrev = data.offset > 0, hasNext = last < data.size;
    html += '<div style="display:flex;align-items:center;gap:8px;margin-top:10px">' +
      '<span class="muted" style="font-size:12px">Zeige ' + first + ' bis ' + last +
        ' von ' + data.size + '</span>' +
      '<span style="flex:1"></span>' +
      '<button class="btn btn-secondary btn-sm" onclick="pageCohort(-1)"' +
        (hasPrev ? '' : ' disabled') + '>Zurück</button>' +
      '<button class="btn btn-secondary btn-sm" onclick="pageCohort(1)"' +
        (hasNext ? '' : ' disabled') + '>Weiter</button>' +
      '<button class="btn btn-secondary btn-sm" onclick="exportCohort(this)">CSV exportieren</button>' +
      '</div>';
  } else if (data.size === 0) {
    html += '<p class="muted" style="font-size:13px;margin-top:12px">' +
      'Noch keine Firma hat alle diese Signale. Lockere die Kohorte oder ' +
      'sammle mit der Kontaktsuche mehr.</p>';
  } else if (_cohortOffset > 0) {
    // The cohort shrank under us (a re-profile, a delete): back to page one.
    _cohortOffset = 0;
    return runCohort();
  }
  el.innerHTML = html + '</div>';
}

function _cohortRanges() {
  return Object.entries(_cohort.ranges || {})
    .filter(([, r]) => r.min !== null || r.max !== null)
    .map(([code, r]) => ({code: code, min: r.min, max: r.max}));
}

async function exportCohort(button) {
  const qs = new URLSearchParams({require: [..._cohort.require].join(','),
                                  exclude: [..._cohort.exclude].join(',')});
  // Ranges travel as "CODE:min:max", a blank side meaning no limit.
  const ranges = Object.entries(_cohort.ranges || {})
    .map(([code, r]) => code + ':' + (r.min ?? '') + ':' + (r.max ?? ''));
  if (ranges.length) qs.set('ranges', ranges.join(','));
  button.disabled = true;
  button.textContent = 'Exportiert…';
  let url;
  let link;
  try {
    const response = await authFetch('/api/cohort.csv?' + qs);
    if (await lockIfRefused(response)) throw new Error('Dashboard-Freigabe nötig');
    if (!response.ok) throw new Error('Export fehlgeschlagen (HTTP ' + response.status + ')');
    url = URL.createObjectURL(await response.blob());
    link = document.createElement('a');
    link.href = url;
    link.download = 'cohort.csv';
    document.body.appendChild(link);
    link.click();
    button.textContent = 'CSV exportieren';
  } catch (error) {
    button.textContent = 'Export fehlgeschlagen, erneut versuchen';
  } finally {
    if (link) link.remove();
    // Give the browser time to start consuming the download before revoking.
    if (url) setTimeout(() => URL.revokeObjectURL(url), 1000);
    button.disabled = false;
  }
}

function pageCohort(dir) {
  _cohortOffset = Math.max(0, _cohortOffset + dir * COHORT_PAGE);
  runCohort();
}

// ── Settings ──

async function loadSettings() {
  const data = await api('/api/settings');
  if (!data) return;

  // Active provider indicator
  const prov = data.provider || 'instantly';
  const provEl = document.getElementById('active-provider');
  if (provEl) provEl.textContent = prov;

  // Secret fields never echo a value — show a "saved" placeholder instead.
  const savedPh = (id, isSet, base) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.value = '';
    el.placeholder = isSet ? 'Gespeichert. Zum Ändern neuen Wert eingeben' : base;
  };
  const tag = (id, ok, okLabel) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.className = 'email-tag ' + (ok ? 'verified' : 'guess');
    el.textContent = ok ? okLabel : 'nicht gesetzt';
  };

  // Non-secret values repopulate
  document.getElementById('gmail-id').value = data.gmail_client_id || '';
  document.getElementById('smtp-host').value = data.smtp_host || '';
  document.getElementById('smtp-port').value = data.smtp_port || '';
  document.getElementById('smtp-user').value = data.smtp_username || '';
  document.getElementById('imap-host').value = data.imap_host || '';
  document.getElementById('imap-port').value = data.imap_port || '';
  document.getElementById('imap-user').value = data.imap_username || '';
  document.getElementById('m365-client').value = data.m365_client_id || '';
  document.getElementById('m365-tenant').value = data.m365_tenant_id || '';
  document.getElementById('linkedin-email').value = data.linkedin_email || '';
  document.getElementById('cf-account-id').value = data.cloudflare_account_id || '';

  savedPh('gmail-secret', data.gmail_client_secret_set, 'Client-Secret eingeben');
  savedPh('smtp-pass', data.smtp_password_set, 'Passwort oder App-Passwort eingeben');
  savedPh('imap-pass', data.imap_password_set, 'Wie das SMTP-Passwort');
  document.getElementById('imap-same-pass').checked = !data.imap_password_set;
  savedPh('instantly-key', data.instantly_api_key_set, 'Deinen Instantly-API-Schlüssel eingeben');
  savedPh('reoon-key', data.reoon_api_key_set, '600 gratis pro Monat · reoon.com/email-verifier');
  savedPh('zerobounce-key', data.zerobounce_api_key_set, '100 gratis pro Monat · am besten für Catch-alls bei M365/Workspace');
  savedPh('hunter-key', data.hunter_api_key_set, '50 gratis pro Monat + Suche nach E-Mail-Mustern');
  savedPh('linkedin-password', data.linkedin_password_set, 'Passwort eingeben');
  savedPh('cf-api-token', data.cloudflare_api_token_set, 'Dein Cloudflare-API-Token');

  // Gmail auth status chip
  const g = document.getElementById('gmail-status');
  if (g) {
    if (data.gmail_authorized) { g.className = 'email-tag verified'; g.textContent = 'autorisiert'; }
    else if (data.gmail_client_secret_set) { g.className = 'email-tag risky'; g.textContent = 'ausführen: harvey gmail auth'; }
    else { g.className = 'email-tag guess'; g.textContent = 'nicht eingerichtet'; }
  }
  const m = document.getElementById('m365-status');
  if (m) {
    if (data.m365_authorized) { m.className = 'email-tag verified'; m.textContent = 'autorisiert'; }
    else if (data.m365_client_id) { m.className = 'email-tag risky'; m.textContent = 'ausführen: harvey microsoft auth'; }
    else { m.className = 'email-tag guess'; m.textContent = 'nicht eingerichtet'; }
  }
  tag('reoon-status', data.reoon_api_key_set, 'gesetzt');
  tag('zerobounce-status', data.zerobounce_api_key_set, 'gesetzt');
  tag('hunter-status', data.hunter_api_key_set, 'gesetzt');
}

function saveGmail() {
  const payload = {GMAIL_CLIENT_ID: document.getElementById('gmail-id').value.trim()};
  const sec = document.getElementById('gmail-secret').value;
  if (sec) payload.GMAIL_CLIENT_SECRET = sec;
  saveEnv(payload, 'Gmail-OAuth gespeichert. Führe jetzt „harvey gmail auth“ im Terminal aus.').then(loadSettings);
}

function saveSmtp() {
  const payload = {
    SMTP_HOST: document.getElementById('smtp-host').value.trim(),
    SMTP_PORT: document.getElementById('smtp-port').value.trim(),
    SMTP_USERNAME: document.getElementById('smtp-user').value.trim(),
  };
  const pass = document.getElementById('smtp-pass').value;
  if (pass) payload.SMTP_PASSWORD = pass;
  // Blank IMAP fields fall back to the SMTP ones. A typed IMAP password wins;
  // otherwise the checkbox clears it back to the SMTP one, and an unticked
  // empty box keeps whatever IMAP password is saved.
  payload.IMAP_HOST = document.getElementById('imap-host').value.trim();
  payload.IMAP_PORT = document.getElementById('imap-port').value.trim();
  payload.IMAP_USERNAME = document.getElementById('imap-user').value.trim();
  const imapPass = document.getElementById('imap-pass').value;
  if (imapPass) payload.IMAP_PASSWORD = imapPass;
  else if (document.getElementById('imap-same-pass').checked) payload.IMAP_PASSWORD = '';
  saveEnv(payload, 'SMTP-Einstellungen gespeichert.').then(loadSettings);
}

function saveMicrosoft() {
  const client = document.getElementById('m365-client').value.trim();
  if (!client) { showToast('Füge zuerst die Anwendungs-ID (Client) ein.', 'error'); return; }
  saveEnv({M365_CLIENT_ID: client, M365_TENANT_ID: document.getElementById('m365-tenant').value.trim()},
          'Gespeichert. Führe jetzt „harvey microsoft auth“ im Terminal aus.').then(loadSettings);
}

function saveVerifiers() {
  const payload = {};
  const r = document.getElementById('reoon-key').value;
  const z = document.getElementById('zerobounce-key').value;
  const h = document.getElementById('hunter-key').value;
  if (r) payload.REOON_API_KEY = r;
  if (z) payload.ZEROBOUNCE_API_KEY = z;
  if (h) payload.HUNTER_API_KEY = h;
  if (!Object.keys(payload).length) { showToast('Gib zuerst mindestens einen Schlüssel ein.', 'error'); return; }
  saveEnv(payload, 'Prüfschlüssel gespeichert.').then(loadSettings);
}

async function saveEnv(payload, okMsg) {
  const data = await api('/api/settings/env', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(payload)
  });
  if (data && data.success) showToast(okMsg, 'success');
  else showToast((data && data.message) || 'Speichern fehlgeschlagen. Läuft das Dashboard noch?', 'error');
}

// Secrets are only sent when typed: an empty box means "keep the saved one".
function saveInstantly() {
  const key = document.getElementById('instantly-key').value.trim();
  if (!key) { showToast('Gib zuerst einen neuen Schlüssel ein. Der gespeicherte bleibt erhalten.', 'error'); return; }
  saveEnv({INSTANTLY_API_KEY: key}, 'Instantly-API-Schlüssel gespeichert.');
}

function saveLinkedIn() {
  const payload = {LINKEDIN_EMAIL: document.getElementById('linkedin-email').value.trim()};
  const pass = document.getElementById('linkedin-password').value;
  if (pass) payload.LINKEDIN_PASSWORD = pass;
  saveEnv(payload, 'LinkedIn-Zugangsdaten gespeichert.');
}

function saveCloudflare() {
  const payload = {CLOUDFLARE_ACCOUNT_ID: document.getElementById('cf-account-id').value.trim()};
  const token = document.getElementById('cf-api-token').value.trim();
  if (token) payload.CLOUDFLARE_API_TOKEN = token;
  saveEnv(payload, 'Cloudflare-Zugangsdaten gespeichert.');
}

async function testInstantly() {
  const key = document.getElementById('instantly-key').value.trim();
  const el = document.getElementById('instantly-test-result');
  el.innerHTML = '<div class="test-result pending">Teste&hellip;</div>';
  const data = await api('/api/settings/test-instantly', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({api_key: key})
  });
  if (!data) {
    el.innerHTML = '<div class="test-result error">Dashboard-Server nicht erreichbar.</div>';
    return;
  }
  el.innerHTML = '<div class="test-result ' + (data.success ? 'success' : 'error') + '">' + escHtml(failMsg(data, '')) + '</div>';
}

// ── Controls ──

async function loadHarveyStatus() {
  const data = await api('/api/harvey/status');
  const headerDot = document.getElementById('header-dot');
  const headerText = document.getElementById('header-status-text');

  if (_locked) return;  // the header already says "Gesperrt"
  if (!data) {
    headerDot.className = 'status-dot offline';
    headerText.textContent = 'Offline';
    return;
  }
  const running = !!data.running;

  headerDot.className = 'status-dot ' + (running ? 'running' : 'stopped');
  headerText.textContent = running ? 'Harvey läuft' : 'Harvey ist gestoppt';
  document.getElementById('control-dot').className = 'dot ' + (running ? 'running' : 'stopped');
  const label = document.getElementById('control-label');
  label.className = 'label ' + (running ? 'running' : 'stopped');
  label.textContent = running ? 'Läuft' : 'Gestoppt';

  const meta = document.getElementById('control-meta');
  if (running && data.pid) {
    let info = 'PID ' + escHtml(String(data.pid));
    if (data.started_at) info += ' &middot; gestartet ' + formatDate(data.started_at);
    meta.innerHTML = info;
  } else {
    meta.innerHTML = 'Harvey wacht alle paar Minuten auf, erledigt, was ansteht, und schläft wieder.';
  }

  document.getElementById('btn-start').style.display = running ? 'none' : '';
  document.getElementById('btn-stop').style.display = running ? '' : 'none';
}

async function startHarvey() {
  const btn = document.getElementById('btn-start');
  btn.disabled = true;
  const data = await api('/api/harvey/start', {method: 'POST'});
  if (data && data.success) showToast('Harvey gestartet.', 'success');
  else showToast((data && data.message) || 'Start fehlgeschlagen.', 'error');
  btn.disabled = false;
  loadHarveyStatus();
}

async function stopHarvey() {
  const btn = document.getElementById('btn-stop');
  btn.disabled = true;
  const data = await api('/api/harvey/stop', {method: 'POST'});
  if (data && data.success) showToast('Harvey gestoppt.', 'success');
  else showToast((data && data.message) || 'Stoppen fehlgeschlagen.', 'error');
  btn.disabled = false;
  loadHarveyStatus();
}

async function loadLogs() {
  const data = await api('/api/harvey/logs');
  const el = document.getElementById('log-viewer');
  if (data && data.lines && data.lines.length) {
    const stick = el.scrollTop + el.clientHeight >= el.scrollHeight - 30;
    el.textContent = data.lines.join('\n');
    if (stick) el.scrollTop = el.scrollHeight;
  } else {
    el.textContent = 'Noch keine Logs. Starte Harvey, um Aktivität zu sehen.';
  }
}

// ── Pipeline data ──

// Calls today per subscription — each labelled with the provider it ran on.
function brainCallChips(data) {
  const chip = (label, n) => '<span class="chip">' + label + ' Aufrufe heute <b>' + escHtml(String(n || 0)) + '</b></span>';
  const codex = data.codex_calls_today || 0;
  const st = data.brain_status || {};
  // A paused brain must not look healthy: say so, and why.
  const paused = st.status === 'paused'
    ? '<span class="chip" title="' + escHtml(st.reason || '') + '">' +
      escHtml(st.provider === 'codex' ? 'Codex' : 'Modell') + ' <b>pausiert</b>: ' +
      escHtml((st.reason || '').slice(0, 120)) + '</span>'
    : '';
  if (data.brain_provider === 'codex') {
    return paused + chip('Codex', codex) + (data.claude_calls_today ? chip('Claude', data.claude_calls_today) : '');
  }
  return paused + chip('Claude', data.claude_calls_today) + (codex ? chip('Codex', codex) : '');
}

function fmtTokens(n) {
  n = n || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(1).replace('.', ',') + ' Mrd.';
  if (n >= 1e6) return (n / 1e6).toFixed(1).replace('.', ',') + ' Mio.';
  if (n >= 1e3) return (n / 1e3).toFixed(1).replace('.', ',') + ' Tsd.';
  return String(n);
}


function emailTag(p) {
  if (!p.email) return '';
  // Fall back to the legacy boolean for rows predating email_status.
  const status = p.email_status || (p.email_verified ? 'verified' : 'guess');
  if (!own(STATUS, status)) return '';
  const m = statusMeta(status);
  return ' <span class="mini t-' + m.tone + '" title="E-Mail: ' + escHtml(m.label) + '">' +
    escHtml(m.label) + '</span>';
}

// Quota gauges for the ACTIVE provider only: Claude's are the numbers
// `/usage` shows in Claude Code; Codex's are the ChatGPT plan's limits.
// When they can't be read, say so rather than hiding the card.
function quotaCardHtml(data) {
  const provider = data && data.quota_provider;
  if (!provider) return '';
  const title = provider === 'codex' ? 'Codex-Kontingent (ChatGPT-Plan)' : 'Claude-Abo-Kontingent';
  let html = '<h2>' + escHtml(title) + '</h2>';
  if (!data.quota || !Object.keys(data.quota).length) {
    const why = provider === 'codex'
      ? 'Kontingent nicht verfügbar: Harvey konnte die Limits deines ChatGPT-Plans nicht lesen (prüfe codex login, die Codex-CLI-Version und das Netzwerk).'
      : 'Kontingent nicht verfügbar: Harvey konnte die Limits deines Claude-Abos nicht lesen (prüfe claude login und das Netzwerk).';
    return html + '<p class="muted">' + escHtml(why) + ' Bis dahin zählen die Budgets Harveys eigene Aufrufe.</p>';
  }
  const labels = {five_hour: '5-Stunden-Fenster', seven_day: 'Wöchentlich', primary: 'Primäres Fenster',
                  secondary: 'Sekundäres Fenster', limit_reached: 'Nutzungslimit erreicht'};
  for (const [key, w] of Object.entries(data.quota)) {
    const pct = Math.min(100, Math.max(0, w.utilization || 0));
    const color = pct >= 80 ? 'yellow' : 'green';
    const resets = w.resets_at ? 'Reset ' + formatDate(w.resets_at) : '';
    html += '<div class="progress-wrap">' +
      '<div class="progress-label">' +
        '<span class="text">' + escHtml(labels[key] || key) + (resets ? ' &middot; ' + escHtml(resets) : '') + '</span>' +
        '<span class="pct">' + pct.toFixed(0) + '%</span>' +
      '</div>' +
      '<div class="progress-bar"><div class="progress-fill ' + color + '" style="width:' + pct + '%"></div></div>' +
    '</div>';
  }
  return html;
}

async function loadUsage() {
  const data = await api('/api/usage');
  const statsEl = document.getElementById('usage-stats');
  if (!data) { statsEl.innerHTML = offlineState(); return; }

  const quotaEl = document.getElementById('usage-quota');
  const qHtml = quotaCardHtml(data);
  quotaEl.innerHTML = qHtml;
  quotaEl.style.display = qHtml ? 'block' : 'none';

  // Totals cards — usage-first (calls is the headline; tokens as chips).
  // No dollars: on a subscription plan usage isn't billed per token.
  const t = data.totals || {};
  const card = (label, p) => {
    p = p || {};
    return '<div class="stat-card"><div class="label">' + label + '</div>' +
      '<div class="value">' + (p.calls || 0) + '</div>' +
      '<div class="breakdown">' +
        '<span class="chip">Aufrufe</span>' +
        '<span class="chip">aus <b>' + fmtTokens(p.output_tokens) + '</b></span>' +
        '<span class="chip">ein <b>' + fmtTokens(p.input_tokens) + '</b></span>' +
        '<span class="chip">Cache <b>' + fmtTokens(p.cache_read_tokens) + '</b></span>' +
      '</div></div>';
  };
  statsEl.innerHTML = card('Heute', t.today) + card('Letzte 7 Tage', t.week) + card('Letzte 30 Tage', t.month);

  // Daily columns — output tokens per day (the work done)
  const dailyEl = document.getElementById('usage-daily');
  const days = (data.by_day || []).slice(-30);
  if (days.length) {
    const maxOut = Math.max(...days.map(d => d.output_tokens || 0), 1);
    dailyEl.innerHTML = '<h2>Ausgabe-Tokens pro Tag <span class="hint muted" style="font-weight:400">letzte ' +
      days.length + ' Tage · Spitze ' + fmtTokens(maxOut) + '</span></h2>' +
      '<div class="chart-cols" role="img" aria-label="Ausgabe-Tokens pro Tag">' + days.map(d =>
        '<span class="col" title="' + escHtml(d.day || '') + ': ' + fmtTokens(d.output_tokens) + ' aus, ' +
          (d.calls || 0) + ' Aufrufe"><i style="height:' + Math.max(2, (d.output_tokens || 0) / maxOut * 100) + '%"></i></span>'
      ).join('') + '</div>' +
      '<div class="chart-axis"><span>' + escHtml(days[0].day || '') + '</span><span>' +
        escHtml(days[days.length - 1].day || '') + '</span></div>';
    dailyEl.style.display = 'block';
  } else {
    dailyEl.style.display = 'none';
  }

  // Breakdown tables — calls + tokens, no cost column
  const tablesEl = document.getElementById('usage-tables');
  const table = (title, rows, keyName, label) => {
    if (!rows || !rows.length) return '';
    const max = Math.max(...rows.map(r => r.calls || 0), 1);
    let h = '<div class="card flush"><div class="card-head"><h3>' + title + '</h3></div><table><thead><tr>' +
      '<th>' + (label || keyName) + '</th><th>Aufrufe</th><th class="num">Eingabe</th><th class="num">Ausgabe</th><th class="num">Cache gelesen</th>' +
      '</tr></thead><tbody>';
    for (const r of rows) {
      h += '<tr><td class="cell-main">' + escHtml(String(r[keyName.toLowerCase()] || '')) + '</td>' +
        '<td><span class="meter-row"><span class="bar" style="width:120px"><span style="width:' +
          (100 * (r.calls || 0) / max) + '%;background:var(--accent)"></span></span><span class="v">' + (r.calls || 0) + '</span></span></td>' +
        '<td class="num muted">' + fmtTokens(r.input_tokens) + '</td>' +
        '<td class="num">' + fmtTokens(r.output_tokens) + '</td>' +
        '<td class="num muted">' + fmtTokens(r.cache_read_tokens) + '</td></tr>';
    }
    return h + '</tbody></table></div>';
  };

  const anyRows = (data.by_agent || []).length || (data.by_task || []).length;
  if (!anyRows) {
    tablesEl.innerHTML = emptyState(icon('usage'), 'Noch keine Nutzung erfasst',
      'Sobald Harvey das Modell aufruft (Claude oder Codex), wird jeder Aufruf hier erfasst, mit genauen Aufrufen und Tokens nach Anbieter, Agent und Aufgabe.');
  } else {
    const providers = data.by_provider || [];
    const showProviders = providers.length > 1 || (providers.length === 1 && providers[0].provider !== 'claude');
    tablesEl.innerHTML =
      (showProviders ? table('Nach Anbieter (30 Tage)', providers, 'Provider', 'Anbieter') : '') +
      table('Nach Agent (30 Tage)', data.by_agent, 'Agent', 'Agent') +
      table('Nach Aufgabe (30 Tage)', data.by_task, 'Task', 'Aufgabe') +
      table('Nach Modell (30 Tage)', data.by_model, 'Model', 'Modell');
  }
}

// ── Outbox: a decisions desk, not a wall of drafts ──
//
// Approving mail is a queue of one-at-a-time judgements. Showing all of them
// stacked invites a single "approve all" reflex, which is exactly the review
// the approval ladder exists to prevent. One email fills the pane; the rest
// wait in the rail.

async function loadOutbox() {
  const [data, mode] = await Promise.all([api('/api/outbox'), api('/api/sending-mode')]);
  _sendingMode = mode && !mode.error ? mode : null;
  const banner = document.getElementById('outbox-banner');
  const desk = document.getElementById('outbox-desk');
  const list = document.getElementById('outbox-list');
  const actions = document.getElementById('outbox-actions');
  if (!data) { desk.innerHTML = offlineState(); list.innerHTML = ''; return; }
  if (data.error) {
    // Never let a failed read pass for "Nothing to review".
    desk.innerHTML = emptyState(icon('alert'), 'Postausgang konnte nicht gelesen werden', escHtml(data.error));
    list.innerHTML = '';
    return;
  }

  const pending = data.pending || [];
  _desk.items = pending;
  _desk.footer = data.footer || '';
  // Keep the desk on the draft being edited even if the list shifted.
  const editingAt = _desk.editing ? pending.findIndex(p => p.id === _desk.editing) : -1;
  if (editingAt >= 0) _desk.i = editingAt;
  else if (_desk.editing) {
    _desk.editing = null;
    _desk.unsaved = null;
  }
  if (_desk.i >= pending.length) _desk.i = Math.max(0, pending.length - 1);
  navCount('nav-outbox', pending.length);

  actions.innerHTML =
    (pending.length && !data.paused
      ? (pending.length > pending.filter(p => heldReason(p)).length
        ? '<button class="btn btn-primary btn-sm" onclick="outboxApproveAll()" ' +
          'title="E-Mails, die Harvey zurückgehalten hat, gibst du einzeln frei">Alle ' +
          (pending.length - pending.filter(p => heldReason(p)).length) + ' freigeben</button>' : '') : '') +
    (data.paused
      ? '<button class="btn btn-secondary btn-sm" onclick="sendingToggle(\'resume\')">' +
        'Versand fortsetzen</button>'
      : '<button class="btn btn-secondary btn-sm" onclick="sendingToggle(\'pause\')">' +
        'Gesamten Versand pausieren</button>');

  // The kill switch gets a banner only when it's actually on — a permanent
  // bar for a thing that isn't happening is just noise.
  banner.innerHTML = sendingModeBanner(_sendingMode) + (data.paused
    ? '<div class="banner bad">' + icon('alert') + '<div><b>Versand pausiert</b>' +
        '<p>' + escHtml(data.paused) + '. Freigegebene E-Mails bleiben in der Warteschlange, bis du fortsetzt.</p></div></div>'
    : '');
  // Only once there is mail to warn about: a fresh install sends nothing.
  const anyMail = pending.length || (data.approved || []).length || (data.sent || []).length;
  if (data.compliance_notice && anyMail) {
    banner.innerHTML +=
      '<div class="banner wait">' + icon('alert') + '<div><b>Absenderangaben fehlen</b>' +
        '<p>' + escHtml(data.compliance_notice) + '</p></div></div>';
  }

  desk.innerHTML = pending.length ? renderDesk(pending, _desk.i) :
    '<div class="card">' + emptyState(icon('check'), 'Nichts zu prüfen',
      _sendingMode && _sendingMode.mode === 'autopilot'
        ? 'Der Autopilot ist an, Harvey sendet also selbst. Alles, was er für dich zurückhält, erscheint hier.'
        : 'Jeder Entwurf von Harvey landet zuerst hier. Gib einen frei, dann geht er nach Plan raus.') +
    '</div>';

  const table = (title, rows, cols) => {
    if (!rows || !rows.length) return '';
    let h = '<div class="subhead">' + title + ' <span class="hint">' + rows.length + '</span></div>' +
      '<div class="table-card"><table class="nowrap clip"><thead><tr>' +
      cols.map(c => '<th>' + c[0] + '</th>').join('') + '</tr></thead><tbody>';
    for (const r of rows) {
      h += '<tr>' + cols.map(c => '<td' + (c[2] ? ' class="muted"' : '') + '>' +
        (c[3] ? c[1](r) : escHtml(String(c[1](r) ?? ''))) + '</td>').join('') + '</tr>';
    }
    return h + '</tbody></table></div>';
  };

  list.innerHTML =
    table('Freigegeben und geplant', data.approved, [
      ['An', r => r.to_email], ['Schritt', r => r.step], ['Betreff', r => r.subject],
      ['Versand', r => r.status === 'sending' ? badge(r.status) : escHtml(formatDate(r.send_at)), true, true],
    ]) +
    table('Zuletzt gesendet', data.sent, [
      ['An', r => r.to_email], ['Schritt', r => r.step], ['Betreff', r => r.subject],
      ['Gesendet', r => formatDate(r.sent_at), true],
    ]) +
    table('Nicht gesendet', data.failed, [
      ['An', r => r.to_email], ['Status', r => badge(r.status), false, true],
      ['Grund', r => r.error, true], ['Aktualisiert', r => formatDate(r.updated_at), true],
    ]);
}

const CONTACT_SOURCES = {
  known_company_backfill: 'Teamseite auf ihrer Website',
  company_website: 'Teamseite auf ihrer Website',
  scraped_mailto: 'auf ihrer Website veröffentlichte Adresse',
  linkedin_search: 'LinkedIn-Suche',
  hunter_domain_search: 'Hunter-Domainsuche',
  job_board: 'Jobbörse',
  web_search: 'Websuche',
  company_inbox: 'Allgemeines Postfach der Firma',
};

// What the draft is built on, so every claim can be checked against it.
function renderDeskContext(ctx) {
  if (!ctx) return '';
  const row = (k, v) => v ? '<div class="k">' + k + '</div><div class="v">' + v + '</div>' : '';
  const href = safeHref(ctx.website);
  const site = href
    ? '<a href="' + href + '" target="_blank" rel="noopener noreferrer">' +
        escHtml(ctx.website.replace(/^https?:\/\//i, '')) + ' &#8599;</a>'
    : '';
  const who = [ctx.contact, titleLabel(ctx.contact_title)].filter(Boolean).map(escHtml).join(' &middot; ');
  const email = [ctx.email_status ? statusMeta(ctx.email_status).label : '', ctx.score ? 'Score ' + ctx.score : '']
    .filter(Boolean).map(escHtml).join(' &middot; ');
  const signals = Object.entries(ctx.signals || {})
    .map(([k, v]) => escHtml(k) + ': <b>' + escHtml(v) + '</b>').join('<br>');
  return '<div class="desk-context">' +
    row('Firma', escHtml(ctx.company)) +
    row('Website', site) +
    row('Über', escHtml(ctx.description)) +
    row('Kontakt', who) +
    row('Gefunden über', escHtml(Object.prototype.hasOwnProperty.call(CONTACT_SOURCES, ctx.contact_source)
      ? CONTACT_SOURCES[ctx.contact_source] : ctx.contact_source)) +
    row('E-Mail', email) +
    row('Signale', signals) +
    row('Kampagne', ctx.campaign ? escHtml(campaignLabel(ctx.campaign)) +
      ' <span class="muted">(wie sie gefunden wurden, keine Aussage über sie)</span>' : '') +
    row('Notizen des Writers', escHtml(ctx.notes)) +
  '</div>';
}

// ── Approval or autopilot ──
// Approval (the default): every email waits in the Outbox for you.
// Autopilot (channels.email.require_approval: false): Harvey sends on its
// own, and still holds what gate.autopilot_review flags. Turning autopilot
// on takes typing the word; turning it off is one click and works at once.
let _sendingMode = null, _autopilotArmed = false, _modeBusy = false;

function sendingModeBanner(m) {
  if (!m) return '';
  if (m.mode === 'autopilot') {
    return '<div class="banner wait mode-banner">' + icon('alert') + '<div><b>Autopilot ist an</b>' +
      '<p>Harvey sendet E-Mails selbst. Er hält alles zurück, was auf eine fremde Website verlinkt oder ' +
      'seine eigenen Anweisungen zitiert; das wartet hier' + (m.held ? ' (' + m.held + ' gerade)' : '') + '.</p></div>' +
      '<button class="btn btn-secondary btn-sm" onclick="setSendingMode(\'approval\')"' +
        (_modeBusy ? ' disabled' : '') + '>Autopilot ausschalten</button></div>';
  }
  const instantly = m.provider === 'instantly';
  return '<div class="banner mode-banner">' + icon('check') + '<div><b>Freigabemodus</b>' +
    '<p>' + (instantly
      ? 'Instantly kann keine einzelnen E-Mails zurückhalten. Kampagnen gehen deshalb erst an Instantly, wenn du den Autopilot einschaltest.'
      : 'Jede E-Mail von Harvey wartet hier, bis du sie freigibst.') +
    ' <a href="#" onclick="goTab(\'settings\');return false">Zum Autopilot</a></p></div></div>';
}

function sendingModeCard(m) {
  if (!m) return '<div class="card muted">Versandmodus konnte nicht gelesen werden.</div>';
  const auto = m.mode === 'autopilot';
  const instantly = m.provider === 'instantly';
  const domains = (m.your_domains || []).map(escHtml).join(', ') || 'noch keine';
  let html = '<div class="card mode-card"><h3>Versandmodus: ' + (auto ? 'Autopilot' : 'Freigabe') + '</h3>' +
    '<p><b>Freigabe</b> (Standard): Jede E-Mail von Harvey, ob erste E-Mail, Follow-up oder Antwort, ' +
      'wartet im Postausgang, bis du sie freigibst.' +
      (instantly ? ' Bei Instantly, das keine einzelnen E-Mails zurückhalten kann, heißt das: Kampagnen werden nicht gestartet.' : '') + '</p>' +
    '<p><b>Autopilot</b>: Harvey sendet selbst, nach Plan, innerhalb deines Tageslimits und außerhalb der Ruhezeiten. ' +
      'Abmeldungen, Unzustellbare und „Gesamten Versand pausieren“ funktionieren wie gewohnt. Eine E-Mail hält er ' +
      'trotzdem für dich zurück, wenn sie auf eine fremde Website verlinkt oder dort eine Adresse nennt, oder wenn sie ' +
      'Harveys eigene Anweisungen zitiert: Text von Websites und aus Antworten kann versuchen, zu steuern, was er schreibt. ' +
      'Auch die erste Antwort, nachdem du eine Abmeldung zurückgenommen hast, wartet.</p>' +
    '<p class="muted">Zählt als deine: ' + domains + (m.booking_page ? '; deine Buchungsseite ' + escHtml(m.booking_page) : '') +
      '. Weitere trägst du unter <code>channels.email.link_domains</code> in <code>' + escHtml(m.file) + '</code> ein.</p>';
  if (auto) {
    html += '<div class="convo-actions"><span class="muted">Ausschalten wirkt sofort: E-Mails, die der Autopilot ' +
      'freigegeben hat und die noch nicht raus sind, kommen zurück in den Postausgang.' +
      (instantly ? ' Kampagnen, die schon bei Instantly sind, laufen weiter, bis du sie dort pausierst.' : '') + '</span>' +
      '<button class="btn btn-primary btn-sm" onclick="setSendingMode(\'approval\')"' +
        (_modeBusy ? ' disabled' : '') + '>Autopilot ausschalten</button></div>';
  } else if (_autopilotArmed) {
    html += '<div class="convo-actions mode-confirm"><span class="muted">Gib zuerst ein, zwei Stapel von Hand frei ' +
      'und lies, was Harvey schreibt. Tippe <b>autopilot</b>, um ihn einzuschalten. Er startet mit Harveys nächstem Durchlauf.</span>' +
      '<input class="form-input" id="autopilot-confirm" autocomplete="off" aria-label="Zum Bestätigen autopilot tippen" ' +
        'oninput="document.getElementById(\'autopilot-go\').disabled = this.value.trim().toLowerCase() !== \'autopilot\'" ' +
        'onkeydown="if (event.key === \'Enter\' && this.value.trim().toLowerCase() === \'autopilot\') setSendingMode(\'autopilot\')">' +
      '<button class="btn btn-primary btn-sm" id="autopilot-go" disabled onclick="setSendingMode(\'autopilot\')">Autopilot einschalten</button>' +
      '<button class="btn btn-ghost btn-sm" onclick="_autopilotArmed=false;renderSendingModeCard()">Abbrechen</button></div>';
  } else {
    html += '<div class="convo-actions"><button class="btn btn-secondary btn-sm" ' +
      'onclick="_autopilotArmed=true;renderSendingModeCard()">Autopilot einschalten…</button></div>';
  }
  return html + '</div>';
}

function renderSendingModeCard() {
  const el = document.getElementById('sending-mode-card');
  if (el) el.innerHTML = sendingModeCard(_sendingMode);
  const input = document.getElementById('autopilot-confirm');
  if (input) input.focus();
}

async function loadSendingModeCard() {
  const m = await api('/api/sending-mode');
  _sendingMode = m && !m.error ? m : null;
  renderSendingModeCard();
}

async function setSendingMode(mode) {
  if (_modeBusy) return;
  const body = {mode};
  if (mode === 'autopilot') {
    const input = document.getElementById('autopilot-confirm');
    body.confirm = input ? input.value.trim().toLowerCase() : '';
  }
  _modeBusy = true;
  document.querySelectorAll('.mode-banner button, .mode-card button').forEach(b => { b.disabled = true; });
  try {
    const res = await api('/api/sending-mode', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    if (res && res.success) {
      _autopilotArmed = false;
      showToast(mode === 'autopilot'
        ? 'Der Autopilot ist ab Harveys nächstem Durchlauf an. Zurückgehaltene E-Mails warten weiter auf dich.'
        : 'Der Autopilot ist aus. Jede E-Mail wartet auf dich' +
          (res.requeued ? '; ' + res.requeued + ' vom Autopilot eingereihte kamen zurück in den Postausgang.' : '.') +
          (_sendingMode && _sendingMode.provider === 'instantly'
            ? ' Kampagnen, die schon bei Instantly sind, laufen weiter: pausiere sie in Instantly.' : ''), 'success');
    } else {
      showToast(failMsg(res, 'Versandmodus konnte nicht geändert werden.'), 'error');
    }
  } finally {
    _modeBusy = false;
  }
  loadSendingModeCard();
  loadOutbox();
}

// Why autopilot sent this back for a human (sender: gate.autopilot_review).
// state.HELD_FOR_REVIEW, and state.LEGACY_HELD_FOR_REVIEW on older rows.
const HELD_PREFIXES = ['zur Prüfung zurückgehalten: ', 'held for your review: '];
function heldReason(item) {
  const e = String((item && item.error) || '');
  const prefix = HELD_PREFIXES.find(p => e.startsWith(p));
  const why = prefix ? e.slice(prefix.length) : '';
  return why.charAt(0).toUpperCase() + why.slice(1);
}

function renderDesk(items, i) {
  const cur = items[i];
  const rail = items.map((it, n) => {
    const who = it.context && it.context.contact ? it.context.contact : it.to_email;
    const parts = String(who || '').split(/[\s@.]+/);
    return '<div class="desk-item ' + (n === i ? 'active' : '') + '" onclick="deskGo(' + n + ')">' +
      '<span class="avatar">' + initials(parts[0], parts[1]) + '</span>' +
      '<div class="di-text"><div class="to">' + escHtml(who) +
        (heldReason(it) ? ' <span class="tag t-bad" title="Harvey hat das zurückgehalten, statt es zu senden">Zurückgehalten</span>' : '') +
        '</div>' +
      '<div class="sub">' + escHtml(it.subject || '(kein Betreff)') + '</div></div>' +
    '</div>';
  }).join('');

  const editing = _desk.editing === cur.id;
  const unsaved = (editing && _desk.unsaved) || {subject: cur.subject || '', body: cur.body || ''};
  // A reply keeps its subject: Gmail threads by it.
  const fixedSubject = cur.kind === 'reply'
    ? ' readonly title="Eine Antwort behält ihren Betreff, damit sie im Verlauf bleibt"' : '';
  const draft = editing
    ? '<input id="desk-subject" class="desk-edit-subject" value="' + escHtml(unsaved.subject) + '"' +
        fixedSubject + ' oninput="deskKeep()">' +
      '<textarea id="desk-body" class="desk-edit-body" rows="14" oninput="deskKeep()">' +
        escHtml(unsaved.body) + '</textarea>'
    : '<div class="subject">' + escHtml(cur.subject || '(kein Betreff)') + '</div>' +
      '<div class="body">' + escHtml(cur.body) + '</div>';
  // Cold email leaves with the footer underneath; show it as it will go out.
  // Not added again when the body already ends with it, as in the Sender.
  const added = cur.kind === 'sequence' && !(cur.body || '').trimEnd().endsWith(_desk.footer)
    ? _desk.footer : '';
  const footer = added
    ? '<div class="desk-footer" title="Wird beim Senden angehängt (compliance in harvey.yaml)">' +
        escHtml(added) + '</div>' : '';
  const actions = editing
    ? '<button class="btn btn-primary" onclick="deskSave(' + jsq(cur.id) + ')">Speichern</button>' +
      '<button class="btn btn-secondary" onclick="deskEdit(null)">Abbrechen</button>'
    : '<button class="btn btn-primary" onclick="outboxAct(' + jsq(cur.id) + ',\'approve\')">' +
        'Freigeben <kbd>F</kbd></button>' +
      '<button class="btn btn-secondary" onclick="deskEdit(' + jsq(cur.id) + ')">' +
        'Bearbeiten <kbd>B</kbd></button>' +
      '<button class="btn btn-secondary" onclick="outboxAct(' + jsq(cur.id) + ',\'reject\')">' +
        'Ablehnen <kbd>X</kbd></button>';

  return '<div class="desk">' +
    '<div class="desk-list">' + rail + '</div>' +
    '<div class="desk-pane">' +
      '<div class="to-line">An <b>' + escHtml(cur.to_email) + '</b>' +
        '<span class="tag">' + (cur.kind === 'reply' ? 'Antwort' : 'E-Mail ' + escHtml(String(cur.step))) + '</span>' +
        '<span class="muted">Versand ' + formatDate(cur.send_at) + '</span></div>' +
      renderDeskContext(cur.context) +
      (heldReason(cur)
        ? '<div class="banner wait desk-held">' + icon('alert') + '<div><b>Harvey hat das zurückgehalten, statt es zu senden</b>' +
          '<div>' + escHtml(heldReason(cur)) + '. Lies es vor dem Freigeben: Text von einer Website oder aus einer ' +
          'E-Mail kann versuchen, zu steuern, was Harvey schreibt.</div></div></div>'
        : '') +
      draft + footer +
      '<div class="desk-actions">' + actions +
        '<span class="muted" style="font-size:12px;margin-left:auto">' +
          (i + 1) + ' von ' + items.length + '</span>' +
      '</div>' +
    '</div></div>';
}

function deskEdit(id) {
  _desk.editing = id;
  _desk.unsaved = null;
  document.getElementById('outbox-desk').innerHTML = renderDesk(_desk.items, _desk.i);
  if (id) document.getElementById('desk-body').focus();
}

// What's typed survives a re-render (switching tabs and back reloads the desk).
function deskKeep() {
  _desk.unsaved = {
    subject: document.getElementById('desk-subject').value,
    body: document.getElementById('desk-body').value,
  };
}

// While a draft is being edited, nothing else on the desk may act on the
// stored version of it: Save or Cancel first.
function deskBusy() {
  if (!_desk.editing) return false;
  showToast('Speichere oder verwirf zuerst deine Änderung.', 'error');
  return true;
}

async function deskSave(id) {
  const subject = document.getElementById('desk-subject').value.trim();
  const body = document.getElementById('desk-body').value.trim();
  if (!subject || !body) { showToast('Betreff und Text sind beide nötig.', 'error'); return; }
  let data = null;
  try {
    const r = await authFetch('/api/outbox/' + encodeURIComponent(id) + '/edit', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({subject: subject, body: body}),
    });
    if (await lockIfRefused(r)) return;
    data = await r.json();
  } catch {}
  if (data && data.success) {
    _desk.editing = null;
    _desk.unsaved = null;
    showToast('Gespeichert. Wartet weiter auf deine Freigabe.', 'success');
    loadOutbox();
  } else {
    showToast((data && data.message) || 'Speichern fehlgeschlagen.', 'error');
  }
}

function deskGo(n) {
  if (n < 0 || n >= _desk.items.length || deskBusy()) return;
  _desk.i = n;
  document.getElementById('outbox-desk').innerHTML = renderDesk(_desk.items, n);
}

// Keyboard review. Ignored while typing into a field, so Settings still works.
document.addEventListener('keydown', e => {
  if (currentTab !== 'outbox' || e.metaKey || e.ctrlKey || e.altKey) return;
  // Mid-edit, A would approve the stored draft, not what's typed. Only
  // Escape (cancel) works until the edit is saved or cancelled.
  if (_desk.editing) {
    const typing = /^(input|textarea)$/i.test(e.target.tagName || '');
    if (e.key === 'Escape') { e.preventDefault(); deskEdit(null); }
    else if (!typing && 'jkfxb'.includes(e.key.toLowerCase())) { e.preventDefault(); deskBusy(); }
    return;
  }
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || e.target.isContentEditable) return;
  const cur = _desk.items[_desk.i];
  const k = e.key.toLowerCase();
  if (k === 'j') { e.preventDefault(); deskGo(_desk.i + 1); }
  else if (k === 'k') { e.preventDefault(); deskGo(_desk.i - 1); }
  // German letters: F = Freigeben, X = Ablehnen, B = Bearbeiten. Never A: in German
  // it reads like "Ablehnen", and pressing it would approve an email.
  else if (k === 'f' && cur) { e.preventDefault(); outboxAct(cur.id, 'approve'); }
  else if (k === 'x' && cur) { e.preventDefault(); outboxAct(cur.id, 'reject'); }
  else if (k === 'b' && cur) { e.preventDefault(); deskEdit(cur.id); }
});

async function outboxAct(id, action) {
  if (deskBusy()) return;
  const data = await api('/api/outbox/' + encodeURIComponent(id) + '/' + action, {method: 'POST'});
  if (data && data.success) showToast(action === 'approve' ? 'Freigegeben. Geht nach Plan raus.' : 'Abgelehnt.', 'success');
  else showToast(failMsg(data, 'Aktion fehlgeschlagen.'), 'error');
  loadOutbox();
}

async function outboxApproveAll() {
  if (deskBusy()) return;
  const data = await api('/api/outbox/approve-all', {method: 'POST'});
  if (data && data.success) showToast(data.approved + ' E-Mail(s) freigegeben.' + (data.held
    ? ' ' + data.held + (data.held === 1 ? ' zurückgehaltene wartet' : ' zurückgehaltene warten') +
      ' darauf, dass du sie einzeln liest und freigibst.'
    : ''), 'success');
  else showToast(failMsg(data, 'Alle freigeben ist fehlgeschlagen.'), 'error');
  loadOutbox();
}

async function sendingToggle(action) {
  const data = await api('/api/sending/' + action, {method: 'POST'});
  if (data && data.success) showToast(action === 'pause' ? 'Versand pausiert.' : 'Versand fortgesetzt.', 'success');
  else showToast(failMsg(data, action === 'pause' ? 'Versand konnte nicht pausiert werden.' : 'Versand konnte nicht fortgesetzt werden.'), 'error');
  loadOutbox();
}

const COMPANIES_PER_PAGE = 50;
let _companiesPage = 0;

async function loadCompanies() {
  companyDrill = false;
  const el = document.getElementById('companies-list');
  const data = await api('/api/companies');
  if (!data) { el.innerHTML = offlineState(); return; }
  _companies = data;
  if (!data.length) {
    el.innerHTML = emptyState(icon('companies'), 'Noch keine Firmen',
      'Bestätige deine <b>Signale</b> und finde dann Firmen im Tab <b>Entdecken</b>. ' +
      'OpenStreetMap ist kostenlos und braucht kein Konto.');
    return;
  }
  renderCompaniesPage(_companiesPage);
}

function renderCompaniesPage(page) {
  const el = document.getElementById('companies-list');
  const data = _companies;
  const pages = Math.max(1, Math.ceil(data.length / COMPANIES_PER_PAGE));
  _companiesPage = Math.min(Math.max(0, page), pages - 1);
  const start = _companiesPage * COMPANIES_PER_PAGE;
  let html = '<div class="table-card"><table class="nowrap"><thead><tr><th>Firma</th>' +
    '<th title="Passung der Firma von 0 bis 100: Branche, Entscheider, Erreichbarkeit, Kaufsignal. Fahr über einen Wert, um die Gründe zu sehen.">Passung</th>' +
    '<th title="Fahr über einen Status, um zu sehen, warum die Firma kontaktiert wurde oder nicht">Status</th>' +
    '<th>Branche</th><th>Ort</th><th class="num">Kontakte</th><th>Hinzugefügt</th></tr></thead><tbody>';
  data.slice(start, start + COMPANIES_PER_PAGE).forEach((c, j) => {
    const i = start + j;
    const website = c.website || (c.domain ? 'https://' + c.domain : '');
    const domain = safeHref(website)
      ? '<a href="' + safeHref(website) + '" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">' +
        escHtml(c.domain || website.replace(/^https?:\/\//i, '')) + '</a>'
      : escHtml(c.domain);
    html += '<tr class="clickable" onclick="showCompanyContacts(' + i + ')">' +
      '<td><div class="cell-stack"><span class="cell-main">' + escHtml(c.name) + '</span>' +
        '<span class="cell-sub">' + domain + '</span></div></td>' +
      '<td title="' + escHtml((c.fit_reasons || []).join(' · ')) + '">' +
        (typeof c.fit === 'number' ? meterHtml(c.fit) : '') + '</td>' +
      '<td title="' + escHtml(c.register_why || '') + '">' + escHtml(c.register_status || '') + '</td>' +
      '<td>' + (industryLabel(c.industry) ? '<span class="tag">' + escHtml(industryLabel(c.industry)) + '</span>' : '') + '</td>' +
      '<td>' + escHtml(c.location) + '</td>' +
      '<td class="num">' + (c.contact_count || 0) + '</td>' +
      '<td class="muted">' + relTime(c.created_at) + '</td></tr>';
  });
  const pager = pages > 1
    ? ' &nbsp; <button class="btn btn-ghost btn-sm" ' + (_companiesPage === 0 ? 'disabled ' : '') +
        'onclick="renderCompaniesPage(' + (_companiesPage - 1) + ')">&larr; Zurück</button> ' +
      'Seite ' + (_companiesPage + 1) + ' von ' + pages +
      ' <button class="btn btn-ghost btn-sm" ' + (_companiesPage >= pages - 1 ? 'disabled ' : '') +
        'onclick="renderCompaniesPage(' + (_companiesPage + 1) + ')">Weiter &rarr;</button>'
    : '';
  el.innerHTML = html + '</tbody></table></div>' +
    '<div class="table-foot">' + (start + 1) + ' bis ' + Math.min(start + COMPANIES_PER_PAGE, data.length) +
    ' von ' + data.length + (data.length === 1 ? ' Firma' : ' Firmen') + pager + '</div>';
}

// Basic facts about one company, above its contacts: where it came from,
// why it fits, what's been done, and what Harvey saw on its own site.
function companyInfoCard(company, profile) {
  const c = (profile && profile.company) || company;
  const reg = (profile && profile.register) || {};
  const obs = (profile && profile.observations) || [];
  const link = (url, text) => safeHref(url)
    ? '<a href="' + safeHref(url) + '" target="_blank" rel="noopener noreferrer">' + escHtml(text || url) + '</a>'
    : escHtml(text || url || '');
  const website = c.website || (c.domain ? 'https://' + c.domain : '');
  const row = (k, v) => v ? '<tr><td class="muted" style="width:170px;vertical-align:top">' + k +
    '</td><td style="white-space:normal">' + v + '</td></tr>' : '';
  const latest = {};
  for (const o of obs) {
    const key = o.signal_code + '|' + (o.value_text || o.value_num);
    if (!latest[key] && (o.value_text || o.value_num !== null)) latest[key] = o;
  }
  const by = code => Object.values(latest).filter(o => o.signal_code === code);
  const list = (items, max) => items.slice(0, max).map(o =>
    (o.evidence_url ? link(o.evidence_url, o.value_text || String(o.value_num)) : escHtml(o.value_text || String(o.value_num)))
  ).join('<br>');
  const people = by('DECISION_MAKER_TITLE').length ? by('DECISION_MAKER_TITLE') : by('CONTACT_FOUND');
  const form = by('CONTACT_FORM_URL')[0];
  const shown = new Set(['DECISION_MAKER_TITLE', 'CONTACT_FOUND', 'CONTACT_FORM_URL', 'HIRING_ROLE', 'FOUND_IN_SERP', 'TECH_STACK']);
  const other = Object.values(latest).filter(o => !shown.has(o.signal_code));
  let html = '<div class="card" style="margin-bottom:16px"><div class="card-head"><h3>' + escHtml(c.name || c.domain) + '</h3>' +
    '<span class="meta">' + (typeof company.fit === 'number' ? 'Passung ' + company.fit + ' / 100' : '') + '</span></div>' +
    '<table><tbody>' +
    row('Website', link(website, c.domain || website)) +
    row('Branche', escHtml(industryLabel(c.industry))) +
    row('Region', escHtml(c.location)) +
    row('Warum ausgewählt', escHtml(reg.why_selected || (company.fit_reasons || []).join(' · '))) +
    row('Status', '<b>' + escHtml(reg.status || company.register_status || '') + '</b>' +
      (reg.why_not_contacted || company.register_why ? ' · ' + escHtml(reg.why_not_contacted || company.register_why) : '')) +
    row('Auf der Website genannt', list(people, 8)) +
    row('Kontaktformular', form ? link(form.value_text, form.value_text) : '') +
    row('Offene Stellen', list(by('HIRING_ROLE'), 6)) +
    row('Technik der Website', escHtml(by('TECH_STACK').map(o => o.value_text).slice(0, 1).join(''))) +
    row('Weitere Signale', other.slice(0, 8).map(o => escHtml(o.label) + ': ' +
      escHtml(o.value_text || String(o.value_num))).join('<br>')) +
    row('Gefunden über', c.source_url ? link(c.source_url, 'Suchergebnis') + ' · ' + sourceLabel(c.source) : sourceLabel(c.source)) +
    row('Zuerst gefunden', escHtml((c.created_at || '').slice(0, 10))) +
    '</tbody></table></div>';
  return html;
}

async function showCompanyContacts(index) {
  const company = _companies[index];
  if (!company) return;
  companyDrill = true;
  const el = document.getElementById('companies-list');
  const [data, profile] = await Promise.all([
    api('/api/companies/' + encodeURIComponent(company.id) + '/contacts'),
    api('/api/companies/' + encodeURIComponent(company.id) + '/profile'),
  ]);
  let html = '<button class="btn btn-ghost btn-sm" onclick="loadCompanies()" style="margin-bottom:12px">' +
    '&larr; Alle Firmen</button>' + companyInfoCard(company, profile) +
    '<div class="card flush"><div class="card-head"><h3>Kontakte</h3>' +
    '<span class="meta">' + escHtml([industryLabel(company.industry), company.location].filter(Boolean).join(' · ')) + '</span></div>';
  if (!data || !data.length) {
    html += '<div class="empty"><div class="copy">Bei dieser Firma noch keine Kontakte gefunden.</div></div></div>';
  } else {
    html += '<table><thead><tr><th>Name</th><th>E-Mail</th><th>Telefon</th><th>LinkedIn</th><th>Status</th><th>Quelle</th></tr></thead><tbody>';
    for (const p of data) {
      const phoneIcon = p.phone_verified ? ' <span class="muted">&#10003;</span>' : '';
      html += '<tr><td><div class="cell-row"><span class="avatar">' + initials(p.first_name, p.last_name) + '</span>' +
          '<div class="cell-stack"><span class="cell-main">' + escHtml(p.first_name) + ' ' + escHtml(p.last_name) + '</span>' +
          '<span class="cell-sub">' + escHtml(titleLabel(p.title)) + '</span></div></div></td>' +
        '<td>' + escHtml(p.email) + emailTag(p) + '</td>' +
        '<td>' + escHtml(p.phone) + phoneIcon + '</td>' +
        '<td>' + (safeHref(p.linkedin_url) ? '<a href="' + safeHref(p.linkedin_url) + '" target="_blank" rel="noopener noreferrer">Profil</a>' : '') + '</td>' +
        '<td>' + badge(p.status) + '</td><td class="muted">' + sourceLabel(p.source) + '</td></tr>';
    }
    html += '</tbody></table></div>';
  }
  el.innerHTML = html;
}

async function submitFeedback(entityType, entityId, promptText) {
  const comment = prompt(promptText || 'Dein Feedback:');
  if (!comment) return;
  const data = await api('/api/feedback', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({entity_type: entityType, entity_id: entityId, comment: comment})
  });
  if (data && data.success) showToast(entityType === 'campaign'
    ? 'Gespeichert. Harvey beachtet es ab jetzt in den E-Mails, die er schreibt.'
    : 'Gespeichert. Harvey nutzt es, wenn er die erste E-Mail an diese Person schreibt (Gmail, SMTP oder Microsoft 365).', 'success');
  else showToast((data && data.message) || 'Feedback konnte nicht gespeichert werden.', 'error');
}

function fbProspect(i) {
  const p = _prospects[i];
  if (p) submitFeedback('contact', p.id, 'Sollte Harvey etwas wissen, bevor er dieser Person schreibt?');
}

function fbCampaign(i) {
  const c = _campaigns[i];
  if (c) submitFeedback('campaign', c.id, 'Was soll Harvey in den nächsten E-Mails anders machen? (z. B. „kürzer; keine Bewertungen erwähnen“)');
}

// Search and status filter live outside the table, so the 15-second refresh
// re-renders rows without losing what you typed.
let _contactQuery = '', _contactFilter = 'all';
const CONTACT_FILTERS = [
  ['all', 'Alle', null],
  ['todo', 'Zu schreiben', ['new', 'queued']],
  ['contacted', 'Kontaktiert', ['contacted']],
  ['replied', 'Geantwortet', ['replied', 'interested', 'meeting']],
  ['closed', 'Abgeschlossen', null],
];

async function loadProspects() {
  const el = document.getElementById('prospects-table');
  const data = await api('/api/prospects');
  if (!data) { el.innerHTML = offlineState(); return; }
  _prospects = data;
  if (!data.length) {
    el.innerHTML = emptyState(icon('contacts'), 'Noch keine Kontakte',
      'Harvey hat noch keine Kontakte gefunden. Sobald er läuft, sucht der Scout-Agent im Web nach Leuten, die zu deinem Idealkundenprofil in <b>harvey.yaml</b> passen.');
    return;
  }
  if (!document.getElementById('contact-rows')) {
    el.innerHTML = '<div class="toolbar">' +
      '<label class="search">' + icon('discover') +
        '<input class="form-input" id="contact-search" type="search" placeholder="Name, Firma, E-Mail suchen" ' +
        'aria-label="Kontakte suchen" oninput="_contactQuery=this.value;renderContacts()"></label>' +
      '<div class="segmented" id="contact-filters" role="group" aria-label="Nach Status filtern"></div>' +
      '</div><div id="contact-rows"></div>';
    // A rebuild after an offline blip must show the search still applied.
    document.getElementById('contact-search').value = _contactQuery;
  }
  renderContacts();
}

function contactMatches(p, f) {
  const known = ['new', 'queued', 'contacted', 'replied', 'interested', 'meeting'];
  if (f === 'all') return true;
  if (f === 'closed') return !known.includes(p.status);
  const hit = CONTACT_FILTERS.find(x => x[0] === f);
  return !!(hit && hit[2] && hit[2].includes(p.status));
}

function setContactFilter(f) { _contactFilter = f; renderContacts(); }

function renderContacts() {
  const rowsEl = document.getElementById('contact-rows');
  const filtersEl = document.getElementById('contact-filters');
  if (!rowsEl) return;
  const q = _contactQuery.trim().toLowerCase();
  const found = p => !q || [p.first_name, p.last_name, p.company, p.email, p.title].join(' ').toLowerCase().includes(q);
  filtersEl.innerHTML = CONTACT_FILTERS.map(f =>
    '<button class="' + (f[0] === _contactFilter ? 'on' : '') + '" aria-pressed="' + (f[0] === _contactFilter) + '" onclick="setContactFilter(\'' + f[0] + '\')">' +
      f[1] + ' <span class="n">' + _prospects.filter(p => contactMatches(p, f[0]) && found(p)).length + '</span></button>'
  ).join('');

  const rows = _prospects.map((p, i) => [p, i]).filter(([p]) => contactMatches(p, _contactFilter) && found(p));
  if (!rows.length) {
    rowsEl.innerHTML = '<div class="table-card">' + emptyState(icon('discover'), 'Keine Treffer',
      'Nichts passt zu dieser Suche und diesem Filter.') + '</div>';
    return;
  }
  let html = '<div class="table-card"><table class="nowrap"><thead><tr><th>Name</th><th>Firma</th><th>E-Mail</th>' +
    '<th>Score</th><th>Status</th><th>Quelle</th><th>Hinzugefügt</th><th></th></tr></thead><tbody>';
  for (const [p, i] of rows) {
    html += '<tr><td><div class="cell-row"><span class="avatar">' + initials(p.first_name, p.last_name) + '</span>' +
        '<div class="cell-stack"><span class="cell-main">' + escHtml(p.first_name) + ' ' + escHtml(p.last_name) + '</span>' +
        '<span class="cell-sub">' + escHtml(titleLabel(p.title)) + '</span></div></div></td>' +
      '<td>' + escHtml(p.company) + '</td>' +
      '<td><div class="cell-stack"><span>' + escHtml(p.email) + '</span>' +
        '<span class="cell-sub">' + [emailTag(p).trim(), p.phone ? escHtml(p.phone) +
          (p.phone_verified ? ' <span class="verified" title="Telefon geprüft">&#10003;</span>' : '') : '']
          .filter(Boolean).join(' · ') + '</span>' +
        '</div></td>' +
      '<td>' + (p.score || p.score === 0 ? meterHtml(p.score) : '') + '</td>' +
      '<td>' + badge(p.status) + '</td>' +
      '<td class="muted">' + sourceLabel(p.source) + '</td>' +
      '<td class="muted" style="white-space:nowrap">' + relTime(p.created_at) + '</td>' +
      '<td><div class="cell-row">' + (p.status === 'opted_out' || p.opt_out_undone || _optOut.id === p.id
        ? '<button class="btn btn-ghost btn-sm" onclick="toggleOptOut(' + i + ')" ' +
          'aria-expanded="' + (_optOut.id === p.id) + '" title="Warum Harvey dieser Person nicht mehr schreibt">' +
          (p.status === 'opted_out' ? 'Warum?' : 'Abmeldeverlauf') + '</button>' : '') +
        '<button class="btn btn-ghost btn-sm row-action" onclick="fbProspect(' + i + ')" ' +
        'title="Feedback zu diesem Kontakt geben">Feedback</button></div></td></tr>';
    if (_optOut.id === p.id) html += '<tr class="detail-row"><td colspan="8">' + optOutPanel(p) + '</td></tr>';
  }
  rowsEl.innerHTML = html + '</tbody></table></div>' +
    '<div class="table-foot">' + rows.length + ' von ' + _prospects.length + ' Kontakten</div>';
}

// Why someone is opted out, and undoing it. Harvey never moves anyone off
// "opted out" on its own, so a mistaken one (a bare "stop" that meant
// something else) needs you: see what triggered it, then undo it here.
let _optOut = {id: null, data: null, armed: false, busy: false};
const OPT_OUT_RULES = {
  phrase: 'Passte auf eine Abmeldeformulierung',
  no_thanks: 'Passte auf „Nein danke“',
  only_words: 'Die Antwort bestand nur aus einem Abmeldewort',
  model: 'Einschätzung des Modells (keine Formulierung passte)',
};

async function toggleOptOut(i) {
  const p = _prospects[i];
  if (!p) return;
  if (_optOut.id === p.id) { _optOut = {id: null, data: null, armed: false, busy: false}; renderContacts(); return; }
  _optOut = {id: p.id, data: null, armed: false, busy: false};
  renderContacts();
  const data = await api('/api/prospects/' + encodeURIComponent(p.id) + '/opt-out');
  if (_optOut.id !== p.id) return;   // closed or switched meanwhile
  _optOut.data = data || {error: true};
  renderContacts();
}

function optOutPanel(p) {
  const d = _optOut.data;
  const name = escHtml([p.first_name, p.last_name].filter(Boolean).join(' ') || p.email);
  if (!d) return '<div class="optout-panel muted">Lädt…</div>';
  if (d.error) return '<div class="optout-panel muted">Die Abmeldedaten konnten nicht geladen werden. Versuch es noch einmal.</div>';
  let html = '<div class="optout-panel"><h4>' + (d.opted_out ? 'Warum Harvey ' + name + ' nicht mehr schreibt'
    : 'Abmeldeverlauf für ' + name) + '</h4>';
  if (d.recorded) {
    html += '<p>' + escHtml(d.why) + '</p><div class="optout-meta">' +
      escHtml(own(OPT_OUT_RULES, d.rule) || d.rule || '') + (d.at ? ' · ' + relTime(d.at) : '') + '</div>';
  } else if (d.opted_out || (d.history || []).length) {
    html += '<p>Die Person hat sich abgemeldet, bevor Harvey Gründe festgehalten hat.' +
      (d.message ? ' Ihre letzte Nachricht:' : ' Es liegt keine Antwort von ihr vor.') + '</p>';
  }
  if (d.message) {
    html += '<div class="thread-msg received"><div class="sender">' + escHtml(p.first_name || 'Kontakt') +
      (d.at ? ' &middot; ' + relTime(d.at) : '') + '</div>' + escHtml(d.message) + '</div>';
  }
  const undone = (d.history || []).filter(h => h.action === 'opt_out_undone');
  if (undone.length) {
    html += '<div class="optout-meta">' + undone.map(h => 'Du hast eine Abmeldung zurückgenommen, ' + relTime(h.at)).join(' · ') + '</div>';
  }
  if (!d.opted_out) {
    html += '<div class="convo-actions"><span class="muted">Derzeit nicht abgemeldet (' +
      escHtml(statusMeta(d.status).label) + ').</span></div>';
  } else if (_optOut.armed) {
    html += '<div class="convo-actions"><span class="muted">Harvey behandelt die Person dann so, als hätte sie geantwortet. ' +
      'Nichts wird automatisch gesendet, von der Abmeldung abgebrochene E-Mails bleiben abgebrochen, und Harveys Antwort ' +
      'auf ihre nächste Nachricht wartet auf deine Freigabe, auch im Autopilot. Nimm die Abmeldung nur zurück, ' +
      'wenn sie sich nicht abmelden wollte.</span>' +
      '<button class="btn btn-primary btn-sm" onclick="undoOptOut()"' + (_optOut.busy ? ' disabled' : '') +
        '>Zurücknahme bestätigen</button>' +
      '<button class="btn btn-ghost btn-sm" onclick="_optOut.armed=false;renderContacts()">Abbrechen</button></div>';
  } else {
    html += '<div class="convo-actions"><button class="btn btn-secondary btn-sm" ' +
      'onclick="_optOut.armed=true;renderContacts()">Abmeldung zurücknehmen</button></div>';
  }
  return html + '</div>';
}

async function undoOptOut() {
  const id = _optOut.id;
  if (!id || _optOut.busy) return;
  _optOut.busy = true;
  renderContacts();
  const res = await api('/api/prospects/' + encodeURIComponent(id) + '/opt-out/undo', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({confirm: true}),
  });
  if (res && res.success) showToast('Abmeldung zurückgenommen. Die Person ist als geantwortet markiert; Harvey sendet nichts von sich aus.', 'success');
  else showToast(failMsg(res, 'Die Abmeldung konnte nicht zurückgenommen werden. Versuch es noch einmal.'), 'error');
  if (_optOut.id !== id) return;
  _optOut.armed = false;
  _optOut.busy = false;
  _optOut.data = await api('/api/prospects/' + encodeURIComponent(id) + '/opt-out') || {error: true};
  loadProspects();
}

// From a conversation: the contact, with the opt-out record open.
async function showOptOut(prospectId, email) {
  goTab('prospects');
  _contactQuery = email || '';
  const search = document.getElementById('contact-search');
  if (search) search.value = _contactQuery;
  await loadProspects();
  const i = _prospects.findIndex(p => p.id === prospectId);
  if (i >= 0 && _optOut.id !== prospectId) toggleOptOut(i);
}

// Opens a filled-in compose window in whatever Gmail account the browser is
// logged into. Nothing is sent from here — the user reads it and presses Send.
// Same values as the Sender's _render, so a hand-sent email reads the same.
function fillMergeVars(text, r) {
  const inbox = r.source === 'company_inbox';
  const vals = {
    first_name: r.first_name || (inbox ? 'zusammen' : ''), last_name: r.last_name || '',
    company: r.company || '', title: r.title || '',
    personalization: r.personalization_notes || '',
  };
  return String(text || '').replace(/\{\{\s*(\w+)\s*\}\}/g, (m, k) => {
    k = k.toLowerCase();
    return Object.prototype.hasOwnProperty.call(vals, k) ? vals[k] : m;
  });
}

let _gmailBusy = false;  // one compose window per click, never two

async function openInGmail(ci, si, ri) {
  if (_gmailBusy) return;
  const c = _campaigns[ci];
  const step = c && (c.sequence || [])[si];
  const r = c && (c.recipients || [])[ri];
  if (!step || !r) return;
  // Cold email carries the postal address and opt-out line, sent by hand too.
  const filled = fillMergeVars(step.body, r).trimEnd();
  const body = filled + (c.footer && !filled.endsWith(c.footer) ? '\n\n' + c.footer : '');
  // authuser picks the Google account Harvey sends as, not just whichever
  // one the browser happens to be signed into.
  const url = 'https://mail.google.com/mail/?view=cm&fs=1' +
    (c.send_as ? '&authuser=' + encodeURIComponent(c.send_as) : '') +
    '&to=' + encodeURIComponent(r.email) +
    '&su=' + encodeURIComponent(fillMergeVars(step.subject, r)) +
    '&body=' + encodeURIComponent(body);
  const left = /\{\{\s*\w+\s*\}\}/.test(fillMergeVars(step.subject, r) + fillMergeVars(step.body, r));
  // No 'noopener' feature string: with it window.open always returns null,
  // so a blocked pop-up could not be told apart from a successful one. The
  // tab opens blank right away (inside the click, so it isn't blocked) and
  // only gets the draft once the send is recorded.
  const w = window.open('', '_blank');
  if (!w) {
    showToast('Dein Browser hat den Gmail-Tab blockiert. Erlaube Pop-ups für diese Seite und klick noch einmal.', 'error');
    return;
  }
  w.opener = null;
  // Record the send BEFORE the user can press Send in Gmail, so Harvey can
  // never email them as well — not even if they forget to tick the box.
  _gmailBusy = true;
  let claimed = r.sent_by_hand;
  try { if (!claimed) claimed = await markSent(ci, ri, true); } finally { _gmailBusy = false; }
  if (!claimed) { w.close(); return; }
  w.location.href = url;
  if (left) showToast('Dieser Entwurf enthält noch einen {{Platzhalter}}. Korrigiere ihn in Gmail vor dem Senden.', 'error');
}

async function markSent(ci, ri, sent) {
  const r = (_campaigns[ci] && _campaigns[ci].recipients || [])[ri];
  if (!r) return;
  let res = null;
  try {
    const resp = await authFetch('/api/prospects/' + encodeURIComponent(r.id) + '/sent', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sent}),
    });
    if (await lockIfRefused(resp)) return;
    res = await resp.json();
  } catch {}
  const ok = !!(res && res.success);
  if (!ok) showToast((res && res.message) || 'Konnte nicht gespeichert werden. Versuch es noch einmal.', 'error');
  else if (!sent && res.sent) showToast('Die Person hat schon geantwortet, deshalb bleibt das als gesendet markiert.');
  else showToast(sent ? 'Als von dir gesendet markiert.' : 'Markierung entfernt.', 'success');
  loadCampaigns();
  // True only when this call changed it: a status means they replied or
  // opted out, 'already' means another click or tab got there first.
  return ok && sent === !!res.sent && !res.status && !res.already;
}

// Where a campaign's people stand, as one stacked bar.
function campaignProgress(c) {
  const rs = c.recipients || [];
  if (!rs.length) return '';
  const count = st => rs.filter(r => st.includes(r.status)).length;
  const parts = [
    ['Kontaktiert', count(['contacted']), 'c-accent'],
    ['Geantwortet', count(['replied']), 'c-active'],
    ['Interessiert', count(['interested', 'meeting']), 'c-good'],
    ['Wartend', count(['new', 'queued']), 'c-accent-soft'],
  ];
  const closed = rs.length - parts.reduce((x, p) => x + p[1], 0);
  if (closed > 0) parts.push(['Abgeschlossen', closed, 'c-idle']);
  return '<div class="campaign-progress"><div class="bar" role="img" aria-label="Wo die Kontakte dieser Kampagne stehen">' +
    parts.filter(p => p[1]).map(p => '<span class="' + p[2] + '" style="width:' + (100 * p[1] / rs.length) +
      '%" title="' + p[0] + ': ' + p[1] + '"></span>').join('') + '</div>' +
    '<div class="campaign-legend">' + parts.filter(p => p[1]).map(p =>
      '<span><i class="' + p[2] + '"></i>' + p[0] + ' <b>' + p[1] + '</b></span>').join('') + '</div></div>';
}

async function loadCampaigns() {
  const el = document.getElementById('campaigns-list');
  const data = await api('/api/campaigns');
  if (!data) { el.innerHTML = offlineState(); return; }
  _campaigns = data;
  if (!data.length) {
    el.innerHTML = emptyState(icon('campaigns'), 'Noch keine Kampagnen',
      'Der Writer-Agent hat noch keine Sequenzen entworfen. Er legt automatisch los, sobald Harvey bewertete Kontakte hat, für die er schreiben kann.');
    return;
  }
  let html = '';
  data.forEach((c, i) => {
    let stepsHtml = '';
    (c.sequence || []).forEach((step, s) => {
      const sendBtns = (c.recipients || []).map((r, ri) => {
        // Only offered when Harvey can't send itself and this person can
        // still be emailed (the server decides: can_send_by_hand).
        if (r.sent_by_hand) {
          const tick = '<label style="display:inline-flex;align-items:center;gap:4px;font-size:13px;cursor:pointer">' +
            '<input type="checkbox" checked onchange="markSent(' + i + ',' + ri + ',this.checked)"> ' +
            '&#10003; Von dir gesendet</label>';
          if (s === 0) return '<span class="rcpt-who">' + escHtml(r.email) + '</span>' + tick;
          // Follow-ups are the user's too: Harvey never emails someone they emailed.
          return '<span class="rcpt-who">' + escHtml(r.email) + '</span>' +
            '<button class="btn btn-secondary btn-sm" onclick="openInGmail(' + i + ',' + s + ',' + ri + ')">' +
            'In Gmail öffnen</button>' +
            '<span class="muted" style="font-size:12px">Harvey sendet keine Follow-ups an Leute, denen du selbst geschrieben hast.</span>';
        }
        if (s !== 0) return '';
        if (!r.can_send_by_hand) {
          // Harvey sends here; this only records an email the user already sent.
          return r.can_mark_sent
            ? '<label class="muted" style="display:inline-flex;align-items:center;gap:4px;font-size:12px;cursor:pointer">' +
              '<input type="checkbox" onchange="markSent(' + i + ',' + ri + ',this.checked)"> ' +
              'Ich habe ' + escHtml(r.email) + ' schon selbst geschrieben</label>'
            : '';
        }
        const risky = r.email_status === 'risky'
          ? ' <span class="mini t-waiting">Catch-all, kann unzustellbar sein</span>' : '';
        return '<span class="rcpt-who">' + escHtml(r.email) + risky + '</span>' +
          '<button class="btn btn-secondary btn-sm" onclick="openInGmail(' + i + ',' + s + ',' + ri + ')">' +
          'In Gmail öffnen</button>' +
          '<label><input type="checkbox" onchange="markSent(' + i + ',' + ri + ',this.checked)"> Als gesendet markieren</label>'
      }).filter(Boolean).map(x => '<div class="rcpt">' + x + '</div>').join('');
      stepsHtml += '<div class="email-step"><span class="step-dot">' + escHtml(String(step.step || '?')) + '</span>' +
        '<div><div class="step-num">' + (s === 0
          ? (c.first_email_personal ? 'Erste E-Mail · Vorlage (für Folge-Mails / Fallback)' : 'Erste E-Mail')
          : 'Follow-up') +
        (step.delay_days ? ' &middot; ' + escHtml(String(step.delay_days)) + ' Tage nach der letzten' : '') + '</div>' +
        '<div class="subject">' + escHtml(step.subject) + '</div>' +
        '<div class="body">' + escHtml(step.body) + '</div>' +
        (sendBtns ? '<div class="step-actions">' + sendBtns + '</div>' : '') +
        '</div></div>';
    });
    const pc = (c.prospect_ids || []).length;
    html += '<div class="campaign-card"><div class="campaign-top">' +
      '<div class="campaign-title"><h3>' + escHtml(campaignLabel(c.name) || 'Kampagne ohne Titel') + '</h3>' +
        '<button class="btn btn-ghost btn-sm" onclick="fbCampaign(' + i + ')">Feedback</button></div>' +
      '<div class="meta">' + badge(c.status) + '<span>' + pc + (pc !== 1 ? ' Kontakte' : ' Kontakt') + '</span>' +
        '<span>' + ((c.sequence || []).length === 1 ? '1 E-Mail' : (c.sequence || []).length + ' E-Mails') + '</span><span>Erstellt ' + relTime(c.created_at) + '</span></div>' +
      campaignProgress(c) + '</div>' +
      (c.first_email_personal && (c.sequence || []).length
        ? '<p class="muted" style="font-size:13px;margin:12px 0 0">Das hier ist die Vorlage (für Folge-Mails / Fallback). ' +
          'Die erste Mail schreibt Harvey für jede Person einzeln. Du findest sie im ' +
          '<a href="#" onclick="goTab(\'outbox\');return false">Postausgang</a>.</p>'
        : '') +
      '<div class="sequence">' +
      (stepsHtml || '<p class="muted" style="font-size:13px;padding-top:16px">Diese Kampagne hat keine E-Mail-Schritte.</p>') + '</div></div>';
  });
  el.innerHTML = html;
}

// The whole exchange in order: Harvey's cold emails (from the Outbox), the
// replies, Harvey's answers, and any answer still waiting for approval.
let _convos = [], _convoFilter = 'needs';
const STAGE_LABELS = {
  initial_outreach: 'Erstkontakt', engaged: 'Im Gespräch', qualifying: 'Qualifizierung', presenting: 'Präsentation',
  negotiating: 'Verhandlung', closing: 'Abschluss', closed_won: 'Gewonnen', closed_lost: 'Verloren',
};
const CONVO_FILTERS = [
  ['needs', 'Braucht dich', c => !!c.draft || c.status === 'needs_human'],
  ['open', 'Offen', c => c.status !== 'closed' && c.status !== 'awaiting_reply'],
  ['awaiting', 'Wartet auf Antwort', c => c.status === 'awaiting_reply'],
  ['closed', 'Geschlossen', c => c.status === 'closed'],
  ['all', 'Alle', () => true],
];

async function loadConversations() {
  const el = document.getElementById('conversations-list');
  const data = await api('/api/conversations?awaiting=1');
  if (!data) { el.innerHTML = offlineState(); return; }
  _convos = data;
  if (!data.length) {
    el.innerHTML = emptyState(icon('conversations'), 'Noch keine Gespräche',
      'Bisher hat noch kein Kontakt geantwortet. Sobald jemand antwortet, ordnet der Handler-Agent die Antwort ein und reagiert darauf. Jeder Verlauf erscheint hier.');
    return;
  }
  // Open on "Braucht dich" only when something needs you.
  if (_convoFilter === 'needs' && !data.some(CONVO_FILTERS[0][2]))
    _convoFilter = data.some(CONVO_FILTERS[1][2]) ? 'open' : 'all';
  renderConversations();
}

function setConvoFilter(f) { _convoFilter = f; renderConversations(); }

function renderConversations() {
  const el = document.getElementById('conversations-list');
  const filter = CONVO_FILTERS.find(f => f[0] === _convoFilter) || CONVO_FILTERS[CONVO_FILTERS.length - 1];
  const bar = '<div class="toolbar"><div class="segmented" role="group" aria-label="Gespräche filtern">' +
    CONVO_FILTERS.map(f => '<button class="' + (f[0] === filter[0] ? 'on' : '') + '" aria-pressed="' + (f[0] === filter[0]) +
      '" onclick="setConvoFilter(\'' + f[0] + '\')">' + f[1] + ' <span class="n">' +
      _convos.filter(f[2]).length + '</span></button>').join('') + '</div></div>';
  // Handed-to-you first, then drafts waiting; otherwise newest first (as served).
  const rank = c => c.status === 'needs_human' ? 0 : c.draft ? 1 : 2;
  const shown = _convos.filter(filter[2]).map((c, i) => [c, i])
    .sort((a, b) => rank(a[0]) - rank(b[0]) || a[1] - b[1]).map(x => x[0]);
  if (!shown.length) {
    el.innerHTML = bar + '<div class="card">' + emptyState(icon('check'), 'Hier ist nichts',
      'Kein Gespräch passt zu diesem Filter.') + '</div>';
    return;
  }
  el.innerHTML = bar + shown.map(convoCard).join('');
}

function convoCard(c) {
  const items = (c.emails || []).map(e => ({ts: e.sent_at, email: e}))
    .concat((c.thread || []).map(m => ({ts: m.timestamp, msg: m})))
    .map(it => ({...it, key: +parseTs(it.ts)}))
    // An item with no time goes last instead of scrambling the order.
    .sort((a, b) => (isNaN(a.key) ? Infinity : a.key) - (isNaN(b.key) ? Infinity : b.key) || 0);
  const first = c.first_name || 'Kontakt';
  const thread = items.map(it => {
    if (it.email) {
      return '<div class="thread-email"><div class="te-head">' + icon('campaigns') +
        '<b>E-Mail ' + escHtml(String(it.email.step)) + '</b><span>' + escHtml(it.email.subject) + '</span>' +
        '<span class="te-when">' + relTime(it.email.sent_at) + '</span></div>' +
        '<div class="te-body">' + escHtml(it.email.body) + '</div></div>';
    }
    const m = it.msg, sent = m.sender === 'harvey';
    return '<div class="thread-msg ' + (sent ? 'sent' : 'received') + '"><div class="sender">' +
      (sent ? 'Harvey' : escHtml(first)) + ' &middot; ' + relTime(m.timestamp) + '</div>' +
      escHtml(m.content) + '</div>';
  }).join('');
  const draft = c.draft
    ? '<div class="thread-draft"><div class="sender">' + icon('pen') + 'Harvey hat eine Antwort entworfen · wartet auf dich</div>' +
      '<div class="td-body">' + escHtml(c.draft.body) + '</div>' +
      '<button class="btn btn-primary btn-sm" onclick="goTab(\'outbox\')">Im Postausgang prüfen' + icon('arrow') + '</button></div>'
    : '';
  const name = [c.first_name, c.last_name].filter(Boolean).join(' ') || c.company || 'Unbekannt';
  const stage = own(STAGE_LABELS, c.stage) ? '<span class="tag">' + STAGE_LABELS[c.stage] + '</span>' : '';
  return '<div class="convo-card"><div class="convo-head">' +
    '<span class="avatar">' + initials(c.first_name, c.last_name) + '</span>' +
    '<div><h3>' + escHtml(name) + '</h3><div class="sub">' +
      escHtml([c.company, c.prospect_email].filter(Boolean).join(' · ')) + ' · ' + relTime(c.updated_at) + '</div></div>' +
    // An escalation's intent badge says the same as its status: show one.
    '<div class="tags">' + (c.intent && !(c.status === 'needs_human' && c.intent === 'escalate') ? badge(c.intent) : '') +
      stage + badge(c.status) + '</div></div>' +
    '<div class="thread">' + (thread || '<p class="muted" style="font-size:13px">Noch keine Nachrichten in diesem Verlauf.</p>') +
    draft + '</div>' + convoActions(c) + '</div>';
}

// Harvey answers new replies only on open conversations: an escalated one
// waits for you to reply yourself, then close it or hand it back.
function convoActions(c) {
  if (c.status === 'needs_human' && c.prospect_status === 'opted_out') {
    // A complaint or legal threat from someone who opted out: handing it
    // back wouldn't help, Harvey never answers them.
    return '<div class="convo-actions"><span class="muted">' + icon('alert') +
      'Abgemeldet, und Harvey hat das an dich übergeben. Antworte bei Bedarf aus deinem Postfach, dann:</span>' +
      '<button class="btn btn-secondary btn-sm" onclick="setConvoStatus(' + jsq(c.id) + ',\'closed\')">Schließen</button>' +
      '<button class="btn btn-secondary btn-sm" onclick="showOptOut(' + jsq(c.prospect_id) + ',' + jsq(c.prospect_email) +
      ')">Warum abgemeldet?</button></div>';
  }
  if (c.status === 'needs_human') {
    return '<div class="convo-actions"><span class="muted">' + icon('alert') +
      'Harvey hat das an dich übergeben. Antworte aus deinem Postfach, dann:</span>' +
      '<button class="btn btn-secondary btn-sm" onclick="setConvoStatus(' + jsq(c.id) + ',\'open\')">An Harvey zurückgeben</button>' +
      '<button class="btn btn-secondary btn-sm" onclick="setConvoStatus(' + jsq(c.id) + ',\'closed\')">Schließen</button></div>';
  }
  if (c.status === 'open') {
    return '<div class="convo-actions"><button class="btn btn-ghost btn-sm" onclick="setConvoStatus(' +
      jsq(c.id) + ',\'closed\')">Gespräch schließen</button></div>';
  }
  if (c.status === 'closed' && c.prospect_status === 'opted_out') {
    // Reopening wouldn't help: Harvey never answers someone who opted out.
    return '<div class="convo-actions"><span class="muted">' + icon('alert') + 'Abgemeldet. Harvey schreibt dieser Person nicht mehr.</span>' +
      '<button class="btn btn-secondary btn-sm" onclick="showOptOut(' + jsq(c.prospect_id) + ',' + jsq(c.prospect_email) +
      ')">Warum abgemeldet?</button></div>';
  }
  if (c.status === 'closed') {
    return '<div class="convo-actions"><button class="btn btn-ghost btn-sm" onclick="setConvoStatus(' +
      jsq(c.id) + ',\'open\')">Wieder öffnen</button></div>';
  }
  return '';
}

async function setConvoStatus(id, status) {
  const data = await api('/api/conversations/' + encodeURIComponent(id) + '/status', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({status: status}),
  });
  if (data && data.success) showToast(status === 'open' ? 'Wieder offen. Harvey antwortet auf die nächste Nachricht.' : 'Geschlossen. Eine neue Antwort startet ein neues Gespräch.', 'success');
  else showToast(failMsg(data, 'Das Gespräch konnte nicht geändert werden.'), 'error');
  loadConversations();
  loadToday();
}

async function loadActivity() {
  const el = document.getElementById('activity-list');
  const data = await api('/api/activity');
  if (!data) { el.innerHTML = offlineState(); return; }
  if (!data.length) {
    el.innerHTML = emptyState(icon('activity'), 'Noch keine Aktivität',
      'Harvey hat noch nichts getan. Jeder gefundene Kontakt, jede geschriebene E-Mail und jede bearbeitete Antwort erscheint hier, sobald es passiert.');
    return;
  }
  let html = '<div class="card"><div class="timeline">';
  let day = '';
  for (const a of data) {
    const label = dayLabel(a.created_at);
    if (label !== day) { day = label; html += '<div class="tl-day">' + escHtml(label) + '</div>'; }
    html += '<div class="tl-item' + (a.agent === 'main' ? ' quiet' : '') + '"><span class="tl-time">' + timeOfDay(a.created_at) + '</span>' +
      '<span class="tl-icon">' + icon(own(AGENT_ICONS, a.agent) || 'activity') + '</span>' +
      '<span class="tl-text">' + actionText(a) + '</span>' +
      '<span class="tl-agent">' + escHtml(a.agent) + '</span></div>';
  }
  el.innerHTML = html + '</div></div>';
}

// ── Init & live refresh ──

loadToday();
loadTrend();
loadFunnel();
loadSetupStatus();
loadRuns();
loadTodayActivity();
loadHarveyStatus();

loadLiveStatus();

// Agent status: quick poll (not while locked: it would only be refused)
setInterval(() => { if (!_locked) loadHarveyStatus(); }, 8000);

// Live status panel: refreshed while the Today tab is open and visible.
setInterval(() => {
  if (!document.hidden && !_locked && currentTab === 'today') loadLiveStatus();
}, 10000);

// Nav counts stay live wherever you are, so "something needs me" is visible
// from any tab without polling that tab's contents.
setInterval(async () => {
  if (document.hidden || _locked || currentTab === 'today') return;
  const data = await api('/api/today');
  if (!data) return;
  navCount('nav-today', (data.items || []).filter(i => i.tone !== 'good').length);
  navCount('nav-outbox', (data.stats || {}).outbox_pending || 0);
}, 20000);

// Data tabs: auto-refresh live views without clobbering anything in progress.
// Outbox, settings and help are deliberately excluded — re-rendering the desk
// under someone mid-decision loses their place, and settings may be mid-edit.
setInterval(() => {
  if (document.hidden || _locked) return;
  switch (currentTab) {
    case 'today': loadToday(); loadTrend(); loadFunnel(); loadRuns(); loadTodayActivity(); break;
    case 'companies': if (!companyDrill) loadCompanies(); break;
    case 'prospects': loadProspects(); break;
    case 'campaigns': loadCampaigns(); break;
    case 'conversations': loadConversations(); break;
    case 'activity': loadActivity(); break;
    case 'usage': loadUsage(); break;
    case 'controls': loadLogs(); break;
  }
}, 15000);
