/*
 * RedFlagged – Selector Configuration
 * ------------------------------------
 * A dedicated module for the "Selectors" tab. It lets a future user define every
 * page selector the Python automation relies on, separated by each portal link
 * (security.microsoft.com, purview.microsoft.com, outlook.office.com,
 * vistra.service-now.com, odin.landpower.net).
 *
 * Extensible: users can ADD their own portals (links) and functions (selector
 * fields) from the UI. Built-in defaults mirror function/tools.py and stay
 * resettable; user additions live in a separate localStorage key.
 *
 * Each selector lives in its own textbox. Volatile auto-generated numbers
 * (cell-745, SearchBox1612, ms-searchux-input-0) can be hardened with the
 * "Make robust" option.
 */
(function () {
  'use strict';

  var STORAGE_KEY = 'rf_selector_config';   // field values: { groupId: { fieldKey: value } }
  var CUSTOM_KEY = 'rf_selector_custom';     // user additions: { portals: [...], fields: { groupId: [...] } }
  var ACTION_KEY = 'rf_selector_actions';    // per-field action overrides: { groupId: { fieldKey: { action, value } } }

  // type: 'text' -> single-line input (URL / single value / search filter)
  // type: 'list' -> one textbox per selector (first match wins, rest are fallbacks)
  var SELECTOR_GROUPS = [
    {
      id: 'defender',
      name: 'Microsoft Defender / Security',
      url: 'https://security.microsoft.com',
      host: 'security.microsoft.com',
      initials: 'MD',
      accent: '#3b82f6',
      description: 'Incidents queue – search and incident-ID selectors.',
      fields: [
        {
          key: 'incidentsUrl',
          label: 'Incidents page URL',
          source: 'INCIDENTS_URL',
          type: 'text',
          default: 'https://security.microsoft.com/incidents',
          help: 'Landing URL opened for the incidents queue.'
        },
        {
          key: 'incidentsSearchLocators',
          label: 'Incident search box selectors',
          source: 'INCIDENTS_SEARCH_LOCATORS',
          type: 'list',
          default: [
            "input#SearchBox1612.ms-SearchBox-field.field-533",
            "input[id^='SearchBox'][class*='ms-SearchBox-field']",
            "input.ms-SearchBox-field",
            "input[aria-label*='Search']"
          ],
          help: 'One selector per box. First match is used; the rest are fallbacks.'
        },
        {
          key: 'incidentIdLocators',
          label: 'Incident ID cell selectors',
          source: 'INCIDENT_ID_LOCATORS',
          type: 'list',
          default: [
            "div.ms-DetailsRow-cell.cell-745.cellUnpadded-743"
          ],
          help: 'Selector(s) for the grid cell that contains the incident ID.'
        },
        {
          key: 'incidentsLauncherSearch',
          label: 'Incidents launcher search input (primary + fallbacks)',
          source: 'page_actions[0]',
          type: 'list',
          default: [
            "input#SearchBox287.ms-SearchBox-field.field-531",
            "input[id^='SearchBox'][class*='ms-SearchBox-field']",
            "input.ms-SearchBox-field",
            "input[aria-label*='Search']",
            "input[placeholder*='Search']"
          ],
          help: 'Used when opening the startup tab. Class names/IDs can be dynamic, so keep the attribute-based fallbacks.'
        }
      ]
    },
    {
      id: 'purview',
      name: 'Microsoft Purview',
      url: 'https://purview.microsoft.com',
      host: 'purview.microsoft.com',
      initials: 'PV',
      accent: '#a855f7',
      description: 'Data Loss Prevention alerts page – search input selectors.',
      fields: [
        {
          key: 'alertsUrl',
          label: 'DLP alerts page URL',
          source: 'page_actions[1].url',
          type: 'text',
          default: 'https://purview.microsoft.com/datalossprevention/alertspage',
          help: 'DLP alerts page opened to read alert rows.'
        },
        {
          key: 'alertsSearchLocators',
          label: 'Alerts search input (primary + fallbacks)',
          source: 'page_actions[1]',
          type: 'list',
          default: [
            "#ms-searchux-input-0",
            "input[id^='ms-searchux-input-']",
            "input[type='search']"
          ],
          help: 'Primary is the id-based input (By.ID ms-searchux-input-0); the rest are attribute fallbacks.'
        }
      ]
    },
    {
      id: 'outlook',
      name: 'Outlook on the web',
      url: 'https://outlook.office.com',
      host: 'outlook.office.com',
      initials: 'OL',
      accent: '#38bdf8',
      description: 'Mailbox search used to locate the alert email.',
      fields: [
        {
          key: 'outlookUrl',
          label: 'Outlook URL',
          source: 'page_actions[2].url',
          type: 'text',
          default: 'https://outlook.office.com/',
          help: 'Outlook on the web landing URL.'
        },
        {
          key: 'outlookSearchLocators',
          label: 'Outlook search box selectors',
          source: 'OUTLOOK_SEARCH_LOCATORS',
          type: 'list',
          default: [
            "input#topSearchInput.uz227.GgHzM",
            "input#topSearchInput.uz227.EmdGf",
            "#topSearchInput",
            "input[aria-label*='Search']"
          ],
          help: 'One selector per box. #topSearchInput is the stable id-based fallback.'
        },
        {
          key: 'alertSenderFilter',
          label: 'Alert sender search filter',
          source: 'OUTLOOK_ALERT_SENDER_FILTER',
          type: 'text',
          default: 'from:Office365Alerts@microsoft.com',
          help: 'Search query prefix used to narrow the mailbox to alert emails.'
        }
      ]
    },
    {
      id: 'servicenow',
      name: 'ServiceNow',
      url: 'https://vistra.service-now.com',
      host: 'vistra.service-now.com',
      initials: 'SN',
      accent: '#22c55e',
      description: 'Incident list opened for ticket cross-reference.',
      fields: [
        {
          key: 'incidentListUrl',
          label: 'Incident list URL',
          source: 'page_actions[3].url',
          type: 'text',
          default: 'https://vistra.service-now.com/nav_to.do?uri=%2Fincident.do%3Fsys_id%3D-1%26sysparm_query%3Dactive%3Dtrue%26sysparm_stack%3Dincident_list.do%3Fsysparm_query%3Dactive%3Dtrue',
          help: 'Deep link to the active incident list. No element selectors are defined for this portal.'
        }
      ]
    },
    {
      id: 'odin',
      name: 'Odin AI',
      url: 'https://odin.landpower.net',
      host: 'odin.landpower.net',
      initials: 'OD',
      accent: '#f59e0b',
      description: 'Chat input, file upload, send button, and response selectors.',
      fields: [
        {
          key: 'odinUrl',
          label: 'Odin URL',
          source: 'ODIN_URL',
          type: 'text',
          default: 'https://odin.landpower.net/',
          help: 'Odin chatbot landing URL.'
        },
        {
          key: 'odinPromptInputSelectors',
          label: 'Prompt input selectors',
          source: 'ODIN_PROMPT_INPUT_SELECTORS',
          type: 'list',
          default: [
            ".form-control.m-0.w-100.border-0.bg-transparent.chat-input.chat-input-normal",
            "input.form-control.m-0.w-100.border-0.bg-transparent.chat-input.chat-input-normal",
            "textarea",
            "textarea[placeholder*='Ask']",
            "input[placeholder*='Ask']",
            "[role='textbox'][contenteditable='true']",
            "div[contenteditable='true']"
          ],
          help: 'Where the prompt text is typed. One selector per box.'
        },
        {
          key: 'odinFileInputSelectors',
          label: 'File upload input selectors',
          source: 'ODIN_FILE_INPUT_SELECTORS',
          type: 'list',
          default: [
            "input#nputFilesOdin",
            "input#inputFilesOdin",
            "input[type='file'][id*='filesodin']",
            "input[type='file'][name*='filesodin']",
            "input[type='file']#nputFilesOdin",
            "input[type='file']#inputFilesOdin",
            "input[type='file'][multiple]",
            "input[type='file']"
          ],
          help: 'File <input> used to attach evidence to Odin.'
        },
        {
          key: 'odinSendButtonSelectors',
          label: 'Send button selectors',
          source: 'ODIN_SEND_BUTTON_SELECTORS',
          type: 'list',
          default: [
            "button.btn.btn-outline-secondary.fw-bold.ms-1.me-1",
            "button[aria-label*='Send']",
            "button[title*='Send']",
            "button[type='submit']",
            "[role='button'][aria-label*='Send']"
          ],
          help: 'Button that submits the prompt.'
        },
        {
          key: 'odinResponseBlockSelectors',
          label: 'Response block selectors',
          source: 'ODIN_RESPONSE_BLOCK_SELECTORS',
          type: 'list',
          default: [
            ".flex.flex-grow.flex-col.w-100",
            "[data-message-author-role='assistant']",
            "[data-testid*='assistant']",
            "article",
            "[role='article']",
            "[class*='markdown']",
            "[class*='prose']",
            "[class*='message']"
          ],
          help: 'Container(s) that hold the assistant response text.'
        }
      ]
    }
  ];

  var ACCENT_PRESETS = ['#3b82f6', '#a855f7', '#38bdf8', '#22c55e', '#f59e0b', '#ec4899', '#14b8a6', '#ef4444', '#64748b'];

  // The interaction the automation performs on a selector ("the function").
  var ACTIONS = [
    { key: 'none', label: 'Static / none', color: '#94a3b8' },
    { key: 'navigate', label: 'Navigate (open URL)', color: '#a855f7' },
    { key: 'click', label: 'Click', color: '#3b82f6' },
    { key: 'sendkeys', label: 'Send Keys (type)', color: '#f59e0b', needsValue: true, valueLabel: 'Keys / text to send', valuePlaceholder: 'Text to type (blank = filled by automation)' },
    { key: 'read', label: 'Read text', color: '#14b8a6' },
    { key: 'wait', label: 'Wait for element', color: '#64748b', needsValue: true, valueLabel: 'Timeout (seconds)', valuePlaceholder: 'e.g. 30' },
    { key: 'upload', label: 'Upload file', color: '#ec4899', needsValue: true, valueLabel: 'File path', valuePlaceholder: 'Path or {artifact_path}' },
    { key: 'submit', label: 'Submit / press Enter', color: '#2563eb' },
    { key: 'clear', label: 'Clear field', color: '#ef4444' }
  ];

  function actionDef(key) {
    for (var i = 0; i < ACTIONS.length; i++) { if (ACTIONS[i].key === key) return ACTIONS[i]; }
    return ACTIONS[0];
  }

  // Default action per built-in field, mirroring what tools.py does with each selector.
  var DEFAULT_ACTIONS = {
    'defender.incidentsUrl': 'navigate',
    'defender.incidentsSearchLocators': 'sendkeys',
    'defender.incidentIdLocators': 'read',
    'defender.incidentsLauncherSearch': 'sendkeys',
    'purview.alertsUrl': 'navigate',
    'purview.alertsSearchLocators': 'sendkeys',
    'outlook.outlookUrl': 'navigate',
    'outlook.outlookSearchLocators': 'sendkeys',
    'outlook.alertSenderFilter': 'none',
    'servicenow.incidentListUrl': 'navigate',
    'odin.odinUrl': 'navigate',
    'odin.odinPromptInputSelectors': 'sendkeys',
    'odin.odinFileInputSelectors': 'upload',
    'odin.odinSendButtonSelectors': 'click',
    'odin.odinResponseBlockSelectors': 'read'
  };
  SELECTOR_GROUPS.forEach(function (g) {
    g.fields.forEach(function (f) {
      if (!f.action) f.action = DEFAULT_ACTIONS[g.id + '.' + f.key] || 'none';
      if (f.actionValue === undefined) f.actionValue = '';
    });
  });

  // ---- persistence helpers -------------------------------------------------

  function loadStored() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return {};
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (err) {
      return {};
    }
  }

  function persist(state) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  }

  function loadCustom() {
    try {
      var raw = localStorage.getItem(CUSTOM_KEY);
      if (!raw) return { portals: [], fields: {} };
      var parsed = JSON.parse(raw);
      return {
        portals: Array.isArray(parsed.portals) ? parsed.portals : [],
        fields: (parsed.fields && typeof parsed.fields === 'object') ? parsed.fields : {}
      };
    } catch (err) {
      return { portals: [], fields: {} };
    }
  }

  function persistCustom(custom) {
    localStorage.setItem(CUSTOM_KEY, JSON.stringify(custom));
  }

  // Per-field action overrides: { groupId: { fieldKey: { action, value } } }
  function loadActions() {
    try {
      var raw = localStorage.getItem(ACTION_KEY);
      if (!raw) return {};
      var parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch (err) {
      return {};
    }
  }

  function persistActions(actions) {
    localStorage.setItem(ACTION_KEY, JSON.stringify(actions));
  }

  function effectiveAction(group, field) {
    var actions = loadActions();
    var override = actions[group.id] && actions[group.id][field.key];
    if (override && override.action) return override.action;
    return field.action || 'none';
  }

  function effectiveActionValue(group, field) {
    var actions = loadActions();
    var override = actions[group.id] && actions[group.id][field.key];
    if (override && override.value !== undefined) return override.value;
    return field.actionValue || '';
  }

  function setActionOverride(groupId, fieldKey, action, value) {
    var actions = loadActions();
    if (!actions[groupId]) actions[groupId] = {};
    actions[groupId][fieldKey] = { action: action, value: value };
    persistActions(actions);
  }

  function clearGroupActions(groupId) {
    var actions = loadActions();
    if (actions[groupId]) { delete actions[groupId]; persistActions(actions); }
  }

  function clearFieldAction(groupId, fieldKey) {
    var actions = loadActions();
    if (actions[groupId] && actions[groupId][fieldKey]) {
      delete actions[groupId][fieldKey];
      persistActions(actions);
    }
  }

  // ---- identifiers / effective group model --------------------------------

  function slugify(text) {
    return String(text || '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
      .replace(/^-+|-+$/g, '')
      .slice(0, 40) || 'item';
  }

  function uniqueKey(base, taken) {
    var key = base;
    var i = 2;
    while (taken.indexOf(key) !== -1) { key = base + '-' + i; i += 1; }
    return key;
  }

  function initialsFor(name) {
    var parts = String(name || '').trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return 'NA';
    if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
    return (parts[0][0] + parts[1][0]).toUpperCase();
  }

  function deriveHost(url) {
    try { return new URL(url).host || url; }
    catch (e) { return String(url || '').replace(/^https?:\/\//, '').split('/')[0] || String(url || ''); }
  }

  function withScheme(url) {
    var u = String(url || '').trim();
    if (!u) return u;
    return /^[a-z]+:\/\//i.test(u) ? u : 'https://' + u;
  }

  function normalizeCustomField(f) {
    var type = f.type === 'text' ? 'text' : 'list';
    return {
      key: f.key,
      label: f.label || f.key,
      type: type,
      help: f.help || '',
      source: f.source || 'custom',
      default: f.default !== undefined ? f.default : (type === 'list' ? [] : ''),
      action: f.action || 'none',
      actionValue: f.actionValue || '',
      custom: true
    };
  }

  // Built-in groups (augmented with any custom fields) + fully custom portals.
  function getEffectiveGroups() {
    var custom = loadCustom();
    var groups = SELECTOR_GROUPS.map(function (g) {
      var extra = (custom.fields[g.id] || []).map(normalizeCustomField);
      return {
        id: g.id, name: g.name, url: g.url, host: g.host, initials: g.initials,
        accent: g.accent, description: g.description, custom: false,
        fields: g.fields.concat(extra)
      };
    });
    custom.portals.forEach(function (p) {
      var extra = (custom.fields[p.id] || []).map(normalizeCustomField);
      groups.push({
        id: p.id, name: p.name, url: p.url, host: p.host || deriveHost(p.url),
        initials: p.initials || initialsFor(p.name), accent: p.accent || '#64748b',
        description: p.description || '', custom: true, fields: extra
      });
    });
    return groups;
  }

  function defaultValue(field) {
    return field.type === 'list' ? (Array.isArray(field.default) ? field.default.slice() : []) : (field.default || '');
  }

  function effectiveValue(state, group, field) {
    var groupState = state[group.id];
    if (groupState && Object.prototype.hasOwnProperty.call(groupState, field.key)) {
      return groupState[field.key];
    }
    return defaultValue(field);
  }

  function valuesEqual(a, b) {
    if (Array.isArray(a) || Array.isArray(b)) {
      var aa = Array.isArray(a) ? a : [a];
      var bb = Array.isArray(b) ? b : [b];
      if (aa.length !== bb.length) return false;
      for (var i = 0; i < aa.length; i++) {
        if (String(aa[i]) !== String(bb[i])) return false;
      }
      return true;
    }
    return String(a == null ? '' : a) === String(b == null ? '' : b);
  }

  function isFieldCustomized(state, group, field) {
    return !valuesEqual(effectiveValue(state, group, field), defaultValue(field));
  }

  function groupCustomizedCount(state, group) {
    return group.fields.reduce(function (n, field) {
      return n + (isFieldCustomized(state, group, field) ? 1 : 0);
    }, 0);
  }

  function asArray(value) {
    return Array.isArray(value) ? value : (value === undefined || value === null || value === '' ? [] : [value]);
  }

  function parseListValue(text) {
    return String(text || '')
      .split('\n')
      .map(function (line) { return line.trim(); })
      .filter(function (line) { return line.length > 0; });
  }

  // ---- robustness (dynamic-number handling) --------------------------------

  function hasDynamicNumbers(selector) {
    var sel = String(selector || '');
    var stripped = sel.replace(/\[[^\]]*\]/g, '');
    if (/#[\w-]*\d/.test(stripped)) return true;
    var classes = stripped.match(/\.[A-Za-z_][\w-]*/g) || [];
    return classes.some(function (c) { return /\d{2,}/.test(c); });
  }

  function robustifySelector(selector) {
    var sel = String(selector || '').trim();
    if (!sel) return sel;
    if (/[\s>+~,]/.test(sel.replace(/\[[^\]]*\]/g, ''))) return sel;

    var attrs = [];
    var core = sel.replace(/\[[^\]]*\]/g, function (m) { attrs.push(m); return ''; });

    var tag = '';
    var tagMatch = core.match(/^[A-Za-z][\w-]*/);
    if (tagMatch) { tag = tagMatch[0]; core = core.slice(tagMatch[0].length); }

    var idPart = '';
    core = core.replace(/#([A-Za-z_][\w-]*)/, function (_, id) {
      if (/\d/.test(id)) {
        var prefix = id.replace(/\d.*$/, '');
        idPart = prefix ? "[id^='" + prefix + "']" : '#' + id;
      } else {
        idPart = '#' + id;
      }
      return '';
    });

    var classParts = [];
    var classRe = /\.([A-Za-z_][\w-]*)/g;
    var m;
    while ((m = classRe.exec(core)) !== null) {
      var name = m[1];
      if (/\d{2,}/.test(name)) {
        var stem = name.replace(/\d.*$/, '');
        classParts.push(stem ? "[class*='" + stem + "']" : '.' + name);
      } else {
        classParts.push('.' + name);
      }
    }

    return (tag + idPart + classParts.join('') + attrs.join('')) || sel;
  }

  function countDynamicSelectors(state) {
    var total = 0;
    getEffectiveGroups().forEach(function (group) {
      group.fields.forEach(function (field) {
        if (field.type !== 'list') return;
        asArray(effectiveValue(state, group, field)).forEach(function (sel) {
          if (hasDynamicNumbers(sel)) total += 1;
        });
      });
    });
    return total;
  }

  function countSelectorFields(group) {
    return group.fields.reduce(function (n, field) {
      return n + (field.type === 'list' ? 1 : 0);
    }, 0);
  }

  // ---- notifications -------------------------------------------------------

  function notify(title, text, status) {
    if (typeof window.showNotification === 'function') {
      window.showNotification({ title: title, text: text, status: status || 'Success', source: 'Selectors' });
    }
  }

  function setFeedback(message) {
    var node = document.getElementById('selector-config-feedback');
    if (node) node.textContent = message;
  }

  // ---- small DOM helpers ---------------------------------------------------

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }

  var ICON_SPARKLE = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m12 3 1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9L12 3Z"/><path d="M19 14v4"/><path d="M21 16h-4"/><path d="M5 4v2"/><path d="M6 5H4"/></svg>';
  var ICON_X = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>';
  var ICON_TRASH = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/></svg>';
  var ICON_PLUS = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14"/><path d="M5 12h14"/></svg>';

  // ---- rendering -----------------------------------------------------------

  var container = null;
  var collapsed = {}; // groupId -> bool

  function fieldSearchText(group, field, values) {
    return (group.name + ' ' + group.url + ' ' + field.label + ' ' + (field.source || '') + ' ' + values.join(' ')).toLowerCase();
  }

  function updateRowVolatile(row, value) {
    if (!row) return;
    var volatile = hasDynamicNumbers(value);
    row.classList.toggle('is-volatile', volatile);
    var input = row.querySelector('.sc-sel-input');
    if (input) {
      input.title = volatile
        ? 'Contains auto-generated numbers (id/class) that may change. Click the ✦ button to make it robust.'
        : '';
    }
  }

  function refreshFieldMeta(wrap, group, field) {
    var rows = wrap.querySelector('.sc-sel-rows');
    if (!rows) return;
    var values = [];
    Array.prototype.forEach.call(rows.children, function (row, i) {
      var idx = row.querySelector('.sc-sel-index');
      if (idx) idx.textContent = String(i + 1);
      var input = row.querySelector('.sc-sel-input');
      if (input) values.push(input.value);
    });
    var pill = wrap.querySelector('.sc-field-count');
    if (pill) pill.textContent = values.length + (values.length === 1 ? ' selector' : ' selectors');
    wrap.setAttribute('data-search', fieldSearchText(group, field, values));
  }

  function makeSelectorRow(group, field, value, wrap) {
    var row = el('div', 'sc-sel-row');
    row.appendChild(el('span', 'sc-sel-index', ''));

    var input = el('input', 'sc-input sc-sel-input');
    input.type = 'text';
    input.spellcheck = false;
    input.value = String(value == null ? '' : value);
    input.setAttribute('aria-label', group.name + ' – ' + field.label + ' selector');
    row.appendChild(input);

    var robustBtn = el('button', 'sc-icon-btn sc-sel-robust');
    robustBtn.type = 'button';
    robustBtn.title = 'Make robust – generalize dynamic numbers (e.g. cell-745 → [class*="cell-"])';
    robustBtn.setAttribute('aria-label', 'Make this selector robust');
    robustBtn.innerHTML = ICON_SPARKLE;
    robustBtn.addEventListener('click', function () {
      var robust = robustifySelector(input.value);
      if (robust !== input.value) {
        input.value = robust;
        notify('Selector Robustified', 'Generalized dynamic numbers in this selector.', 'Success');
      } else {
        notify('Already Robust', 'No dynamic numbers detected in this selector.', 'Warning');
      }
      updateRowVolatile(row, input.value);
      refreshFieldMeta(wrap, group, field);
    });
    row.appendChild(robustBtn);

    var removeBtn = el('button', 'sc-icon-btn sc-sel-remove');
    removeBtn.type = 'button';
    removeBtn.title = 'Remove this selector';
    removeBtn.setAttribute('aria-label', 'Remove this selector');
    removeBtn.innerHTML = ICON_X;
    removeBtn.addEventListener('click', function () {
      row.remove();
      refreshFieldMeta(wrap, group, field);
    });
    row.appendChild(removeBtn);

    input.addEventListener('input', function () {
      updateRowVolatile(row, input.value);
      refreshFieldMeta(wrap, group, field);
    });

    updateRowVolatile(row, input.value);
    return row;
  }

  function buildSelectorList(group, field, value, wrap) {
    var list = el('div', 'sc-sel-list');
    list.id = 'selcfg-' + group.id + '-' + field.key;
    list.setAttribute('data-group', group.id);
    list.setAttribute('data-field', field.key);

    var rows = el('div', 'sc-sel-rows');
    list.appendChild(rows);

    // Always show at least one empty box so there is somewhere to type
    // (e.g. a newly added function). Empty boxes are dropped on save.
    var seed = asArray(value);
    if (seed.length === 0) seed = [''];
    seed.forEach(function (sel) {
      rows.appendChild(makeSelectorRow(group, field, sel, wrap));
    });

    var tools = el('div', 'sc-sel-tools');

    var addBtn = el('button', 'sc-mini-btn', '+ Add selector');
    addBtn.type = 'button';
    addBtn.addEventListener('click', function () {
      var row = makeSelectorRow(group, field, '', wrap);
      rows.appendChild(row);
      refreshFieldMeta(wrap, group, field);
      var input = row.querySelector('.sc-sel-input');
      if (input) input.focus();
    });
    tools.appendChild(addBtn);

    var robustAll = el('button', 'sc-mini-btn sc-mini-btn-robust');
    robustAll.type = 'button';
    robustAll.innerHTML = ICON_SPARKLE + '<span>Make all robust</span>';
    robustAll.title = 'Generalize dynamic numbers in every selector in this field';
    robustAll.addEventListener('click', function () {
      var changed = 0;
      rows.querySelectorAll('.sc-sel-row').forEach(function (row) {
        var input = row.querySelector('.sc-sel-input');
        if (!input) return;
        var robust = robustifySelector(input.value);
        if (robust !== input.value) { input.value = robust; changed += 1; }
        updateRowVolatile(row, input.value);
      });
      refreshFieldMeta(wrap, group, field);
      notify(
        changed ? 'Selectors Robustified' : 'Already Robust',
        changed
          ? changed + ' selector(s) generalized in “' + field.label + '”.'
          : 'No dynamic numbers found in “' + field.label + '”.',
        changed ? 'Success' : 'Warning'
      );
    });
    tools.appendChild(robustAll);

    list.appendChild(tools);
    return list;
  }

  function renderField(group, field, state) {
    var value = effectiveValue(state, group, field);
    var wrap = el('div', 'sc-field');

    var head = el('div', 'sc-field-head');
    var labelWrap = el('div', 'sc-field-label-wrap');
    var label = el('label', 'sc-field-label', field.label);
    label.setAttribute('for', 'selcfg-' + group.id + '-' + field.key);
    labelWrap.appendChild(label);

    var countPill = el('span', 'sc-field-count');
    if (field.type === 'list') {
      var n = asArray(value).length;
      countPill.textContent = n + (n === 1 ? ' selector' : ' selectors');
    } else {
      countPill.textContent = 'value';
      countPill.classList.add('sc-field-count-value');
    }
    labelWrap.appendChild(countPill);

    if (field.custom) {
      var customTag = el('span', 'sc-tag sc-tag-customfield', 'Custom');
      labelWrap.appendChild(customTag);
    }
    head.appendChild(labelWrap);

    var headRight = el('div', 'sc-field-head-right');
    if (field.source) headRight.appendChild(el('span', 'sc-field-source mono', field.source));
    if (field.custom) {
      var delBtn = el('button', 'sc-field-remove');
      delBtn.type = 'button';
      delBtn.title = 'Delete this function';
      delBtn.setAttribute('aria-label', 'Delete function ' + field.label);
      delBtn.innerHTML = ICON_TRASH;
      delBtn.addEventListener('click', function () { removeCustomField(group.id, field.key, field.label); });
      headRight.appendChild(delBtn);
    }
    head.appendChild(headRight);
    wrap.appendChild(head);

    // Action row: what the automation does with this selector.
    var actionRow = el('div', 'sc-action-row');
    actionRow.appendChild(el('span', 'sc-action-label', 'Action'));

    var actionSelect = el('select', 'sc-action-select');
    actionSelect.setAttribute('aria-label', group.name + ' – ' + field.label + ' action');
    ACTIONS.forEach(function (a) {
      var opt = el('option', '', a.label);
      opt.value = a.key;
      actionSelect.appendChild(opt);
    });
    var curAction = effectiveAction(group, field);
    actionSelect.value = curAction;
    actionSelect.setAttribute('data-action', curAction);
    actionSelect.style.setProperty('--act-color', actionDef(curAction).color);
    actionRow.appendChild(actionSelect);

    var valueWrap = el('label', 'sc-action-value');
    var valueLabel = el('span', 'sc-action-value-label', actionDef(curAction).valueLabel || 'Value');
    var valueInput = el('input', 'sc-input sc-action-value-input');
    valueInput.type = 'text';
    valueInput.spellcheck = false;
    valueInput.value = effectiveActionValue(group, field);
    valueInput.placeholder = actionDef(curAction).valuePlaceholder || '';
    valueWrap.appendChild(valueLabel);
    valueWrap.appendChild(valueInput);
    actionRow.appendChild(valueWrap);

    function syncActionValue() {
      var def = actionDef(actionSelect.value);
      valueWrap.style.display = def.needsValue ? '' : 'none';
      valueLabel.textContent = def.valueLabel || 'Value';
      valueInput.placeholder = def.valuePlaceholder || '';
    }
    syncActionValue();

    actionSelect.addEventListener('change', function () {
      actionSelect.setAttribute('data-action', actionSelect.value);
      actionSelect.style.setProperty('--act-color', actionDef(actionSelect.value).color);
      syncActionValue();
      setActionOverride(group.id, field.key, actionSelect.value, valueInput.value);
    });
    valueInput.addEventListener('input', function () {
      setActionOverride(group.id, field.key, actionSelect.value, valueInput.value);
    });
    wrap.appendChild(actionRow);

    if (field.type === 'list') {
      wrap.appendChild(buildSelectorList(group, field, value, wrap));
      refreshFieldMeta(wrap, group, field);
    } else {
      var input = el('input', 'sc-input');
      input.type = 'text';
      input.spellcheck = false;
      input.value = String(value == null ? '' : value);
      input.id = 'selcfg-' + group.id + '-' + field.key;
      input.setAttribute('data-group', group.id);
      input.setAttribute('data-field', field.key);
      input.setAttribute('aria-label', group.name + ' – ' + field.label);
      input.addEventListener('input', function () {
        wrap.setAttribute('data-search', fieldSearchText(group, field, [input.value]));
      });
      wrap.appendChild(input);
      wrap.setAttribute('data-search', fieldSearchText(group, field, [String(value || '')]));
    }

    if (field.help) wrap.appendChild(el('small', 'sc-field-help mono', field.help));
    return wrap;
  }

  function chevronSvg() {
    var span = el('span', 'sc-chevron');
    span.innerHTML = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>';
    return span;
  }

  // Inline "add function" form rendered inside a portal body.
  function buildAddFieldForm(group) {
    var panel = el('div', 'sc-addform');
    panel.hidden = true;
    panel.appendChild(el('div', 'sc-addform-title', 'Add function to ' + group.name));

    var grid = el('div', 'sc-addform-grid');

    var nameField = el('label', 'sc-addform-field');
    nameField.appendChild(el('span', 'sc-addform-label', 'Function name'));
    var nameInput = el('input', 'sc-input');
    nameInput.type = 'text';
    nameInput.placeholder = 'e.g. Dismiss button selectors';
    nameField.appendChild(nameInput);
    grid.appendChild(nameField);

    var typeField = el('label', 'sc-addform-field');
    typeField.appendChild(el('span', 'sc-addform-label', 'Type'));
    var typeSel = el('select', 'sc-input');
    var optList = el('option', '', 'Multiple selectors (with fallbacks)'); optList.value = 'list';
    var optText = el('option', '', 'Single value (URL / text)'); optText.value = 'text';
    typeSel.appendChild(optList);
    typeSel.appendChild(optText);
    typeField.appendChild(typeSel);
    grid.appendChild(typeField);

    var actionField = el('label', 'sc-addform-field');
    actionField.appendChild(el('span', 'sc-addform-label', 'Action (what to do)'));
    var actionSel = el('select', 'sc-input');
    ACTIONS.forEach(function (a) {
      var opt = el('option', '', a.label);
      opt.value = a.key;
      actionSel.appendChild(opt);
    });
    actionSel.value = 'click';
    actionField.appendChild(actionSel);
    grid.appendChild(actionField);

    var actionValueField = el('label', 'sc-addform-field');
    var actionValueLabel = el('span', 'sc-addform-label', actionDef('click').valueLabel || 'Value');
    var actionValueInput = el('input', 'sc-input');
    actionValueInput.type = 'text';
    actionValueField.appendChild(actionValueLabel);
    actionValueField.appendChild(actionValueInput);
    grid.appendChild(actionValueField);

    function syncAddActionValue() {
      var def = actionDef(actionSel.value);
      actionValueField.style.display = def.needsValue ? '' : 'none';
      actionValueLabel.textContent = def.valueLabel || 'Value';
      actionValueInput.placeholder = def.valuePlaceholder || '';
    }
    syncAddActionValue();
    actionSel.addEventListener('change', syncAddActionValue);

    var helpField = el('label', 'sc-addform-field sc-addform-field-wide');
    helpField.appendChild(el('span', 'sc-addform-label', 'Description (optional)'));
    var helpInput = el('input', 'sc-input');
    helpInput.type = 'text';
    helpInput.placeholder = 'What this selector targets…';
    helpField.appendChild(helpInput);
    grid.appendChild(helpField);

    panel.appendChild(grid);

    var actions = el('div', 'sc-addform-actions');
    var addBtn = el('button', 'sc-btn sc-btn-primary sc-btn-sm', 'Add function');
    addBtn.type = 'button';
    addBtn.addEventListener('click', function () {
      var label = nameInput.value.trim();
      if (!label) { notify('Name Required', 'Enter a function name.', 'Warning'); nameInput.focus(); return; }
      addCustomField(group.id, label, typeSel.value, helpInput.value.trim(), actionSel.value, actionValueInput.value.trim());
    });
    var cancelBtn = el('button', 'sc-btn sc-btn-ghost sc-btn-sm', 'Cancel');
    cancelBtn.type = 'button';
    cancelBtn.addEventListener('click', function () { panel.hidden = true; });
    actions.appendChild(addBtn);
    actions.appendChild(cancelBtn);
    panel.appendChild(actions);

    return panel;
  }

  function renderGroup(group, state) {
    var customized = groupCustomizedCount(state, group);
    var card = el('section', 'sc-portal');
    card.setAttribute('data-group', group.id);
    card.style.setProperty('--sc-accent', group.accent);
    if (group.custom) card.classList.add('is-custom-portal');
    if (collapsed[group.id]) card.classList.add('is-collapsed');

    var head = el('button', 'sc-portal-head');
    head.type = 'button';
    head.setAttribute('aria-expanded', collapsed[group.id] ? 'false' : 'true');

    head.appendChild(el('span', 'sc-portal-avatar', group.initials));

    var meta = el('div', 'sc-portal-meta');
    var titleRow = el('div', 'sc-portal-title-row');
    titleRow.appendChild(el('h3', 'sc-portal-title', group.name));

    var sf = countSelectorFields(group);
    titleRow.appendChild(el('span', 'sc-tag', sf + ' selector' + (sf === 1 ? '' : 's')));

    if (group.custom) titleRow.appendChild(el('span', 'sc-tag sc-tag-customportal', 'Custom portal'));

    var customBadge = el('span', 'sc-tag sc-tag-custom', 'Customized');
    customBadge.title = customized + ' field(s) differ from defaults';
    if (!customized) customBadge.style.display = 'none';
    titleRow.appendChild(customBadge);
    meta.appendChild(titleRow);

    var sub = el('div', 'sc-portal-sub');
    sub.appendChild(el('span', 'sc-portal-host mono', group.host));
    if (group.description) {
      sub.appendChild(el('span', 'sc-portal-dot', '•'));
      sub.appendChild(el('span', 'sc-portal-desc', group.description));
    }
    meta.appendChild(sub);
    head.appendChild(meta);

    head.appendChild(chevronSvg());
    head.addEventListener('click', function () { toggleCollapse(group.id); });
    card.appendChild(head);

    var body = el('div', 'sc-portal-body');

    var linkRow = el('div', 'sc-portal-linkrow');
    var openLink = el('a', 'sc-portal-link mono');
    openLink.href = group.url;
    openLink.target = '_blank';
    openLink.rel = 'noopener noreferrer';
    openLink.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg> ' + group.url;
    linkRow.appendChild(openLink);
    body.appendChild(linkRow);

    var fieldsWrap = el('div', 'sc-fields');
    if (group.fields.length === 0) {
      fieldsWrap.appendChild(el('div', 'sc-empty-fields mono', 'No functions yet. Use “Add function” to define a selector for this portal.'));
    } else {
      group.fields.forEach(function (field) {
        fieldsWrap.appendChild(renderField(group, field, state));
      });
    }
    body.appendChild(fieldsWrap);

    var addFieldForm = buildAddFieldForm(group);
    body.appendChild(addFieldForm);

    var actions = el('div', 'sc-portal-actions');

    var addFnBtn = el('button', 'sc-btn sc-btn-ghost sc-btn-sm sc-add-function');
    addFnBtn.type = 'button';
    addFnBtn.innerHTML = ICON_PLUS + '<span>Add function</span>';
    addFnBtn.addEventListener('click', function () {
      addFieldForm.hidden = !addFieldForm.hidden;
      if (!addFieldForm.hidden) {
        var firstInput = addFieldForm.querySelector('input');
        if (firstInput) firstInput.focus();
      }
    });
    actions.appendChild(addFnBtn);

    var spacer = el('div', 'sc-portal-actions-spacer');
    actions.appendChild(spacer);

    if (group.custom) {
      var removePortalBtn = el('button', 'sc-btn sc-btn-danger sc-btn-sm', 'Remove portal');
      removePortalBtn.type = 'button';
      removePortalBtn.addEventListener('click', function () { removeCustomPortal(group.id, group.name); });
      actions.appendChild(removePortalBtn);
    }

    var saveBtn = el('button', 'sc-btn sc-btn-primary sc-btn-sm', 'Save');
    saveBtn.type = 'button';
    saveBtn.addEventListener('click', function () { saveGroup(group); });
    actions.appendChild(saveBtn);

    var resetBtn = el('button', 'sc-btn sc-btn-ghost sc-btn-sm', 'Reset');
    resetBtn.type = 'button';
    resetBtn.addEventListener('click', function () { resetGroup(group); });
    actions.appendChild(resetBtn);

    body.appendChild(actions);
    card.appendChild(body);
    return card;
  }

  function renderStats(state) {
    var statsEl = document.getElementById('selector-config-stats');
    if (!statsEl) return;
    var groups = getEffectiveGroups();
    var fields = groups.reduce(function (n, g) { return n + g.fields.length; }, 0);
    var customized = groups.reduce(function (n, g) { return n + groupCustomizedCount(state, g); }, 0);

    var defs = [
      { label: 'Portals', value: groups.length },
      { label: 'Selector fields', value: fields },
      { label: 'Customized', value: customized },
      { label: 'Dynamic numbers', value: countDynamicSelectors(state), accent: true }
    ];
    statsEl.textContent = '';
    defs.forEach(function (def) {
      var tile = el('div', 'sc-stat' + (def.accent ? ' sc-stat-dyn' : ''));
      tile.appendChild(el('span', 'sc-stat-value', String(def.value)));
      tile.appendChild(el('span', 'sc-stat-label', def.label));
      statsEl.appendChild(tile);
    });
  }

  function renderNav(state) {
    var navEl = document.getElementById('selector-config-nav');
    if (!navEl) return;
    navEl.textContent = '';
    getEffectiveGroups().forEach(function (group) {
      var chip = el('button', 'sc-nav-chip');
      chip.type = 'button';
      chip.setAttribute('data-group', group.id);
      chip.style.setProperty('--sc-accent', group.accent);
      chip.appendChild(el('span', 'sc-nav-dot'));
      chip.appendChild(el('span', 'sc-nav-label', group.name));
      var count = groupCustomizedCount(state, group);
      if (count) chip.appendChild(el('span', 'sc-nav-count', String(count)));
      chip.addEventListener('click', function () {
        collapsed[group.id] = false;
        var cardEl = container.querySelector('.sc-portal[data-group="' + group.id + '"]');
        if (cardEl) {
          cardEl.classList.remove('is-collapsed');
          var h = cardEl.querySelector('.sc-portal-head');
          if (h) h.setAttribute('aria-expanded', 'true');
          cardEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
          cardEl.classList.add('sc-flash');
          setTimeout(function () { cardEl.classList.remove('sc-flash'); }, 900);
        }
        updateExpandToggleLabel();
      });
      navEl.appendChild(chip);
    });
  }

  function feedbackText(state) {
    var groups = getEffectiveGroups();
    var fieldCount = groups.reduce(function (sum, g) { return sum + g.fields.length; }, 0);
    var customized = groups.reduce(function (n, g) { return n + groupCustomizedCount(state, g); }, 0);
    var dynamic = countDynamicSelectors(state);
    return groups.length + ' portals • ' + fieldCount + ' selector fields • ' +
      customized + ' customized • ' + dynamic + ' with dynamic numbers. Changes are saved to this browser.';
  }

  function render() {
    if (!container) return;
    var state = loadStored();
    container.textContent = '';
    getEffectiveGroups().forEach(function (group) {
      container.appendChild(renderGroup(group, state));
    });
    renderStats(state);
    renderNav(state);
    updateExpandToggleLabel();
    setFeedback(feedbackText(state));
  }

  // ---- collapse ------------------------------------------------------------

  function toggleCollapse(groupId) {
    collapsed[groupId] = !collapsed[groupId];
    var cardEl = container.querySelector('.sc-portal[data-group="' + groupId + '"]');
    if (cardEl) {
      cardEl.classList.toggle('is-collapsed', collapsed[groupId]);
      var h = cardEl.querySelector('.sc-portal-head');
      if (h) h.setAttribute('aria-expanded', collapsed[groupId] ? 'false' : 'true');
    }
    updateExpandToggleLabel();
  }

  function allCollapsed() {
    return getEffectiveGroups().every(function (g) { return collapsed[g.id]; });
  }

  function setAllCollapsed(value) {
    getEffectiveGroups().forEach(function (g) {
      collapsed[g.id] = value;
      var cardEl = container.querySelector('.sc-portal[data-group="' + g.id + '"]');
      if (cardEl) {
        cardEl.classList.toggle('is-collapsed', value);
        var h = cardEl.querySelector('.sc-portal-head');
        if (h) h.setAttribute('aria-expanded', value ? 'false' : 'true');
      }
    });
    updateExpandToggleLabel();
  }

  function updateExpandToggleLabel() {
    var btn = document.getElementById('selector-config-expand-toggle');
    if (btn) btn.textContent = allCollapsed() ? 'Expand all' : 'Collapse all';
  }

  // ---- read values from the DOM -------------------------------------------

  function readGroupValues(group) {
    var values = {};
    group.fields.forEach(function (field) {
      if (field.type === 'list') {
        var list = document.getElementById('selcfg-' + group.id + '-' + field.key);
        var arr = [];
        if (list) {
          list.querySelectorAll('.sc-sel-input').forEach(function (input) {
            var v = input.value.trim();
            if (v) arr.push(v);
          });
        }
        values[field.key] = arr;
      } else {
        var control = document.getElementById('selcfg-' + group.id + '-' + field.key);
        if (control) values[field.key] = control.value.trim();
      }
    });
    return values;
  }

  // Persist whatever is currently in the DOM (used before re-render so edits aren't lost).
  function commitCurrentValues() {
    var state = loadStored();
    getEffectiveGroups().forEach(function (group) {
      state[group.id] = readGroupValues(group);
    });
    persist(state);
  }

  // ---- custom portals / functions -----------------------------------------

  function addCustomField(groupId, label, type, help, action, actionValue) {
    commitCurrentValues();
    var custom = loadCustom();
    if (!Array.isArray(custom.fields[groupId])) custom.fields[groupId] = [];

    var group = getEffectiveGroups().filter(function (g) { return g.id === groupId; })[0];
    var takenKeys = group ? group.fields.map(function (f) { return f.key; }) : [];
    var key = uniqueKey(slugify(label), takenKeys);

    custom.fields[groupId].push({
      key: key,
      label: label,
      type: type === 'text' ? 'text' : 'list',
      help: help || '',
      source: 'custom',
      default: type === 'text' ? '' : [],
      action: action || 'none',
      actionValue: actionValue || ''
    });
    persistCustom(custom);
    render();
    applySearch();
    var actLabel = actionDef(action || 'none').label;
    notify('Function Added', '“' + label + '” added (' + actLabel + ').', 'Success');
  }

  function removeCustomField(groupId, fieldKey, label) {
    commitCurrentValues();
    var custom = loadCustom();
    custom.fields[groupId] = (custom.fields[groupId] || []).filter(function (f) { return f.key !== fieldKey; });
    persistCustom(custom);
    var state = loadStored();
    if (state[groupId]) { delete state[groupId][fieldKey]; persist(state); }
    clearFieldAction(groupId, fieldKey);
    render();
    applySearch();
    notify('Function Removed', '“' + (label || fieldKey) + '” removed.', 'Warning');
  }

  function addCustomPortal(name, url, accent, description) {
    commitCurrentValues();
    var custom = loadCustom();
    var takenIds = getEffectiveGroups().map(function (g) { return g.id; });
    var id = uniqueKey('x-' + slugify(name), takenIds);
    var fullUrl = withScheme(url);
    custom.portals.push({
      id: id,
      name: name,
      url: fullUrl,
      host: deriveHost(fullUrl),
      initials: initialsFor(name),
      accent: accent || '#64748b',
      description: description || ''
    });
    persistCustom(custom);
    collapsed[id] = false;
    render();
    applySearch();
    notify('Portal Added', '“' + name + '” added. Use “Add function” to define its selectors.', 'Success');
    var cardEl = container.querySelector('.sc-portal[data-group="' + id + '"]');
    if (cardEl) cardEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  function removeCustomPortal(portalId, name) {
    commitCurrentValues();
    var custom = loadCustom();
    custom.portals = custom.portals.filter(function (p) { return p.id !== portalId; });
    delete custom.fields[portalId];
    persistCustom(custom);
    var state = loadStored();
    delete state[portalId];
    persist(state);
    clearGroupActions(portalId);
    delete collapsed[portalId];
    render();
    applySearch();
    notify('Portal Removed', '“' + (name || portalId) + '” removed.', 'Warning');
  }

  // ---- actions -------------------------------------------------------------

  function refreshGroupBadges() {
    var state = loadStored();
    renderStats(state);
    renderNav(state);
    getEffectiveGroups().forEach(function (group) {
      var cardEl = container.querySelector('.sc-portal[data-group="' + group.id + '"]');
      if (!cardEl) return;
      var badge = cardEl.querySelector('.sc-tag-custom');
      if (badge) badge.style.display = groupCustomizedCount(state, group) ? '' : 'none';
    });
    setFeedback(feedbackText(state));
  }

  function saveGroup(group) {
    var state = loadStored();
    state[group.id] = readGroupValues(group);
    persist(state);
    refreshGroupBadges();
    notify('Selectors Saved', group.name + ' selectors saved.', 'Success');
  }

  function saveAll() {
    commitCurrentValues();
    refreshGroupBadges();
    notify('Selectors Saved', 'All portal selectors saved.', 'Success');
  }

  function resetGroup(group) {
    var state = loadStored();
    delete state[group.id];
    persist(state);
    clearGroupActions(group.id);
    render();
    applySearch();
    notify('Selectors Reset', group.name + ' selectors and actions restored to defaults.', 'Warning');
  }

  function resetAll() {
    localStorage.removeItem(STORAGE_KEY);
    localStorage.removeItem(ACTION_KEY);
    render();
    applySearch();
    notify('Selectors Reset', 'Selector values and actions restored to defaults (custom portals/functions kept).', 'Warning');
  }

  function buildExportPayload() {
    var state = loadStored();
    var payload = { exportedAt: new Date().toISOString(), portals: {} };
    getEffectiveGroups().forEach(function (group) {
      var groupPayload = {
        name: group.name, url: group.url, host: group.host,
        accent: group.accent, custom: !!group.custom, fields: {}
      };
      group.fields.forEach(function (field) {
        groupPayload.fields[field.key] = {
          label: field.label,
          source: field.source || null,
          type: field.type,
          custom: !!field.custom,
          action: effectiveAction(group, field),
          actionValue: effectiveActionValue(group, field),
          value: effectiveValue(state, group, field)
        };
      });
      payload.portals[group.id] = groupPayload;
    });
    return payload;
  }

  function exportJson() {
    var payload = buildExportPayload();
    var blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json;charset=utf-8;' });
    var url = URL.createObjectURL(blob);
    var stamp = new Date().toISOString().slice(0, 10);
    var link = document.createElement('a');
    link.href = url;
    link.download = 'selector_config_' + stamp + '.json';
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    notify('Selectors Exported', 'Configuration downloaded as JSON.', 'Success');
  }

  function importJson(file) {
    var reader = new FileReader();
    reader.onload = function () {
      try {
        var parsed = JSON.parse(String(reader.result || ''));
        var portals = parsed && parsed.portals ? parsed.portals : parsed;
        if (!portals || typeof portals !== 'object') throw new Error('No portal data found.');

        var custom = loadCustom();
        var state = loadStored();
        var actionsState = loadActions();
        var builtInIds = SELECTOR_GROUPS.map(function (g) { return g.id; });
        var importedGroups = 0;

        Object.keys(portals).forEach(function (portalId) {
          var incoming = portals[portalId];
          if (!incoming || typeof incoming !== 'object') return;
          var incomingFields = incoming.fields ? incoming.fields : incoming;
          if (!incomingFields || typeof incomingFields !== 'object') return;

          // Recreate unknown (custom) portals from the file.
          if (builtInIds.indexOf(portalId) === -1 && !custom.portals.some(function (p) { return p.id === portalId; })) {
            var purl = withScheme(incoming.url || '');
            custom.portals.push({
              id: portalId,
              name: incoming.name || portalId,
              url: purl,
              host: incoming.host || deriveHost(purl),
              initials: initialsFor(incoming.name || portalId),
              accent: incoming.accent || '#64748b',
              description: incoming.description || ''
            });
          }

          var groupValues = state[portalId] && typeof state[portalId] === 'object' ? state[portalId] : {};
          Object.keys(incomingFields).forEach(function (fieldKey) {
            var raw = incomingFields[fieldKey];
            var isObj = raw && typeof raw === 'object' && !Array.isArray(raw);
            var val = isObj && 'value' in raw ? raw.value : raw;
            var type = isObj && raw.type ? raw.type : (Array.isArray(val) ? 'list' : 'text');

            // Register unknown fields as custom so they render.
            var known = (SELECTOR_GROUPS.filter(function (g) { return g.id === portalId; })[0] || { fields: [] })
              .fields.some(function (f) { return f.key === fieldKey; });
            var alreadyCustom = (custom.fields[portalId] || []).some(function (f) { return f.key === fieldKey; });
            if (!known && !alreadyCustom) {
              if (!Array.isArray(custom.fields[portalId])) custom.fields[portalId] = [];
              custom.fields[portalId].push({
                key: fieldKey,
                label: (isObj && raw.label) ? raw.label : fieldKey,
                type: type === 'text' ? 'text' : 'list',
                help: (isObj && raw.help) ? raw.help : '',
                source: (isObj && raw.source) ? raw.source : 'custom',
                default: type === 'text' ? '' : [],
                action: (isObj && raw.action) ? raw.action : 'none',
                actionValue: (isObj && raw.actionValue) ? raw.actionValue : ''
              });
            }

            // Action override (applies to built-in and custom fields alike).
            if (isObj && (raw.action || raw.actionValue !== undefined)) {
              if (!actionsState[portalId]) actionsState[portalId] = {};
              actionsState[portalId][fieldKey] = {
                action: raw.action || 'none',
                value: raw.actionValue || ''
              };
            }

            groupValues[fieldKey] = (type === 'list')
              ? (Array.isArray(val) ? val : parseListValue(val))
              : String(val == null ? '' : val).trim();
          });

          if (Object.keys(groupValues).length) { state[portalId] = groupValues; importedGroups += 1; }
        });

        if (!importedGroups) throw new Error('No usable portals in file.');
        persistCustom(custom);
        persist(state);
        persistActions(actionsState);
        render();
        applySearch();
        notify('Selectors Imported', 'Imported configuration for ' + importedGroups + ' portal(s).', 'Success');
      } catch (err) {
        notify('Import Failed', 'Could not import selectors: ' + err.message, 'Error');
      }
    };
    reader.onerror = function () {
      notify('Import Failed', 'Could not read the selected file.', 'Error');
    };
    reader.readAsText(file);
  }

  // ---- search/filter -------------------------------------------------------

  function applySearch() {
    var input = document.getElementById('selector-config-search');
    var term = input ? input.value.trim().toLowerCase() : '';
    if (!container) return;

    getEffectiveGroups().forEach(function (group) {
      var cardEl = container.querySelector('.sc-portal[data-group="' + group.id + '"]');
      if (!cardEl) return;
      var portalMatches = !term ||
        group.name.toLowerCase().indexOf(term) !== -1 ||
        group.url.toLowerCase().indexOf(term) !== -1 ||
        group.host.toLowerCase().indexOf(term) !== -1;
      var fields = cardEl.querySelectorAll('.sc-field');
      var visibleCount = 0;
      fields.forEach(function (field) {
        var haystack = field.getAttribute('data-search') || '';
        var match = !term || haystack.indexOf(term) !== -1 || portalMatches;
        field.style.display = match ? '' : 'none';
        if (match) visibleCount += 1;
      });
      cardEl.style.display = (visibleCount > 0 || portalMatches) ? '' : 'none';
      if (term && (visibleCount > 0 || portalMatches)) {
        cardEl.classList.remove('is-collapsed');
        var h = cardEl.querySelector('.sc-portal-head');
        if (h) h.setAttribute('aria-expanded', 'true');
      }
      var chip = document.querySelector('.sc-nav-chip[data-group="' + group.id + '"]');
      if (chip) chip.classList.toggle('is-dimmed', term && cardEl.style.display === 'none');
    });
  }

  // ---- add-portal form -----------------------------------------------------

  var addPortalForm = null;

  function buildAddPortalForm() {
    var panel = el('div', 'sc-addform sc-addform-portal');
    panel.id = 'selector-config-addportal-form';
    panel.hidden = true;
    panel.appendChild(el('div', 'sc-addform-title', 'Add portal (link)'));

    var grid = el('div', 'sc-addform-grid');

    var nameField = el('label', 'sc-addform-field');
    nameField.appendChild(el('span', 'sc-addform-label', 'Portal name'));
    var nameInput = el('input', 'sc-input');
    nameInput.type = 'text';
    nameInput.placeholder = 'e.g. Azure Portal';
    nameField.appendChild(nameInput);
    grid.appendChild(nameField);

    var urlField = el('label', 'sc-addform-field');
    urlField.appendChild(el('span', 'sc-addform-label', 'URL'));
    var urlInput = el('input', 'sc-input');
    urlInput.type = 'text';
    urlInput.placeholder = 'https://portal.azure.com';
    urlField.appendChild(urlInput);
    grid.appendChild(urlField);

    var descField = el('label', 'sc-addform-field sc-addform-field-wide');
    descField.appendChild(el('span', 'sc-addform-label', 'Description (optional)'));
    var descInput = el('input', 'sc-input');
    descInput.type = 'text';
    descInput.placeholder = 'What this portal is used for…';
    descField.appendChild(descInput);
    grid.appendChild(descField);

    var accentField = el('div', 'sc-addform-field');
    accentField.appendChild(el('span', 'sc-addform-label', 'Accent color'));
    var swatches = el('div', 'sc-accent-swatches');
    var selectedAccent = { value: '#64748b' };
    ACCENT_PRESETS.forEach(function (color, i) {
      var sw = el('button', 'sc-accent-swatch');
      sw.type = 'button';
      sw.style.background = color;
      sw.setAttribute('data-color', color);
      sw.setAttribute('aria-label', 'Accent ' + color);
      if (color === '#64748b') sw.classList.add('is-selected');
      if (i === 8) { selectedAccent.value = '#64748b'; }
      sw.addEventListener('click', function () {
        selectedAccent.value = color;
        swatches.querySelectorAll('.sc-accent-swatch').forEach(function (s) { s.classList.remove('is-selected'); });
        sw.classList.add('is-selected');
      });
      swatches.appendChild(sw);
    });
    accentField.appendChild(swatches);
    grid.appendChild(accentField);

    panel.appendChild(grid);

    var actions = el('div', 'sc-addform-actions');
    var createBtn = el('button', 'sc-btn sc-btn-primary sc-btn-sm', 'Create portal');
    createBtn.type = 'button';
    createBtn.addEventListener('click', function () {
      var name = nameInput.value.trim();
      var url = urlInput.value.trim();
      if (!name) { notify('Name Required', 'Enter a portal name.', 'Warning'); nameInput.focus(); return; }
      if (!url) { notify('URL Required', 'Enter a portal URL.', 'Warning'); urlInput.focus(); return; }
      addCustomPortal(name, url, selectedAccent.value, descInput.value.trim());
      nameInput.value = ''; urlInput.value = ''; descInput.value = '';
      panel.hidden = true;
    });
    var cancelBtn = el('button', 'sc-btn sc-btn-ghost sc-btn-sm', 'Cancel');
    cancelBtn.type = 'button';
    cancelBtn.addEventListener('click', function () { panel.hidden = true; });
    actions.appendChild(createBtn);
    actions.appendChild(cancelBtn);
    panel.appendChild(actions);

    return panel;
  }

  // ---- wiring --------------------------------------------------------------

  function wireToolbar() {
    var saveAllBtn = document.getElementById('selector-config-save-all');
    var resetAllBtn = document.getElementById('selector-config-reset-all');
    var exportBtn = document.getElementById('selector-config-export');
    var importBtn = document.getElementById('selector-config-import-btn');
    var importFile = document.getElementById('selector-config-import-file');
    var search = document.getElementById('selector-config-search');
    var expandToggle = document.getElementById('selector-config-expand-toggle');
    var addPortalBtn = document.getElementById('selector-config-add-portal');

    if (saveAllBtn) saveAllBtn.addEventListener('click', saveAll);
    if (resetAllBtn) resetAllBtn.addEventListener('click', resetAll);
    if (exportBtn) exportBtn.addEventListener('click', exportJson);
    if (expandToggle) expandToggle.addEventListener('click', function () { setAllCollapsed(!allCollapsed()); });
    if (addPortalBtn) {
      addPortalBtn.addEventListener('click', function () {
        if (!addPortalForm) return;
        addPortalForm.hidden = !addPortalForm.hidden;
        if (!addPortalForm.hidden) {
          addPortalForm.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
          var firstInput = addPortalForm.querySelector('input');
          if (firstInput) firstInput.focus();
        }
      });
    }
    if (importBtn && importFile) {
      importBtn.addEventListener('click', function () { importFile.click(); });
      importFile.addEventListener('change', function () {
        if (importFile.files && importFile.files[0]) {
          importJson(importFile.files[0]);
          importFile.value = '';
        }
      });
    }
    if (search) search.addEventListener('input', applySearch);
  }

  var initialized = false;
  function init() {
    if (initialized) return;
    container = document.getElementById('selector-config-groups');
    if (!container) return;
    initialized = true;
    wireToolbar();
    addPortalForm = buildAddPortalForm();
    container.parentNode.insertBefore(addPortalForm, container);
    render();
    applySearch();
  }

  // Read API for the backend bridge / other scripts.
  window.RedflaggedSelectorConfig = {
    init: init,
    getConfig: buildExportPayload,
    getGroups: getEffectiveGroups,
    robustify: robustifySelector,
    hasDynamicNumbers: hasDynamicNumbers,
    addPortal: addCustomPortal,
    addFunction: addCustomField
  };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
