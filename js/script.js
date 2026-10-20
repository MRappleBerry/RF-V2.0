const log = document.getElementById('log');
const cmd = document.getElementById('cmd');
const artifactsList = document.getElementById('artifacts-list');
const artifactsPagination = document.getElementById('artifacts-pagination');
const ARTIFACTS_PAGE_SIZE = 3;
let artifactsPage = 1;
const MODAL_ARTIFACTS_PAGE_SIZE = 3;

const tabIncidents = document.getElementById('tab-incidents');
const tabExecutive = document.getElementById('tab-executive');
const tabArtifacts = document.getElementById('tab-artifacts');
const tabMonitoring = document.getElementById('tab-monitoring');
const tabSelectors = document.getElementById('tab-selectors');
const tabSettings = document.getElementById('tab-settings');
const incidentsTabContent = document.getElementById('incidents-tab-content');
const executiveTabContent = document.getElementById('executive-tab-content');
const artifactsTabContent = document.getElementById('artifacts-tab-content');
const monitoringTabContent = document.getElementById('monitoring-tab-content');
const selectorsTabContent = document.getElementById('selectors-tab-content');
const settingsTabContent = document.getElementById('settings-tab-content');
const mainWrap = document.querySelector('main.wrap');
const rightConsolePanel = document.querySelector('.panel.console');
const mainPanelTitle = document.getElementById('main-panel-title');
const mainPanelSubtitle = document.getElementById('main-panel-subtitle');
const mainPanelChips = document.getElementById('main-panel-chips');
const metricTotalIncidents = document.getElementById('metric-total-incidents');
const metricTruePositive = document.getElementById('metric-true-positive');
const metricFalsePositive = document.getElementById('metric-false-positive');
const metricBenign = document.getElementById('metric-benign');
const classChartTruePositive = document.getElementById('class-chart-true-positive');
const classChartFalsePositive = document.getElementById('class-chart-false-positive');
const classChartBenign = document.getElementById('class-chart-benign');
const classChartTruePositiveCount = document.getElementById('class-chart-true-positive-count');
const classChartFalsePositiveCount = document.getElementById('class-chart-false-positive-count');
const classChartBenignCount = document.getElementById('class-chart-benign-count');
const classificationHistoryBody = document.getElementById('classification-history-body');
const classificationHistoryPagination = document.getElementById('classification-history-pagination');
const executiveTopActorsBody = document.getElementById('executive-top-actors-body');
const executiveTopDepartmentsBody = document.getElementById('executive-top-departments-body');
const executiveTopActorsPagination = document.getElementById('executive-top-actors-pagination');
const executiveTopDepartmentsPagination = document.getElementById('executive-top-departments-pagination');
const executiveYearFilter = document.getElementById('executive-year-filter');
const executiveMonthFilter = document.getElementById('executive-month-filter');
const executiveClassificationFilter = document.getElementById('executive-classification-filter');
const executiveHistorySearch = document.getElementById('executive-history-search');
const classificationHistoryExport = document.getElementById('classification-history-export');
const incidentsTable = document.getElementById('incidents-table');
const incidentsTableColgroup = document.getElementById('incidents-table-colgroup');
const incidentsTableHead = document.getElementById('incidents-table-head');
const incidentsTableBody = document.getElementById('incidents-table-body');
const incidentsPagination = document.getElementById('incidents-pagination');
const DEFAULT_INCIDENTS_TABLE_COLGROUP_HTML = incidentsTableColgroup ? incidentsTableColgroup.innerHTML : '';
const DEFAULT_INCIDENTS_TABLE_HEAD_HTML = incidentsTableHead ? incidentsTableHead.innerHTML : '';
const artifactsIncidentFilter = document.getElementById('artifacts-incident-filter');
const avpSearchField = document.getElementById('avp-search-field');
const avpArtifactsTable = document.getElementById('avp-artifacts-table');
const avpAlertNameCol = document.getElementById('avp-alert-name-col');
const avpAlertColumnToggle = document.getElementById('avp-alert-column-toggle');
const avpAlertColumnToggleHint = document.getElementById('avp-alert-column-toggle-hint');
const artifactsByIncidentBody = document.getElementById('artifacts-by-incident-body');
const settingsThemeSelect = document.getElementById('settings-theme-select');
const settingsTimezoneSelect = document.getElementById('settings-timezone-select');
const runAutomationBtn = document.getElementById('run-automation-btn');
const terminateAutomationBtn = document.getElementById('terminate-automation-btn');
const saveAutomationScheduleBtn = document.getElementById('save-automation-schedule-btn');
const autoRunEnabled = document.getElementById('auto-run-enabled');
const autoRunFrequency = document.getElementById('auto-run-frequency');
const autoRunDay = document.getElementById('auto-run-day');
const autoRunTime = document.getElementById('auto-run-time');
const autoStopEnabled = document.getElementById('auto-stop-enabled');
const autoStopFrequency = document.getElementById('auto-stop-frequency');
const autoStopDay = document.getElementById('auto-stop-day');
const autoStopTime = document.getElementById('auto-stop-time');
const systemConfigStatusBadge = document.getElementById('system-config-status-badge');
const systemConfigStatusText = document.getElementById('system-config-status-text');
const systemConfigSystemState = document.getElementById('system-config-system-state');
const systemConfigNextRun = document.getElementById('system-config-next-run');
const systemConfigNextStop = document.getElementById('system-config-next-stop');
const systemConfigFeedback = document.getElementById('system-config-feedback');
const monitoringProcessBody = document.getElementById('monitoring-process-body');
const monitoringLastUpdated = document.getElementById('monitoring-last-updated');
const INCIDENTS_PAGE_SIZE = 5;
const CLASSIFICATION_HISTORY_PAGE_SIZE = 5;
const EXECUTIVE_DETAIL_PAGE_SIZE = 5;
let monitoringRealtimeHandle = null;
let incidentsPage = 1;
let classificationHistoryPage = 1;
let executiveTopActorsPage = 1;
let executiveTopDepartmentsPage = 1;
let incidentRows = [];
let dbInfoIncidentRows = [];
let dbInfoColumnHeaders = [];
let artifactRows = [];
let artifactInventoryFiles = [];
let executiveHistoryRows = [];
let selectedIncidentForOdin = null;
let artifactsDirectoryHandle = null;
let lastArtifactFilterSnapshot = '';
let isArtifactAlertColumnExpanded = false;
let incidentExpandedIds = new Set();
let incidentRowRegistry = {};
let lastRenderedRows = [];
let idfActiveFilters = [];
let idfFilterPreset = 'all';
const THEME_PREF_KEY = 'rf_theme_preference';
const THEME_SYSTEM = 'system';
const supportedThemeModes = new Set(['light', 'dark', 'classic', 'high-contrast']);
const systemThemeMedia = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;
const DEFAULT_ASSIGNEE_NAMES = ['John Doe', 'Jane Smith', 'Michael Lee', 'Sophia Brown', 'David Wilson'];
const DEFAULT_INCIDENT_ASSIGNEE = 'Security Managed Services';
const RESOLVE_CLASSIFICATION_SELECT_VALUES = new Set([
  'Unclassified',
  'True Positive: Multi staged attack',
  'True Positive: Malware',
  'True Positive: Malicious user activity',
  'True Positive: Unwanted software',
  'True Positive: Phishing',
  'True Positive: Compromised account',
  'True Positive: Other',
  'Informational, expected activity: Security testing',
  'Informational, expected activity: Confirmed activity',
  'Informational, expected activity: Line of business application',
  'Informational, expected activity: Other',
  'False Positive: Not malicious',
  'False Positive: Not enough data to validate',
  'False Positive: Other'
]);
const DB_INFO_PATH = 'automation/db_info.txt';
const ARTIFACTS_DATA_PATH = 'automation/artifacts_inventory.json';
const AUTOMATION_API_BASE = (() => {
  const host = window.location.hostname || '127.0.0.1';
  if(window.location.protocol === 'file:') return 'http://127.0.0.1:8765';
  return `${window.location.protocol}//${host}:8765`;
})();
const incidentModal = document.getElementById('incident-modal');
const incidentModalClose = document.getElementById('incident-modal-close');
const modalArtifactsPagination = document.getElementById('modal-artifacts-pagination');
const modalCommentBox = document.getElementById('modal-comment-box');
const modalAgentSupportToggle = document.getElementById('modal-agent-support-toggle');
const modalAgentSupportPanel = document.getElementById('modal-agent-support-panel');
const openModalFromAgent = document.getElementById('open-modal-from-agent');
const modalAssignedAgentName = document.getElementById('modal-assigned-agent-name');
const modalAssignedAgentQueue = document.getElementById('modal-assigned-agent-queue');
const modalAssignedAgentStatus = document.getElementById('modal-assigned-agent-status');
const modalAssignedAgentFocus = document.getElementById('modal-assigned-agent-focus');
const consultOdinFromSummary = document.getElementById('consult-odin-from-summary');
const viewIncidentFromOdin = document.getElementById('view-incident-from-odin');
const resolveIncidentFromOdin = document.getElementById('resolve-incident-from-odin');
const agentSupportModal = document.getElementById('agent-support-modal');
const agentSupportModalClose = document.getElementById('agent-support-modal-close');
const odinChatThread = document.getElementById('odin-chat-thread');
const odinChatInput = document.getElementById('odin-chat-input');
const odinChatSend = document.getElementById('odin-chat-send');
const odinChatQuickButtons = document.querySelectorAll('.odin-chat-quick');
const resolveModal = document.getElementById('resolve-modal');
const resolveModalClose = document.getElementById('resolve-modal-close');
const resolveCancel = document.getElementById('resolve-cancel');
const resolveSave = document.getElementById('resolve-save');
const resolveIncidentId = document.getElementById('resolve-incident-id');
const resolveIncidentName = document.getElementById('resolve-incident-name');
const resolveTicketId = document.getElementById('resolve-ticket-id');
const resolveClassification = document.getElementById('resolve-classification');
const resolveNote = document.getElementById('resolve-note');
const resolveExplanation = document.getElementById('resolve-explanation');
let activeModalRow = null;
let activeResolveRow = null;
let automationStatusPollHandle = null;
let automationBackendAnnounced = false;
let systemConfigDirty = false;

function automationApiUrl(path){
  return `${AUTOMATION_API_BASE}${path}`;
}
let activeModalArtifacts = [];
let modalArtifactsPage = 1;

function appendOdinChatMessage(role, text){
  if(!odinChatThread || !text) return;
  const row = document.createElement('div');
  row.className = `odin-chat-message ${role === 'user' ? 'user' : 'bot'}`;

  const bubble = document.createElement('div');
  bubble.className = 'odin-chat-bubble';
  bubble.textContent = text;

  row.appendChild(bubble);
  odinChatThread.appendChild(row);
  odinChatThread.scrollTop = odinChatThread.scrollHeight;
}

function getMockOdinReply(message){
  const lower = String(message || '').toLowerCase();
  const selected = selectedIncidentForOdin;
  const incidentLabel = selected
    ? `${selected.incidentId || 'N/A'} (${selected.detection || 'no detection'})`
    : 'the current incident';

  if(lower.includes('summar') || lower.includes('risk')){
    return `Risk summary for ${incidentLabel}: elevated confidence of policy-impacting behavior. Validate actor intent, review artifact timeline, and confirm containment threshold before closure.`;
  }
  if(lower.includes('evidence') || lower.includes('artifact') || lower.includes('checklist')){
    return `Suggested evidence checklist for ${incidentLabel}: 1) source alert snapshot, 2) actor activity timeline, 3) access/share audit records, 4) manager/user attestation, 5) ticket update with validation outcome.`;
  }
  if(lower.includes('contain') || lower.includes('next step') || lower.includes('escalat')){
    return `Recommended next steps: start with scoped containment, preserve audit logs, confirm business impact with owner, then escalate only if exfiltration indicators remain after validation.`;
  }

  return `I am currently running in mock mode (no API yet), but I can still guide triage flow. Ask me for risk summary, evidence checklist, or containment steps for ${incidentLabel}.`;
}

function sendOdinChatMessage(prefilledText){
  const text = typeof prefilledText === 'string'
    ? prefilledText.trim()
    : (odinChatInput ? odinChatInput.value.trim() : '');

  if(!text) return;

  appendOdinChatMessage('user', text);
  if(odinChatInput) odinChatInput.value = '';
  if(odinChatSend) odinChatSend.disabled = true;

  const reply = getMockOdinReply(text);
  window.setTimeout(() => {
    appendOdinChatMessage('bot', reply);
    if(odinChatSend) odinChatSend.disabled = false;
    if(odinChatInput) odinChatInput.focus();
  }, 220);
}

function ensureOdinChatBootstrap(){
  if(!odinChatThread || odinChatThread.childElementCount > 0) return;
  appendOdinChatMessage('bot', 'Hello, I am Odin. This is a mock chatbot interface while API integration is pending.');
  appendOdinChatMessage('bot', 'I can still help with triage guidance. Try: Summarize Risk, Evidence Checklist, or Containment Steps.');
}

function openAgentSupportModal(){
  if(!agentSupportModal) return;
  closeIncidentModal();
  ensureOdinChatBootstrap();
  agentSupportModal.classList.add('active');
  agentSupportModal.setAttribute('aria-hidden', 'false');
}

function closeAgentSupportModal(){
  if(!agentSupportModal) return;
  agentSupportModal.classList.remove('active');
  agentSupportModal.setAttribute('aria-hidden', 'true');
}

function normalizeIncidentClassificationToken(value){
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[_-]+/g, ' ')
    .replace(/\s+/g, ' ');
}

function getIncidentClassificationBucket(value){
  const normalized = normalizeIncidentClassificationToken(value);
  if(!normalized || normalized === 'unclassified') return '';
  if(normalized === 'true positive' || normalized.startsWith('true positive:')) return 'True Positive';
  if(normalized === 'false positive' || normalized.startsWith('false positive:')) return 'False Positive';
  if(normalized === 'benign' || normalized === 'informational, expected activity' || normalized.startsWith('informational, expected activity:')) return 'Benign';
  if(['multi staged attack', 'malware', 'malicious user activity', 'unwanted software', 'phishing', 'compromised account'].includes(normalized)) return 'True Positive';
  if(['security testing', 'confirmed activity', 'line of business application'].includes(normalized)) return 'Benign';
  if(['not malicious', 'not enough data to validate'].includes(normalized)) return 'False Positive';
  return '';
}

function getResolveClassificationValue(value){
  const rawValue = String(value || '').trim();
  if(RESOLVE_CLASSIFICATION_SELECT_VALUES.has(rawValue)) return rawValue;

  const normalized = normalizeIncidentClassificationToken(rawValue);
  const mappedValues = {
    'unclassified': 'Unclassified',
    'true positive': 'True Positive: Other',
    'multi staged attack': 'True Positive: Multi staged attack',
    'malware': 'True Positive: Malware',
    'malicious user activity': 'True Positive: Malicious user activity',
    'unwanted software': 'True Positive: Unwanted software',
    'phishing': 'True Positive: Phishing',
    'compromised account': 'True Positive: Compromised account',
    'benign': 'Informational, expected activity: Other',
    'informational, expected activity': 'Informational, expected activity: Other',
    'security testing': 'Informational, expected activity: Security testing',
    'confirmed activity': 'Informational, expected activity: Confirmed activity',
    'line of business application': 'Informational, expected activity: Line of business application',
    'false positive': 'False Positive: Other',
    'not malicious': 'False Positive: Not malicious',
    'not enough data to validate': 'False Positive: Not enough data to validate'
  };

  return mappedValues[normalized] || 'Unclassified';
}

function openResolveModal(row){
  if(!resolveModal || !row) return;
  closeIncidentModal();
  closeAgentSupportModal();
  activeResolveRow = row;
  if(resolveIncidentName) resolveIncidentName.textContent = row.detection || 'N/A';
  if(resolveIncidentId) resolveIncidentId.textContent = row.incidentId || 'N/A';
  if(resolveTicketId) resolveTicketId.textContent = row.ticket || 'N/A';
  if(resolveClassification) resolveClassification.value = getResolveClassificationValue(row.classification);
  if(resolveNote) resolveNote.value = row.resolutionNote || '';
  if(resolveExplanation) resolveExplanation.value = row.resolveExplanation || '';
  resolveModal.classList.add('active');
  resolveModal.setAttribute('aria-hidden', 'false');
}

function closeResolveModal(){
  if(!resolveModal) return;
  resolveModal.classList.remove('active');
  resolveModal.setAttribute('aria-hidden', 'true');
  activeResolveRow = null;
}

function toggleModalAgentSupport(){
  if(!modalAgentSupportToggle || !modalAgentSupportPanel) return;
  const isOpen = modalAgentSupportPanel.classList.toggle('active');
  modalAgentSupportToggle.setAttribute('aria-expanded', String(isOpen));
  modalAgentSupportPanel.setAttribute('aria-hidden', String(!isOpen));

  showNotification({
    title: 'Assigned Agent',
    text: isOpen ? 'Assigned agent details are now visible in the modal.' : 'Assigned agent panel hidden.',
    status: isOpen ? 'Success' : 'Info',
    source: 'UI'
  });
}

function updateAssignedAgentUI(row){
  const assignedAgent = row && row.assignedTo ? row.assignedTo : 'Unassigned';
  const agentQueue = row && row.source ? row.source : 'Security Managed Services';
  const agentFocus = row && row.ticket
    ? `Owner for ${row.ticket}. Review evidence, update the case, and coordinate approvals when containment is needed.`
    : 'Review case ownership, validate evidence, and proceed with containment approvals if needed.';

  if(modalAgentSupportToggle) modalAgentSupportToggle.textContent = `Agent: ${assignedAgent}`;
  if(openModalFromAgent) openModalFromAgent.textContent = `Agent: ${assignedAgent}`;
  if(modalAssignedAgentName) modalAssignedAgentName.textContent = assignedAgent;
  if(modalAssignedAgentQueue) modalAssignedAgentQueue.textContent = agentQueue;
  if(modalAssignedAgentFocus) modalAssignedAgentFocus.textContent = agentFocus;
  if(modalAssignedAgentStatus) modalAssignedAgentStatus.textContent = assignedAgent === 'Unassigned' ? 'Pending' : 'Assigned';
}

function getActionDrivenOdin(row, actionKey){
  const base = {
    validate: {
      summary: `Validation focus for ${row.incidentId}: correlate ${row.policy || 'policy'} signals with timeline and actor context before escalation.`,
      what: [
        'Cross-check Purview and Sentinel event timestamps for source consistency.',
        `Confirm actor context (${row.actor || 'unknown actor'}) and activity path.`,
        'Verify rule hit quality and remove false-positive indicators.'
      ],
      why: [
        'Prevents unnecessary containment for non-malicious events.',
        'Improves confidence and audit quality of the incident narrative.',
        'Reduces rework during approval and closure.'
      ],
      next: [
        `Validate ownership and access scope for ${row.ticket}.`,
        'Attach correlation evidence and analyst rationale to the case.',
        'Proceed to collection stage if confidence remains medium/high.'
      ]
    },
    collect: {
      summary: `Collection focus for ${row.incidentId}: gather missing artifacts to confirm exposure path and decision readiness.`,
      what: [
        `Primary artifact in scope: ${row.artifact || 'artifact file'}.`,
        'Collect access logs, sharing links, and download traces.',
        'Capture immutable evidence package for reviewer handoff.'
      ],
      why: [
        'Evidence completeness is required for defensible containment actions.',
        'Missing artifacts increase triage ambiguity and SLA risk.',
        'Improves confidence for escalation and post-incident reporting.'
      ],
      next: [
        'Gather residual logs and metadata from source systems.',
        'Tag artifact chain-of-custody in the incident notes.',
        'Re-run Odin summary after evidence set is complete.'
      ]
    },
    contain: {
      summary: `Containment focus for ${row.incidentId}: apply approved controls to limit potential impact and prevent further spread.`,
      what: [
        'Potential exposure risk remains active based on current findings.',
        'Containment paths include revoke access, quarantine file, or block sharing.',
        `Assigned owner for action tracking: ${row.assignedTo || 'Security Managed Services'}.`
      ],
      why: [
        'Time-to-containment directly reduces blast radius.',
        'Delays can increase compliance and regulatory exposure.',
        'Immediate control actions support SLA and business risk objectives.'
      ],
      next: [
        'Secure approval and execute containment playbook steps.',
        'Document every action with timestamp and actor.',
        'Validate closure conditions and communicate outcome.'
      ]
    }
  };

  return base[actionKey] || base.validate;
}

function renderOdinFromAction(row, actionKey){
  const modalOdinSummary = document.getElementById('modal-odin-summary');
  const modalWhat = document.getElementById('modal-odin-what');
  const modalWhy = document.getElementById('modal-odin-why');
  const modalActions = document.getElementById('modal-odin-actions');
  const rightPane = document.querySelector('.incident-pane-right');

  const content = getActionDrivenOdin(row, actionKey);

  if(modalOdinSummary) modalOdinSummary.textContent = content.summary;
  if(modalWhat) modalWhat.innerHTML = content.what.map((x) => `<li>${escapeHtml(x)}</li>`).join('');
  if(modalWhy) modalWhy.innerHTML = content.why.map((x) => `<li>${escapeHtml(x)}</li>`).join('');
  if(modalActions) modalActions.innerHTML = content.next.map((x) => `<li>${escapeHtml(x)}</li>`).join('');

  if(rightPane){
    rightPane.classList.add('flash-focus');
    rightPane.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    setTimeout(() => rightPane.classList.remove('flash-focus'), 380);
  }
}

function bindModalActionItems(row){
  const list = document.getElementById('modal-actions-list');
  if(!list) return;
  const items = list.querySelectorAll('.modal-action-item');
  items.forEach((item) => {
    const activate = () => {
      items.forEach((i) => i.classList.remove('active'));
      item.classList.add('active');
      const key = item.getAttribute('data-action-key') || 'validate';
      renderOdinFromAction(row, key);
    };
    item.addEventListener('click', activate);
    item.addEventListener('keydown', (e) => {
      if(e.key === 'Enter' || e.key === ' '){
        e.preventDefault();
        activate();
      }
    });
  });
}

const DUMMY_INCIDENT_ROWS = [
  {
    incidentId: 'INC-2026-0401-001', ticket: 'SNOW-REQ-238701', date: '2026-04-01',
    name: 'Sensitive Data Exfiltration via SharePoint', status: 'In Progress', severity: 'High',
    assignedTo: 'John Doe', actionUrl: '#', source: 'Microsoft Purview + Sentinel',
    detection: 'Potential Sensitive Data Exfiltration', actor: 'user@company.com',
    location: 'SharePoint Online', policy: 'DLP – Confidential Data',
    recommendation: 'Contain & Validate', artifact: 'purview_alert_snapshot_0401.json',
    classification: 'True Positive', impactedAssets: ['user@company.com', 'SharePoint-ProjectX', 'Desktop-JD01'],
    firstActivity: '2026-04-01 08:12', lastActivity: '2026-04-01 14:47',
    odinSummary: 'High confidence signal from Purview and Sentinel correlation. Recommend evidence validation and short-term containment pending business-owner confirmation.',
    relatedIncidents: [
      {
        incidentId: 'INC-2026-0331-001A', ticket: 'SNOW-REQ-238691', date: '2026-03-31',
        name: 'Precursor: Bulk File Download Before Exfiltration', status: 'Resolved', severity: 'Medium',
        assignedTo: 'John Doe', actionUrl: '#', source: 'Microsoft Purview',
        detection: 'Bulk File Download Activity', actor: 'user@company.com',
        location: 'SharePoint Online', policy: 'DLP – Confidential Data',
        recommendation: 'Closed After Review', artifact: 'bulk_download_log_0331.json',
        classification: 'True Positive', impactedAssets: ['user@company.com', 'SharePoint-ProjectX'],
        firstActivity: '2026-03-31 17:30', lastActivity: '2026-03-31 17:55',
        odinSummary: 'Large volume download from restricted library. Confirmed same actor as parent incident. Closed after initial scope review.'
      },
      {
        incidentId: 'INC-2026-0329-001B', ticket: 'SNOW-REQ-238682', date: '2026-03-29',
        name: 'Unauthorized Access to Restricted Library', status: 'Resolved', severity: 'Medium',
        assignedTo: 'Jane Smith', actionUrl: '#', source: 'Microsoft Purview',
        detection: 'Policy Override on Confidential Library', actor: 'user@company.com',
        location: 'SharePoint Online', policy: 'Access Governance', recommendation: 'Closed After Validation',
        artifact: 'access_override_log_0329.csv', classification: 'True Positive',
        impactedAssets: ['user@company.com'], firstActivity: '2026-03-29 10:05', lastActivity: '2026-03-29 10:20',
        odinSummary: 'Single override event on confidential document library. Linked to subsequent bulk download.'
      }
    ]
  },
  {
    incidentId: 'INC-2026-0302-002', ticket: 'SNOW-REQ-238702', date: '2026-03-02',
    name: 'Anomalous Privileged Access – Entra ID', status: 'Pending', severity: 'High',
    assignedTo: 'Jane Smith', actionUrl: '#', source: 'Microsoft Purview + Sentinel',
    detection: 'Anomalous Privileged Access', actor: 'analyst2@company.com',
    location: 'Entra ID', policy: 'Privileged Access Review', recommendation: 'Validate Account Activity',
    artifact: 'privileged_access_audit_0302.csv', classification: 'False Positive',
    impactedAssets: ['analyst2@company.com', 'Entra-Admin-Role'],
    firstActivity: '2026-03-02 09:00', lastActivity: '2026-03-02 09:44',
    odinSummary: 'Privilege activity deviates from baseline. Session and role-elevation timeline collected; waiting for approver verification.',
    relatedIncidents: [
      {
        incidentId: 'INC-2026-0228-002A', ticket: 'SNOW-REQ-238675', date: '2026-02-28',
        name: 'Role Escalation Request – Outside Business Hours', status: 'Resolved', severity: 'Medium',
        assignedTo: 'Jane Smith', actionUrl: '#', source: 'Sentinel Analytics',
        detection: 'Privileged Role Assigned After Hours', actor: 'analyst2@company.com',
        location: 'Entra ID', policy: 'Privileged Access Review', recommendation: 'Closed – Approved Exception',
        artifact: 'role_assignment_log_0228.json', classification: 'Benign',
        impactedAssets: ['analyst2@company.com'],
        firstActivity: '2026-02-28 22:14', lastActivity: '2026-02-28 22:18',
        odinSummary: 'After-hours elevation event linked to approved exception. Prior incident captured for context.'
      }
    ]
  },
  {
    incidentId: 'INC-2026-0203-003', ticket: 'SNOW-REQ-238703', date: '2026-02-03',
    name: 'Malware Signature Match on Managed Endpoint', status: 'Resolved', severity: 'Medium',
    assignedTo: 'Michael Lee', actionUrl: '#', source: 'Microsoft Defender + Sentinel',
    detection: 'Malware Signature Match', actor: 'device-user@company.com',
    location: 'Endpoint', policy: 'Endpoint Protection', recommendation: 'Closed After Remediation',
    artifact: 'endpoint_ioc_bundle_0203.zip', classification: 'True Positive',
    impactedAssets: ['device-user@company.com', 'DESKTOP-ML0203'],
    firstActivity: '2026-02-03 14:20', lastActivity: '2026-02-03 15:05',
    odinSummary: 'Endpoint was isolated and remediated successfully. IOC sweep across peer devices returned no additional hits.'
  },
  {
    incidentId: 'INC-2026-0104-004', ticket: 'SNOW-REQ-238704', date: '2026-01-04',
    name: 'External Sharing Policy Violation – OneDrive', status: 'Open', severity: 'High',
    assignedTo: 'Sophia Brown', actionUrl: '#', source: 'Microsoft Purview + Sentinel',
    detection: 'External Sharing Policy Violation', actor: 'owner@company.com',
    location: 'OneDrive', policy: 'External Sharing Restrictions', recommendation: 'Contain & Validate',
    artifact: 'external_sharing_events_0104.json', classification: 'True Positive',
    impactedAssets: ['owner@company.com', 'OneDrive-SharedFolder'],
    firstActivity: '2026-01-04 11:00', lastActivity: '2026-01-04 11:38',
    odinSummary: 'External link expansion detected outside policy. Access review needed before auto-revocation to prevent business interruption.'
  },
  {
    incidentId: 'INC-2025-1205-005', ticket: 'SNOW-REQ-238705', date: '2025-12-05',
    name: 'Sensitive Label Downgrade – SharePoint Library', status: 'In Progress', severity: 'Medium',
    assignedTo: 'David Wilson', actionUrl: '#', source: 'Purview Compliance',
    detection: 'Sensitive Label Downgrade', actor: 'editor@company.com',
    location: 'SharePoint Online', policy: 'Label Governance', recommendation: 'Review Label Change History',
    artifact: 'label_change_log_1205.xlsx', classification: 'Benign',
    impactedAssets: ['editor@company.com'],
    firstActivity: '2025-12-05 10:15', lastActivity: '2025-12-05 10:40',
    odinSummary: 'Multiple downgrade actions found in short window. Reviewing approval chain and exception request history.'
  },
  {
    incidentId: 'INC-2025-0906-006', ticket: 'SNOW-REQ-238706', date: '2025-09-06',
    name: 'Impossible Travel Alert – Identity Risk', status: 'Open', severity: 'Medium',
    assignedTo: 'John Doe', actionUrl: '#', source: 'Sentinel Analytics',
    detection: 'Impossible Travel Alert', actor: 'user2@company.com',
    location: 'Identity', policy: 'Conditional Access', recommendation: 'Trigger MFA Reset',
    artifact: 'identity_risk_events_0906.json', classification: 'False Positive',
    impactedAssets: ['user2@company.com'],
    firstActivity: '2025-09-06 07:33', lastActivity: '2025-09-06 07:51',
    odinSummary: 'Sign-ins from distant geos within impossible timeframe. Account risk elevated; step-up auth enforcement recommended.'
  },
  {
    incidentId: 'INC-2025-0607-007', ticket: 'SNOW-REQ-238707', date: '2025-06-07',
    name: 'Phishing Campaign Targeting Finance Team', status: 'Pending', severity: 'High',
    assignedTo: 'Jane Smith', actionUrl: '#', source: 'Defender for Office 365',
    detection: 'Potential Phishing Campaign', actor: 'mailbox-user@company.com',
    location: 'Exchange Online', policy: 'Anti-Phishing', recommendation: 'Quarantine Messages',
    artifact: 'mail_header_analysis_0607.eml', classification: 'True Positive',
    impactedAssets: ['mailbox-user@company.com', 'finance-dl@company.com', 'Exchange-EOP'],
    firstActivity: '2025-06-07 08:00', lastActivity: '2025-06-07 09:22',
    odinSummary: 'Campaign indicators overlap known sender-pattern spoofing. Mail trace and takedown evidence prepared for escalation.'
  },
  {
    incidentId: 'INC-2025-0308-008', ticket: 'SNOW-REQ-238708', date: '2025-03-08',
    name: 'Suspicious Process Chain on Endpoint', status: 'Resolved', severity: 'Medium',
    assignedTo: 'Michael Lee', actionUrl: '#', source: 'Defender for Endpoint',
    detection: 'Suspicious Process Chain', actor: 'endpoint-user@company.com',
    location: 'Endpoint', policy: 'Endpoint Behavioral Rules', recommendation: 'Closed After Validation',
    artifact: 'process_chain_tree_0308.txt', classification: 'Benign',
    impactedAssets: ['endpoint-user@company.com', 'DESKTOP-EP0308'],
    firstActivity: '2025-03-08 13:10', lastActivity: '2025-03-08 13:28',
    odinSummary: 'Behavioral anomaly triaged as non-malicious admin script. Detection rule tuned; incident closed as benign true positive.'
  },
  {
    incidentId: 'INC-2024-1119-009', ticket: 'SNOW-REQ-238709', date: '2024-11-19',
    name: 'Data Staging Detected – Contractor Account', status: 'Open', severity: 'High',
    assignedTo: 'Sophia Brown', actionUrl: '#', source: 'Sentinel + Defender XDR',
    detection: 'Data Staging Activity', actor: 'contractor@company.com',
    location: 'SharePoint Online', policy: 'Data Exfiltration Controls', recommendation: 'Contain & Validate',
    artifact: 'staging_activity_timeline_1119.csv', classification: 'True Positive',
    impactedAssets: ['contractor@company.com', 'SharePoint-Archive', 'OneDrive-Contractor'],
    firstActivity: '2024-11-19 15:00', lastActivity: '2024-11-19 16:45',
    odinSummary: 'Staging pattern resembles pre-exfiltration workflow. File movement throttled while manager attestation is pending.'
  },
  {
    incidentId: 'INC-2024-0701-010', ticket: 'SNOW-REQ-238710', date: '2024-07-01',
    name: 'PCI Credit Card Pattern Match – Exchange Online', status: 'In Progress', severity: 'Medium',
    assignedTo: 'David Wilson', actionUrl: '#', source: 'Purview DLP',
    detection: 'Credit Card Pattern Match', actor: 'finance@company.com',
    location: 'Exchange Online', policy: 'PCI-DSS DLP', recommendation: 'Review and Escalate',
    artifact: 'dlp_match_summary_0701.json', classification: 'False Positive',
    impactedAssets: ['finance@company.com', 'Exchange-Finance'],
    firstActivity: '2024-07-01 10:30', lastActivity: '2024-07-01 10:52',
    odinSummary: 'PCI pattern match confirmed in outbound draft artifacts. Compliance review in progress before final containment action.'
  }
];

function normalizeLookupKey(value){
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function getRecordField(record, aliases){
  if(!record || typeof record !== 'object') return '';

  const entries = Object.entries(record).map(([key, value]) => [normalizeLookupKey(key), value]);
  const normalizedRecord = new Map(entries);

  for(const alias of aliases){
    const value = normalizedRecord.get(normalizeLookupKey(alias));
    if(value === undefined || value === null) continue;
    const normalizedValue = String(value).trim();
    if(normalizedValue) return normalizedValue;
  }

  return '';
}

function parseStoredOdinList(value){
  return String(value || '')
    .split(/\r?\n/)
    .map((line) => line.replace(/^(?:[-*•]\s*|\d+[.)]?\s*)/, '').trim())
    .filter(Boolean);
}

function getRecordList(record, aliases){
  return parseStoredOdinList(getRecordField(record, aliases));
}

function splitIncidentAssets(...values){
  const seen = new Set();
  const assets = [];

  values.forEach((value) => {
    String(value || '')
      .split(/\r?\n|[;,|]/)
      .map(part => part.trim())
      .filter(Boolean)
      .forEach((part) => {
        const key = part.toLowerCase();
        if(seen.has(key)) return;
        seen.add(key);
        assets.push(part);
      });
  });

  return assets;
}

function formatIncidentDate(value){
  const text = String(value || '').trim();
  if(!text) return '—';

  const parsed = Date.parse(text);
  if(Number.isNaN(parsed)) return text;
  return new Date(parsed).toISOString().slice(0, 16).replace('T', ' ');
}

function toIsoDateOnly(value){
  const text = String(value || '').trim();
  if(!text) return '—';

  const parsed = Date.parse(text);
  if(Number.isNaN(parsed)) return text;
  return new Date(parsed).toISOString().slice(0, 10);
}

function extractQuotedAlertText(value){
  const match = String(value || '').match(/'([^']+)'/);
  return match ? match[1].trim() : '';
}

function buildDbInfoOdinSummary(incidentName, severity, status, actor, location){
  return [
    `${incidentName || 'Purview incident'} imported from db_info.txt.`,
    severity ? `Severity ${severity}.` : '',
    status ? `Current status ${status}.` : '',
    actor ? `Primary actor ${actor}.` : '',
    location ? `Location ${location}.` : ''
  ].filter(Boolean).join(' ');
}

function resolveDbInfoHeaders(payload){
  const headers = [];
  const seenHeaders = new Set();
  const sourceHeaders = Array.isArray(payload?.headers) ? payload.headers : [];

  sourceHeaders.forEach((header) => {
    const normalizedHeader = String(header || '').trim();
    const headerKey = normalizedHeader.toLowerCase();
    if(!normalizedHeader || seenHeaders.has(headerKey)) return;
    seenHeaders.add(headerKey);
    headers.push(normalizedHeader);
  });

  if(headers.length) return headers;

  const records = Array.isArray(payload?.purview)
    ? payload.purview
    : (Array.isArray(payload?.records) ? payload.records : []);
  const firstRecord = records.find((record) => record && typeof record === 'object');
  if(!firstRecord) return headers;

  Object.keys(firstRecord).forEach((header) => {
    const normalizedHeader = String(header || '').trim();
    const headerKey = normalizedHeader.toLowerCase();
    if(!normalizedHeader || seenHeaders.has(headerKey)) return;
    seenHeaders.add(headerKey);
    headers.push(normalizedHeader);
  });

  return headers;
}

function buildIncidentRowsFromDbInfo(payload){
  const headers = resolveDbInfoHeaders(payload);
  const records = Array.isArray(payload?.purview)
    ? payload.purview
    : (Array.isArray(payload?.records) ? payload.records : []);

  const seenRowIds = new Set();

  return records.map((record, index) => {
    const alertName = getRecordField(record, ['Alert Name', 'AlertName']);
    const detection = extractQuotedAlertText(alertName) || getRecordField(record, ['alert_DF', 'Alert DF', 'Detection']) || alertName || `Purview Alert ${index + 1}`;
    const incidentId = getRecordField(record, ['Incident ID', 'Incident Id', 'IncidentId']) || `PURVIEW-${String(index + 1).padStart(4, '0')}`;
    const ticket = getRecordField(record, ['ServiceNow Ticket', 'SNOW Ticket', 'Ticket', 'Case Number']) || 'N/A';
    const dateRaw = getRecordField(record, ['Date', 'Alert Date', 'Created', 'Timestamp', 'Time', 'Detected Time']);
    const status = getRecordField(record, ['Status', 'Alert Status', 'AlertStatus']) || 'Open';
    const severity = getRecordField(record, ['Severity', 'Alert Severity']) || 'Medium';
    const assignedTo = getRecordField(record, ['Assigned To', 'Assignee', 'Owner']) || DEFAULT_INCIDENT_ASSIGNEE;
    const actor = getRecordField(record, ['Actor', 'User', 'User Name', 'Matched User', 'Created By', 'Owner']);
    const location = getRecordField(record, ['Location', 'Workload', 'Data Source', 'Source']) || 'Microsoft Purview';
    const policy = getRecordField(record, ['Policy', 'Policy Name', 'Rule', 'DLP Policy']) || 'Purview Alert Policy';
    const artifactName = getRecordField(record, ['File Name', 'Document Name', 'Artifact', 'Item Name', 'Asset']);
    const classification = getRecordField(record, ['Classification']);
    const odinSummary = getRecordField(record, ['Odin Summary', 'ODIN Summary', 'odinSummary']) || buildDbInfoOdinSummary(alertName || detection, severity, status, actor, location);
    const odinConfidence = getRecordField(record, ['Odin Confidence', 'ODIN Confidence', 'odinConfidence']);
    const odinConfidenceLabel = getRecordField(record, ['Odin Confidence Label', 'ODIN Confidence Label', 'odinConfidenceLabel']);
    const odinWhat = getRecordList(record, ['Odin What Happened', 'ODIN What Happened', 'odinWhat']);
    const odinWhy = getRecordList(record, ['Odin Why It Matters', 'ODIN Why It Matters', 'odinWhy']);
    const odinNext = getRecordList(record, ['Odin Recommended Next Actions', 'ODIN Recommended Next Actions', 'odinNext']);
    const odinRawResponse = getRecordField(record, ['Odin Raw Response', 'ODIN Raw Response', 'odinRawResponse']);
    const rowId = `${incidentId}::${alertName || detection || `row-${index + 1}`}`;

    return {
      incidentId,
      ticket,
      date: toIsoDateOnly(dateRaw),
      name: alertName || detection,
      status,
      severity,
      assignedTo,
      actionUrl: '#',
      source: location ? `Microsoft Purview - ${location}` : 'Microsoft Purview',
      detection,
      actor,
      location,
      policy,
      recommendation: /high|critical/i.test(severity) ? 'Contain & Validate' : 'Validate & Document',
      artifact: artifactName,
      classification,
      impactedAssets: splitIncidentAssets(actor, location, artifactName),
      firstActivity: formatIncidentDate(dateRaw),
      lastActivity: formatIncidentDate(dateRaw),
      odinSummary,
      odinConfidence,
      odinConfidenceLabel,
      odinWhat,
      odinWhy,
      odinNext,
      odinRawResponse,
      dbInfoValues: headers.map((header) => {
        const value = record && typeof record === 'object' ? record[header] : '';
        return value == null ? '' : String(value);
      }),
      dbInfoRecord: record && typeof record === 'object' ? record : {},
      rowId
    };
  }).filter((row) => {
    if(seenRowIds.has(row.rowId)) return false;
    seenRowIds.add(row.rowId);
    return true;
  });
}

function parseDbInfoPayload(rawText){
  if(!String(rawText || '').trim()){
    return { headers: [], purview: [] };
  }

  const payload = JSON.parse(rawText);
  return {
    headers: Array.isArray(payload?.headers) ? payload.headers : [],
    purview: Array.isArray(payload?.purview)
      ? payload.purview
      : (Array.isArray(payload?.records) ? payload.records : [])
  };
}

function getEmbeddedDbInfoPayload(){
  const cachedPayload = window.__REDFLAGGED_DB_INFO__;
  if(!cachedPayload || typeof cachedPayload !== 'object'){
    return null;
  }

  return {
    headers: Array.isArray(cachedPayload.headers) ? cachedPayload.headers : [],
    purview: Array.isArray(cachedPayload.purview)
      ? cachedPayload.purview
      : (Array.isArray(cachedPayload.records) ? cachedPayload.records : [])
  };
}

function hasDbInfoRows(payload){
  return !!(payload && Array.isArray(payload.purview) && payload.purview.length > 0);
}

function applyIncidentTableColumns(headers){
  if(!incidentsTableHead || !incidentsTableColgroup) return;

  const resolvedHeaders = Array.isArray(headers)
    ? headers.map((header) => String(header || '').trim()).filter(Boolean)
    : [];

  if(!resolvedHeaders.length){
    incidentsTableColgroup.innerHTML = DEFAULT_INCIDENTS_TABLE_COLGROUP_HTML;
    incidentsTableHead.innerHTML = DEFAULT_INCIDENTS_TABLE_HEAD_HTML;
    incidentsTable?.classList.remove('idf-table-db-info');
    return;
  }

  incidentsTableColgroup.innerHTML = resolvedHeaders.map(() => '<col />').join('');
  incidentsTableHead.innerHTML = `<tr>${resolvedHeaders.map((header) => `<th>${escapeHtml(header)}</th>`).join('')}</tr>`;
  incidentsTable?.classList.add('idf-table-db-info');
}

function getVisibleDbInfoHeaders(headers){
  return (Array.isArray(headers) ? headers : [])
    .map((header) => String(header || '').trim())
    .filter((header) => {
      if(!header) return false;
      const normalizedHeader = header.toLowerCase();
      if(normalizedHeader === 'status') return false;
      if(/^odin\b/i.test(header) || normalizedHeader.startsWith('odin ')) return false;
      return true;
    });
}

function syncDashboardIncidentRows(){
  const nextRows = dbInfoIncidentRows.length ? dbInfoIncidentRows : [];

  incidentRows = mergeIncidentRowState(incidentRows, nextRows);
  renderIncidentsTable(incidentRows);
  renderExecutiveDashboard(incidentRows);
  loadExecutiveTrends();
}

function updateStatusBadgeClass(el, statusText){
  if(!el) return;
  el.classList.remove('good', 'warn', 'bad');
  const t = (statusText || '').toLowerCase();
  if(/closed|resolved|complete|completed|success/.test(t)) el.classList.add('good');
  else if(/new|pending|progress|investigat|open/.test(t)) el.classList.add('warn');
  else if(/fail|error|blocked|critical/.test(t)) el.classList.add('bad');
  else el.classList.add('warn');
}

function normalizeArtifactMatchValue(value){
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function getIncidentAlertNameCandidates(row){
  const candidates = [];
  const seen = new Set();

  const addCandidate = (value) => {
    const rawValue = String(value || '').trim();
    const normalizedValue = normalizeArtifactMatchValue(rawValue);
    if(!normalizedValue || seen.has(normalizedValue)) return;
    seen.add(normalizedValue);
    candidates.push(rawValue);
  };

  addCandidate(row?.name);
  addCandidate(row?.dbInfoRecord?.['Alert Name']);
  addCandidate(row?.dbInfoRecord?.AlertName);
  addCandidate(row?.dbInfoRecord?.alert_name);

  return candidates;
}

function getArtifactAlertNameCandidates(entry){
  const candidates = [];
  const seen = new Set();

  const addCandidate = (value) => {
    const rawValue = String(value || '').trim();
    const normalizedValue = normalizeArtifactMatchValue(rawValue);
    if(!normalizedValue || seen.has(normalizedValue)) return;
    seen.add(normalizedValue);
    candidates.push(normalizedValue);
  };

  addCandidate(entry?.alertName);
  addCandidate(entry?.alert_name);
  addCandidate(entry?.folderName);
  addCandidate(entry?.folder_name);
  addCandidate(entry?.folderPath);
  addCandidate(entry?.folder_path);
  addCandidate(entry?.detection);

  return candidates;
}

function findDbInfoIncidentRowForArtifact(entry){
  const sourceRows = Array.isArray(dbInfoIncidentRows) ? dbInfoIncidentRows.filter(Boolean) : [];
  if(!sourceRows.length || !entry) return null;

  const alertNameCandidates = getArtifactAlertNameCandidates(entry);
  const incidentIdCandidate = normalizeArtifactMatchValue(entry?.incidentId || entry?.incident_id);
  const matchesIncidentId = (row) => {
    if(!incidentIdCandidate) return true;
    const rowIncidentId = normalizeArtifactMatchValue(row?.incidentId || row?.dbInfoRecord?.['Incident ID']);
    return !!rowIncidentId && rowIncidentId === incidentIdCandidate;
  };

  if(alertNameCandidates.length){
    const exactAlertMatches = sourceRows.filter((row) => {
      const rowAlertCandidates = getIncidentAlertNameCandidates(row)
        .map((value) => normalizeArtifactMatchValue(value))
        .filter(Boolean);
      if(!rowAlertCandidates.length) return false;
      return rowAlertCandidates.some((candidate) => alertNameCandidates.includes(candidate)) && matchesIncidentId(row);
    });
    if(exactAlertMatches.length) return exactAlertMatches[0];

    const fuzzyAlertMatches = sourceRows.filter((row) => {
      const rowAlertCandidates = getIncidentAlertNameCandidates(row)
        .map((value) => normalizeArtifactMatchValue(value))
        .filter(Boolean);
      if(!rowAlertCandidates.length || !matchesIncidentId(row)) return false;
      return rowAlertCandidates.some((candidate) => {
        return alertNameCandidates.some((artifactCandidate) => {
          return candidate.includes(artifactCandidate) || artifactCandidate.includes(candidate);
        });
      });
    });
    if(fuzzyAlertMatches.length) return fuzzyAlertMatches[0];
  }

  if(!incidentIdCandidate) return null;
  return sourceRows.find((row) => matchesIncidentId(row)) || null;
}

function getModalArtifactSourceRows(){
  const currentArtifactRows = Array.isArray(artifactRows) ? artifactRows.filter(Boolean) : [];
  if(currentArtifactRows.length) return currentArtifactRows;

  const embeddedPayload = getEmbeddedArtifactsPayload();
  if(hasArtifactPayload(embeddedPayload)){
    return buildArtifactRows(embeddedPayload.files);
  }

  return [];
}

function getArtifactsForIncident(row){
  const sourceRows = getModalArtifactSourceRows();
  if(!sourceRows.length || !row) return [];

  const alertNameCandidates = getIncidentAlertNameCandidates(row)
    .map((value) => normalizeArtifactMatchValue(value))
    .filter(Boolean);
  const detectionCandidate = normalizeArtifactMatchValue(row?.detection);
  const incidentIdCandidate = normalizeArtifactMatchValue(row?.incidentId);

  const exactAlertMatches = sourceRows.filter((artifactRow) => {
    const artifactAlertName = normalizeArtifactMatchValue(artifactRow?.alertName);
    return artifactAlertName && alertNameCandidates.includes(artifactAlertName);
  });
  if(exactAlertMatches.length) return exactAlertMatches;

  const fuzzyAlertMatches = sourceRows.filter((artifactRow) => {
    const artifactAlertName = normalizeArtifactMatchValue(artifactRow?.alertName);
    if(!artifactAlertName) return false;

    if(alertNameCandidates.some((candidate) => artifactAlertName.includes(candidate) || candidate.includes(artifactAlertName))){
      return true;
    }

    return !!(detectionCandidate && artifactAlertName.includes(detectionCandidate));
  });
  if(fuzzyAlertMatches.length) return fuzzyAlertMatches;

  if(!incidentIdCandidate) return [];

  return sourceRows.filter((artifactRow) => normalizeArtifactMatchValue(artifactRow?.incidentId) === incidentIdCandidate);
}

function renderModalArtifacts(){
  const modalArtifactsList = document.getElementById('modal-artifacts-list');
  if(!modalArtifactsList) return;

  const incidentArtifacts = Array.isArray(activeModalArtifacts) ? activeModalArtifacts : [];
  if(!incidentArtifacts.length){
    modalArtifactsList.innerHTML = `
      <li>
        <div>
          <span>No artifacts found for this alert name</span><br/>
          <small class="mono" style="color: var(--muted);">${escapeHtml(activeModalRow?.name || activeModalRow?.detection || 'N/A')}</small>
        </div>
        <span class="badge warn">Missing</span>
      </li>
    `;
    if(modalArtifactsPagination) modalArtifactsPagination.innerHTML = '';
    return;
  }

  const totalPages = Math.max(1, Math.ceil(incidentArtifacts.length / MODAL_ARTIFACTS_PAGE_SIZE));
  modalArtifactsPage = Math.min(Math.max(modalArtifactsPage, 1), totalPages);

  const start = (modalArtifactsPage - 1) * MODAL_ARTIFACTS_PAGE_SIZE;
  const pageArtifacts = incidentArtifacts.slice(start, start + MODAL_ARTIFACTS_PAGE_SIZE);

  modalArtifactsList.innerHTML = pageArtifacts.map((artifactRow, index) => {
    const artifactLabel = artifactRow.artifact || `artifact_${start + index + 1}`;
    const artifactUrl = artifactFileUrl(artifactRow.relativePath || '');
    const artifactMeta = [artifactRow.date || 'N/A', getArtifactAlertStatus(artifactRow)]
      .map((value) => escapeHtml(value))
      .join(' | ');
    return `
      <li>
        <div>
          <span>Artifact - ${escapeHtml(artifactLabel)}</span><br/>
          <small class="mono" style="color: var(--muted);">${artifactMeta}</small>
        </div>
        <a class="badge good" href="${artifactUrl}" target="_blank" rel="noopener noreferrer">Open</a>
      </li>
    `;
  }).join('');

  if(!modalArtifactsPagination) return;
  if(totalPages <= 1){
    modalArtifactsPagination.innerHTML = '';
    return;
  }

  modalArtifactsPagination.innerHTML = `
    <button type="button" class="classification-history-page-btn" id="modal-artifacts-prev" ${modalArtifactsPage === 1 ? 'disabled' : ''}>Previous</button>
    <span class="classification-history-page-info">Page ${modalArtifactsPage} of ${totalPages}</span>
    <button type="button" class="classification-history-page-btn" id="modal-artifacts-next" ${modalArtifactsPage === totalPages ? 'disabled' : ''}>Next</button>
  `;

  const prevBtn = document.getElementById('modal-artifacts-prev');
  const nextBtn = document.getElementById('modal-artifacts-next');

  if(prevBtn){
    prevBtn.addEventListener('click', () => {
      if(modalArtifactsPage > 1){
        modalArtifactsPage -= 1;
        renderModalArtifacts();
      }
    });
  }

  if(nextBtn){
    nextBtn.addEventListener('click', () => {
      if(modalArtifactsPage < totalPages){
        modalArtifactsPage += 1;
        renderModalArtifacts();
      }
    });
  }
}

function openIncidentModal(row){
  if(!incidentModal || !row) return;
  activeModalRow = row;
  activeModalArtifacts = [];
  modalArtifactsPage = 1;
  updateAssignedAgentUI(row);

  const source = row.source || document.getElementById('case-source')?.textContent?.trim() || 'Microsoft Purview + Sentinel';

  const setText = (id, value) => {
    const el = document.getElementById(id);
    if(el) el.textContent = value || 'N/A';
  };

  setText('modal-incident-name', row.detection);
  setText('modal-incident-id', row.incidentId);
  setText('modal-ticket-number', row.ticket);
  setText('modal-date', row.date);
  setText('modal-assigned-to', row.assignedTo);
  setText('modal-source', source);
  setText('modal-detection', row.detection || 'Potential Sensitive Data Exfiltration');
  setText('modal-actor', row.actor || 'user@company.com');
  setText('modal-location', row.location || 'SharePoint Online');
  setText('modal-policy', row.policy || 'DLP – Confidential Data');
  setText('modal-recommendation', row.recommendation || 'Contain & Validate');

  const modalOdinSummary = document.getElementById('modal-odin-summary');
  if(modalOdinSummary){
    modalOdinSummary.textContent = row.odinSummary || 'No Odin summary available for this incident.';
  }

  const modalConfidence = document.getElementById('modal-odin-confidence');
  const modalConfidenceLabel = document.getElementById('modal-odin-confidence-label');
  const modalMode = document.getElementById('modal-odin-mode');
  const modalWhat = document.getElementById('modal-odin-what');
  const modalWhy = document.getElementById('modal-odin-why');
  const modalActions = document.getElementById('modal-odin-actions');

  let confidence = '82%';
  let confidenceLabel = 'Needs Review';
  if(/resolved|closed|complete/i.test(row.status || '')){
    confidence = '94%';
    confidenceLabel = 'Validated';
  } else if(/open|pending/i.test(row.status || '')){
    confidence = '78%';
    confidenceLabel = 'Needs Review';
  }

  if(modalConfidence) modalConfidence.textContent = confidence;
  if(modalConfidenceLabel) modalConfidenceLabel.textContent = confidenceLabel;
  if(modalMode) modalMode.textContent = `Mode: ${confidenceLabel === 'Validated' ? 'Verified' : 'Safe'}`;

  if(modalWhat){
    modalWhat.innerHTML = `
      <li>Correlation matched ${escapeHtml(row.policy || 'policy')} and alert telemetry for ${escapeHtml(row.incidentId || 'this incident')}.</li>
      <li>Primary indicator observed: ${escapeHtml(row.detection || 'Potential Sensitive Data Exfiltration')}.</li>
      <li>Affected context includes ${escapeHtml(row.location || 'SharePoint Online')} and actor ${escapeHtml(row.actor || 'user@company.com')}.</li>
    `;
  }

  if(modalWhy){
    modalWhy.innerHTML = `
      <li>Potential impact to regulated information and internal policy controls.</li>
      <li>Risk increases if external sharing and downloads are confirmed.</li>
      <li>Incident is assigned to ${escapeHtml(row.assignedTo || 'Security Managed Services')} for triage completion.</li>
    `;
  }

  if(modalActions){
    modalActions.innerHTML = `
      <li>Validate ownership and access scope for ticket ${escapeHtml(row.ticket || 'N/A')}.</li>
      <li>Collect remaining artifacts and confirm user intent with manager approval path.</li>
      <li>If validated, execute containment and close-loop documentation.</li>
    `;
  }

  bindModalActionItems(row);

  if(modalCommentBox){
    modalCommentBox.value = row.comment || '';
  }

  activeModalArtifacts = getArtifactsForIncident(row);
  renderModalArtifacts();

  const statusEl = document.getElementById('modal-status');
  if(statusEl){
    statusEl.textContent = row.status || 'In Progress';
    updateStatusBadgeClass(statusEl, row.status || 'In Progress');
  }

  incidentModal.classList.add('active');
  incidentModal.setAttribute('aria-hidden', 'false');
}

function closeIncidentModal(){
  if(!incidentModal) return;
  incidentModal.classList.remove('active');
  incidentModal.setAttribute('aria-hidden', 'true');
  activeModalArtifacts = [];
  modalArtifactsPage = 1;
  if(modalArtifactsPagination) modalArtifactsPagination.innerHTML = '';
  activeModalRow = null;
}

if(modalCommentBox){
  modalCommentBox.addEventListener('input', () => {
    if(activeModalRow){
      activeModalRow.comment = modalCommentBox.value;
    }
  });
}

function formatSize(bytes){
  if (bytes < 1024) return bytes + ' B';
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

function escapeHtml(value){
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderOdinSummaryFromIncident(row){
  const odinSummaryConsoleEl = document.getElementById('odin-summary-console');
  if(!odinSummaryConsoleEl || !row) return;

  let confidence = row.odinConfidence || '82%';
  let confidenceLabel = row.odinConfidenceLabel || 'Needs Review';
  if(!row.odinConfidence && !row.odinConfidenceLabel){
    if(/resolved|closed|complete/i.test(row.status || '')){
      confidence = '94%';
      confidenceLabel = 'Validated';
    } else if(/open|pending/i.test(row.status || '')){
      confidence = '78%';
      confidenceLabel = 'Needs Review';
    }
  }

  const whatItems = Array.isArray(row.odinWhat) && row.odinWhat.length
    ? row.odinWhat
    : [
        `Correlation matched ${row.policy || 'policy'} and alert telemetry for ${row.incidentId || 'this incident'}.`,
        `Primary indicator observed: ${row.detection || 'Potential Sensitive Data Exfiltration'}.`,
        `Affected context includes ${row.location || 'SharePoint Online'} and actor ${row.actor || 'user@company.com'}.`
      ];
  const whyItems = Array.isArray(row.odinWhy) && row.odinWhy.length
    ? row.odinWhy
    : [
        'Potential impact to regulated information and internal policy controls.',
        'Risk increases if external sharing and downloads are confirmed.',
        `Incident is assigned to ${row.assignedTo || 'Security Managed Services'} for triage completion.`
      ];
  const nextItems = Array.isArray(row.odinNext) && row.odinNext.length
    ? row.odinNext
    : [
        `Validate ownership and access scope for ticket ${row.ticket || 'N/A'}.`,
        'Collect remaining artifacts and confirm user intent with manager approval path.',
        'If validated, execute containment and close-loop documentation.'
      ];

  odinSummaryConsoleEl.innerHTML = `
    <div class="odin-main-view">
      <div class="odin-highlight">
        <div class="odin-highlight-head">
          <span class="badge warn">ODIN • AI Findings</span>
          <div class="odin-confidence">
            <small>Confidence</small>
            <b>${escapeHtml(confidence)}</b>
            <span class="badge warn">${escapeHtml(confidenceLabel)}</span>
          </div>
        </div>
        <h4>Summary of Assessment</h4>
        <div class="modal-odin-text">${escapeHtml(row.odinSummary || 'No Odin summary available for this incident.')}</div>
      </div>

      <div class="odin-block">
        <h4>What happened</h4>
        <ul class="odin-list">
          ${whatItems.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
        </ul>
      </div>

      <div class="odin-block">
        <h4>Why it matters</h4>
        <ul class="odin-list">
          ${whyItems.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
        </ul>
      </div>

      <div class="odin-block">
        <h4>Recommended next actions</h4>
        <ol class="odin-list-ordered">
          ${nextItems.map((item) => `<li>${escapeHtml(item)}</li>`).join('')}
        </ol>
      </div>
    </div>
  `;
}

function selectIncident(row){
  if(!row) return;
  selectedIncidentForOdin = row;
  updateAssignedAgentUI(row);
  renderOdinSummaryFromIncident(row);
}

function fileViewUrl(name){
  return '/files/' + encodeURIComponent(name);
}

function setActiveTab(tabName){
  if(tabIncidents) tabIncidents.classList.toggle('active', tabName === 'incidents');
  if(tabExecutive) tabExecutive.classList.toggle('active', tabName === 'executive');
  if(tabArtifacts) tabArtifacts.classList.toggle('active', tabName === 'artifacts');
  if(tabMonitoring) tabMonitoring.classList.toggle('active', tabName === 'monitoring');
  if(tabSelectors) tabSelectors.classList.toggle('active', tabName === 'selectors');
  if(tabSettings) tabSettings.classList.toggle('active', tabName === 'settings');
  if(incidentsTabContent) incidentsTabContent.classList.toggle('active', tabName === 'incidents');
  if(executiveTabContent) executiveTabContent.classList.toggle('active', tabName === 'executive');
  if(artifactsTabContent) artifactsTabContent.classList.toggle('active', tabName === 'artifacts');
  if(monitoringTabContent) monitoringTabContent.classList.toggle('active', tabName === 'monitoring');
  if(selectorsTabContent) selectorsTabContent.classList.toggle('active', tabName === 'selectors');
  if(settingsTabContent) settingsTabContent.classList.toggle('active', tabName === 'settings');

  const executiveMode = tabName === 'executive' || tabName === 'artifacts' || tabName === 'monitoring' || tabName === 'selectors' || tabName === 'settings';
  const hideRightConsole = tabName === 'executive' || tabName === 'settings' || tabName === 'artifacts' || tabName === 'monitoring' || tabName === 'selectors';
  if(mainWrap) mainWrap.classList.toggle('executive-mode', executiveMode);
  if(rightConsolePanel) rightConsolePanel.classList.toggle('is-hidden', hideRightConsole);
  if(mainPanelTitle) mainPanelTitle.textContent = tabName === 'settings'
    ? 'System Settings'
    : tabName === 'selectors'
      ? 'Selector Configuration'
      : tabName === 'monitoring'
        ? 'Automation Monitoring'
        : 'Incident Overview';
  if(mainPanelSubtitle) mainPanelSubtitle.textContent = tabName === 'settings'
    ? 'Manage dashboard appearance and system preferences'
    : tabName === 'selectors'
      ? 'Define automation page selectors, separated by portal link'
      : tabName === 'monitoring'
        ? 'Live view of dashboard-managed Python background processes'
        : 'Unified view of case details and evidence collected';
  if(mainPanelChips){
    const hideChips = tabName === 'settings' || tabName === 'executive' || tabName === 'monitoring' || tabName === 'selectors';
    mainPanelChips.hidden = hideChips;
    mainPanelChips.style.display = hideChips ? 'none' : 'flex';
  }
}

function getFilteredClassificationHistoryRows(){
  const yearFilter = executiveYearFilter ? executiveYearFilter.value : 'all';
  const monthFilter = executiveMonthFilter ? executiveMonthFilter.value : 'all';
  const classificationFilter = executiveClassificationFilter ? executiveClassificationFilter.value : 'all';
  const searchTerm = executiveHistorySearch ? executiveHistorySearch.value.trim().toLowerCase() : '';

  return executiveHistoryRows.filter((item) => {
    const itemYear = item.key === 'unknown' ? 'unknown' : item.key.slice(0, 4);
    const itemMonth = item.key === 'unknown' ? 'unknown' : item.key.slice(5, 7);

    if(yearFilter !== 'all' && itemYear !== yearFilter) return false;
    if(monthFilter !== 'all' && itemMonth !== monthFilter) return false;

    if(classificationFilter === 'true-positive' && item.truePositive === 0) return false;
    if(classificationFilter === 'false-positive' && item.falsePositive === 0) return false;
    if(classificationFilter === 'benign' && item.benign === 0) return false;

    if(searchTerm){
      const searchable = `${item.label} ${itemYear}`.toLowerCase();
      if(!searchable.includes(searchTerm)) return false;
    }

    return true;
  });
}

function updateExecutiveYearFilterOptions(){
  if(!executiveYearFilter) return;
  const previousValue = executiveYearFilter.value || 'all';
  const yearSet = new Set();

  executiveHistoryRows.forEach((item) => {
    if(item.key !== 'unknown') yearSet.add(item.key.slice(0, 4));
  });

  const yearOptions = Array.from(yearSet).sort((a, b) => (a < b ? 1 : -1));
  executiveYearFilter.innerHTML = '<option value="all">All Years</option>' +
    yearOptions.map((year) => `<option value="${year}">${year}</option>`).join('');

  const shouldKeep = previousValue === 'all' || yearSet.has(previousValue);
  executiveYearFilter.value = shouldKeep ? previousValue : 'all';
}

function escapeCsvValue(value){
  const text = String(value ?? '');
  if(/[",\n]/.test(text)) return `"${text.replaceAll('"', '""')}"`;
  return text;
}

function exportClassificationHistoryCsv(){
  const filteredRows = getFilteredClassificationHistoryRows();
  if(filteredRows.length === 0){
    showNotification({
      title: 'Export Unavailable',
      text: 'No classification history rows match the selected filters.',
      status: 'Warning',
      source: 'Executive Dashboard'
    });
    return;
  }

  const header = ['Month / Year', 'Total', 'True Positive', 'False Positive', 'Benign'];
  const lines = [header.join(',')];

  filteredRows.forEach((item) => {
    lines.push([
      escapeCsvValue(item.label),
      escapeCsvValue(item.total),
      escapeCsvValue(item.truePositive),
      escapeCsvValue(item.falsePositive),
      escapeCsvValue(item.benign)
    ].join(','));
  });

  const csvText = lines.join('\n');
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);

  const now = new Date();
  const dateStamp = now.toISOString().slice(0, 10);
  const link = document.createElement('a');
  link.href = url;
  link.download = `classification_history_${dateStamp}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);

  showNotification({
    title: 'Export Complete',
    text: `Downloaded ${filteredRows.length} classification history row(s).`,
    status: 'Success',
    source: 'Executive Dashboard'
  });
}

function renderClassificationHistoryTable(){
  if(!classificationHistoryBody || !classificationHistoryPagination) return;

  const filteredRows = getFilteredClassificationHistoryRows();
  const totalPages = Math.max(1, Math.ceil(filteredRows.length / CLASSIFICATION_HISTORY_PAGE_SIZE));
  classificationHistoryPage = Math.min(Math.max(classificationHistoryPage, 1), totalPages);

  if(filteredRows.length === 0){
    classificationHistoryBody.innerHTML = '<tr><td colspan="5" class="incident-empty">No history rows match the selected filters.</td></tr>';
    classificationHistoryPagination.innerHTML = '';
    if(classificationHistoryExport) classificationHistoryExport.disabled = true;
    return;
  }

  if(classificationHistoryExport) classificationHistoryExport.disabled = false;

  const start = (classificationHistoryPage - 1) * CLASSIFICATION_HISTORY_PAGE_SIZE;
  const pageRows = filteredRows.slice(start, start + CLASSIFICATION_HISTORY_PAGE_SIZE);

  classificationHistoryBody.innerHTML = pageRows.map((item) => `
    <tr>
      <td class="mono">${escapeHtml(item.label)}</td>
      <td>${item.total}</td>
      <td>${item.truePositive}</td>
      <td>${item.falsePositive}</td>
      <td>${item.benign}</td>
    </tr>
  `).join('');

  if(totalPages <= 1){
    classificationHistoryPagination.innerHTML = '';
    return;
  }

  classificationHistoryPagination.innerHTML = `
    <button type="button" class="classification-history-page-btn" id="classification-history-prev" ${classificationHistoryPage === 1 ? 'disabled' : ''}>Previous</button>
    <span class="classification-history-page-info">Page ${classificationHistoryPage} of ${totalPages}</span>
    <button type="button" class="classification-history-page-btn" id="classification-history-next" ${classificationHistoryPage === totalPages ? 'disabled' : ''}>Next</button>
  `;

  const prevBtn = document.getElementById('classification-history-prev');
  const nextBtn = document.getElementById('classification-history-next');

  if(prevBtn){
    prevBtn.addEventListener('click', () => {
      if(classificationHistoryPage > 1){
        classificationHistoryPage -= 1;
        renderClassificationHistoryTable();
      }
    });
  }

  if(nextBtn){
    nextBtn.addEventListener('click', () => {
      if(classificationHistoryPage < totalPages){
        classificationHistoryPage += 1;
        renderClassificationHistoryTable();
      }
    });
  }
}

function renderExecutiveDetailTable(options){
  const {
    rows,
    bodyEl,
    paginationEl,
    emptyMessage,
    page,
    setPage,
    rowRenderer,
    paginationKey
  } = options;

  if(!bodyEl || !paginationEl) return;

  const allRows = Array.isArray(rows) ? rows : [];
  const totalPages = Math.max(1, Math.ceil(allRows.length / EXECUTIVE_DETAIL_PAGE_SIZE));
  const nextPage = Math.min(Math.max(page, 1), totalPages);
  setPage(nextPage);

  if(allRows.length === 0){
    bodyEl.innerHTML = emptyMessage;
    paginationEl.innerHTML = '';
    return;
  }

  const start = (nextPage - 1) * EXECUTIVE_DETAIL_PAGE_SIZE;
  const pageRows = allRows.slice(start, start + EXECUTIVE_DETAIL_PAGE_SIZE);
  bodyEl.innerHTML = pageRows.map(rowRenderer).join('');

  if(totalPages <= 1){
    paginationEl.innerHTML = '';
    return;
  }

  const prevId = `${paginationKey}-prev`;
  const nextId = `${paginationKey}-next`;

  paginationEl.innerHTML = `
    <button type="button" class="classification-history-page-btn" id="${prevId}" ${nextPage === 1 ? 'disabled' : ''}>Previous</button>
    <span class="classification-history-page-info">Page ${nextPage} of ${totalPages}</span>
    <button type="button" class="classification-history-page-btn" id="${nextId}" ${nextPage === totalPages ? 'disabled' : ''}>Next</button>
  `;

  const prevBtn = document.getElementById(prevId);
  const nextBtn = document.getElementById(nextId);

  if(prevBtn){
    prevBtn.addEventListener('click', () => {
      if(nextPage > 1) setPage(nextPage - 1);
      if(paginationKey === 'executive-top-actors') renderExecutiveDashboard(incidentRows);
      if(paginationKey === 'executive-top-departments') renderExecutiveDashboard(incidentRows);
    });
  }

  if(nextBtn){
    nextBtn.addEventListener('click', () => {
      if(nextPage < totalPages) setPage(nextPage + 1);
      if(paginationKey === 'executive-top-actors') renderExecutiveDashboard(incidentRows);
      if(paginationKey === 'executive-top-departments') renderExecutiveDashboard(incidentRows);
    });
  }
}

function renderExecutiveDashboard(rows){
  const hasExecutiveElements = metricTotalIncidents && metricTruePositive && metricFalsePositive && metricBenign;
  if(!hasExecutiveElements) return;

  const resolveClassification = (row) => {
    const explicit = getIncidentClassificationBucket(row.classification);
    if(explicit) return explicit;
    const summaryHints = `${row.recommendation || ''} ${row.odinSummary || ''}`;
    if(/false\s*positive/i.test(summaryHints)) return 'False Positive';
    if(/\bbenign\b/i.test(summaryHints)) return 'Benign';
    return '';
  };

  const parseIncidentDate = (value) => {
    if(!value) return null;
    const d = new Date(value);
    if(Number.isNaN(d.getTime())) return null;
    return d;
  };

  const formatMonthYear = (dateObj) => {
    return new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric' }).format(dateObj);
  };

  const allRows = Array.isArray(rows) ? rows : [];
  const total = allRows.length;

  let truePositiveCount = 0;
  let falsePositiveCount = 0;
  let benignCount = 0;
  const monthYearMap = new Map();
  const actorMap = new Map();
  const departmentMap = new Map();

  allRows.forEach((row) => {
    const classification = resolveClassification(row);
    if(classification === 'True Positive') truePositiveCount += 1;
    if(classification === 'False Positive') falsePositiveCount += 1;
    if(classification === 'Benign') benignCount += 1;

    const incidentDate = parseIncidentDate(row.date);
    const monthKey = incidentDate
      ? `${incidentDate.getUTCFullYear()}-${String(incidentDate.getUTCMonth() + 1).padStart(2, '0')}`
      : 'unknown';
    const monthLabel = incidentDate ? formatMonthYear(incidentDate) : 'Unknown Date';

    if(!monthYearMap.has(monthKey)){
      monthYearMap.set(monthKey, {
        key: monthKey,
        label: monthLabel,
        total: 0,
        truePositive: 0,
        falsePositive: 0,
        benign: 0
      });
    }

    const bucket = monthYearMap.get(monthKey);
    bucket.total += 1;
    if(classification === 'True Positive') bucket.truePositive += 1;
    if(classification === 'False Positive') bucket.falsePositive += 1;
    if(classification === 'Benign') bucket.benign += 1;

    const actorName = row.actor || 'Unknown Actor';
    const actorEntry = actorMap.get(actorName) || {
      actor: actorName,
      incidents: 0,
      lastSeen: row.date || 'N/A'
    };
    actorEntry.incidents += 1;
    if((row.date || '') > (actorEntry.lastSeen || '')) actorEntry.lastSeen = row.date || 'N/A';
    actorMap.set(actorName, actorEntry);

    const departmentName = row.department || row.assignedTo || 'Unassigned';
    const departmentEntry = departmentMap.get(departmentName) || {
      department: departmentName,
      incidents: 0
    };
    departmentEntry.incidents += 1;
    departmentMap.set(departmentName, departmentEntry);
  });

  metricTotalIncidents.textContent = String(total);
  metricTruePositive.textContent = String(truePositiveCount);
  metricFalsePositive.textContent = String(falsePositiveCount);
  metricBenign.textContent = String(benignCount);

  const toPct = (value) => total === 0 ? 0 : Math.round((value / total) * 100);
  if(classChartTruePositive) classChartTruePositive.style.width = `${toPct(truePositiveCount)}%`;
  if(classChartFalsePositive) classChartFalsePositive.style.width = `${toPct(falsePositiveCount)}%`;
  if(classChartBenign) classChartBenign.style.width = `${toPct(benignCount)}%`;

  if(classChartTruePositiveCount) classChartTruePositiveCount.textContent = String(truePositiveCount);
  if(classChartFalsePositiveCount) classChartFalsePositiveCount.textContent = String(falsePositiveCount);
  if(classChartBenignCount) classChartBenignCount.textContent = String(benignCount);

  executiveHistoryRows = Array.from(monthYearMap.values()).sort((a, b) => {
    if(a.key === 'unknown') return 1;
    if(b.key === 'unknown') return -1;
    return a.key < b.key ? 1 : -1;
  });

  const topActors = Array.from(actorMap.values())
    .sort((a, b) => b.incidents - a.incidents || String(a.actor).localeCompare(String(b.actor)))
    ;

  const topDepartments = Array.from(departmentMap.values())
    .sort((a, b) => b.incidents - a.incidents || String(a.department).localeCompare(String(b.department)))
    .map((item) => ({
      ...item,
      share: total === 0 ? '0%' : `${Math.round((item.incidents / total) * 100)}%`
    }));

  renderExecutiveDetailTable({
    rows: topActors,
    bodyEl: executiveTopActorsBody,
    paginationEl: executiveTopActorsPagination,
    emptyMessage: '<tr><td colspan="3" class="incident-empty">No actor data available.</td></tr>',
    page: executiveTopActorsPage,
    setPage: (value) => { executiveTopActorsPage = value; },
    rowRenderer: (item) => `
      <tr>
        <td>${escapeHtml(item.actor)}</td>
        <td>${item.incidents}</td>
        <td class="mono">${escapeHtml(item.lastSeen || 'N/A')}</td>
      </tr>
    `,
    paginationKey: 'executive-top-actors'
  });

  renderExecutiveDetailTable({
    rows: topDepartments,
    bodyEl: executiveTopDepartmentsBody,
    paginationEl: executiveTopDepartmentsPagination,
    emptyMessage: '<tr><td colspan="3" class="incident-empty">No department data available.</td></tr>',
    page: executiveTopDepartmentsPage,
    setPage: (value) => { executiveTopDepartmentsPage = value; },
    rowRenderer: (item) => `
      <tr>
        <td>${escapeHtml(item.department)}</td>
        <td>${item.incidents}</td>
        <td>${escapeHtml(item.share)}</td>
      </tr>
    `,
    paginationKey: 'executive-top-departments'
  });

  updateExecutiveYearFilterOptions();
  renderClassificationHistoryTable();
}

function renderTrendRows(containerEl, items, labelKey, formatLabel){
  if(!containerEl) return;
  if(!items || !items.length){
    containerEl.innerHTML = '<div class="incident-empty">No incidents recorded yet.</div>';
    return;
  }
  const maxCount = Math.max(...items.map((item) => item.count), 1);
  containerEl.innerHTML = items.map((item) => {
    const pct = Math.round((item.count / maxCount) * 100);
    const label = formatLabel ? formatLabel(item[labelKey]) : item[labelKey];
    return (
      '<div class="chart-row">' +
        '<span>' + label + '</span>' +
        '<div class="chart-bar"><div class="chart-fill good" style="width:' + pct + '%"></div></div>' +
        '<small>' + item.count + '</small>' +
      '</div>'
    );
  }).join('');
}

function formatTrendDay(dayStr){
  const d = new Date(dayStr + 'T00:00:00Z');
  if(Number.isNaN(d.getTime())) return dayStr;
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(d);
}

function formatTrendMonth(monthStr){
  const d = new Date(monthStr + '-01T00:00:00Z');
  if(Number.isNaN(d.getTime())) return monthStr;
  return new Intl.DateTimeFormat('en-US', { month: 'short', year: 'numeric', timeZone: 'UTC' }).format(d);
}

async function loadExecutiveTrends(){
  const dailyEl = document.getElementById('trend-daily-rows');
  const monthlyEl = document.getElementById('trend-monthly-rows');
  if(!dailyEl && !monthlyEl) return;
  try {
    const resp = await fetch('/api/report/trends').then((r) => r.json());
    if(!resp.ok) throw new Error(resp.error || 'Failed to load trends.');
    renderTrendRows(dailyEl, resp.daily, 'date', formatTrendDay);
    renderTrendRows(monthlyEl, resp.monthly, 'month', formatTrendMonth);
  } catch (e) {
    if(dailyEl) dailyEl.innerHTML = '<div class="incident-empty">Could not load daily trend.</div>';
    if(monthlyEl) monthlyEl.innerHTML = '<div class="incident-empty">Could not load monthly trend.</div>';
  }
}

function getSystemThemeMode(){
  return systemThemeMedia && systemThemeMedia.matches ? 'dark' : 'light';
}

function sanitizeThemePreference(value){
  if(value === THEME_SYSTEM) return THEME_SYSTEM;
  return supportedThemeModes.has(value) ? value : THEME_SYSTEM;
}

function resolveThemeMode(preference){
  const pref = sanitizeThemePreference(preference);
  if(pref === THEME_SYSTEM) return getSystemThemeMode();
  return pref;
}

function applyThemeMode(mode){
  const root = document.documentElement;
  root.setAttribute('data-theme', mode);
}

function syncThemeFromStoredPreference(){
  const stored = localStorage.getItem(THEME_PREF_KEY) || THEME_SYSTEM;
  const preference = sanitizeThemePreference(stored);
  const resolved = resolveThemeMode(preference);
  applyThemeMode(resolved);
  if(settingsThemeSelect) settingsThemeSelect.value = preference;
}

function initSettingsTheme(){
  syncThemeFromStoredPreference();

  if(settingsThemeSelect){
    settingsThemeSelect.addEventListener('change', () => {
      const preference = sanitizeThemePreference(settingsThemeSelect.value);
      localStorage.setItem(THEME_PREF_KEY, preference);
      applyThemeMode(resolveThemeMode(preference));
      showNotification({
        title: 'Theme Updated',
        text: `Theme changed to ${preference === THEME_SYSTEM ? 'system default' : preference}.`,
        status: 'Success',
        source: 'Settings'
      });
    });
  }

  if(systemThemeMedia){
    const onSystemThemeChange = () => {
      const preference = sanitizeThemePreference(localStorage.getItem(THEME_PREF_KEY) || THEME_SYSTEM);
      if(preference === THEME_SYSTEM){
        applyThemeMode(resolveThemeMode(preference));
      }
    };

    if(typeof systemThemeMedia.addEventListener === 'function'){
      systemThemeMedia.addEventListener('change', onSystemThemeChange);
    } else if(typeof systemThemeMedia.addListener === 'function'){
      systemThemeMedia.addListener(onSystemThemeChange);
    }
  }
}

function initSettingsTimezone(){
  const timezoneKey = 'rf_timezone_preference';
  const storedTimezone = localStorage.getItem(timezoneKey) || 'PHT';
  
  if(settingsTimezoneSelect){
    settingsTimezoneSelect.value = storedTimezone;
    settingsTimezoneSelect.addEventListener('change', () => {
      const selectedTimezone = settingsTimezoneSelect.value;
      localStorage.setItem(timezoneKey, selectedTimezone);
      showNotification({
        title: 'Time Zone Updated',
        text: `Time zone changed to ${selectedTimezone}.`,
        status: 'Success',
        source: 'Settings'
      });
    });
  }
}

function initSettingsFontSize(){
  const FONT_SIZE_KEY = 'rf_fontsize_preference';
  const storedSize = localStorage.getItem(FONT_SIZE_KEY) || '100';
  document.documentElement.style.fontSize = (parseInt(storedSize, 10) / 100) * 16 + 'px';
  const fontSizeSelect = document.getElementById('settings-fontsize-select');
  if(fontSizeSelect){
    fontSizeSelect.value = storedSize;
    fontSizeSelect.addEventListener('change', () => {
      const val = fontSizeSelect.value;
      localStorage.setItem(FONT_SIZE_KEY, val);
      document.documentElement.style.fontSize = (parseInt(val, 10) / 100) * 16 + 'px';
      showNotification({
        title: 'Font Size Updated',
        text: `Font size changed to ${val}%.`,
        status: 'Success',
        source: 'Settings'
      });
    });
  }
}

function setSystemConfigDirty(isDirty){
  systemConfigDirty = isDirty;
}

function getSystemConfigStatusTone(statusText){
  const normalized = String(statusText || '').toLowerCase();
  if(/running/.test(normalized)) return 'good';
  if(/scheduled|terminating/.test(normalized)) return 'warn';
  if(/error/.test(normalized)) return 'bad';
  return 'warn';
}

function getMonitoringStatusTone(statusText){
  const normalized = String(statusText || '').toLowerCase();
  if(/error/.test(normalized)) return 'bad';
  if(/terminated/.test(normalized)) return 'good';
  if(/pending|progress/.test(normalized)) return 'warn';
  return 'warn';
}

function setSystemConfigFeedback(message, tone = 'muted'){
  if(!systemConfigFeedback) return;
  systemConfigFeedback.textContent = message || 'Waiting for the automation backend.';
  systemConfigFeedback.classList.remove('c-good', 'c-warn', 'c-bad', 'c-muted');
  if(tone === 'good') systemConfigFeedback.classList.add('c-good');
  else if(tone === 'warn') systemConfigFeedback.classList.add('c-warn');
  else if(tone === 'bad') systemConfigFeedback.classList.add('c-bad');
  else systemConfigFeedback.classList.add('c-muted');
}

function formatScheduleTimestamp(value){
  if(!value) return 'Not scheduled';
  const date = new Date(value);
  if(Number.isNaN(date.getTime())) return 'Not scheduled';
  return date.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit'
  });
}

function formatMonitoringTimestamp(value){
  if(!value) return 'N/A';
  const date = new Date(value);
  if(Number.isNaN(date.getTime())) return 'N/A';
  return date.toLocaleString([], {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  });
}

function formatMonitoringDuration(startedAt, stoppedAt){
  if(!startedAt) return 'N/A';

  const started = new Date(startedAt);
  if(Number.isNaN(started.getTime())) return 'N/A';

  const ended = stoppedAt ? new Date(stoppedAt) : null;
  const endTime = ended && !Number.isNaN(ended.getTime()) ? ended.getTime() : Date.now();
  const elapsedMs = Math.max(0, endTime - started.getTime());
  const totalSeconds = Math.floor(elapsedMs / 1000);
  const days = Math.floor(totalSeconds / 86400);
  const hours = Math.floor((totalSeconds % 86400) / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  const clock = [hours, minutes, seconds].map((value) => String(value).padStart(2, '0')).join(':');

  return days > 0 ? `${days}d ${clock}` : clock;
}

function updateMonitoringRuntimeCells(){
  const runtimeCells = document.querySelectorAll('[data-monitoring-runtime]');
  runtimeCells.forEach((cell) => {
    const startedAt = cell.getAttribute('data-started-at');
    const stoppedAt = cell.getAttribute('data-stopped-at');
    cell.textContent = formatMonitoringDuration(startedAt, stoppedAt);
  });
}

function ensureMonitoringRealtimeTimer(){
  if(monitoringRealtimeHandle !== null) return;
  monitoringRealtimeHandle = window.setInterval(() => {
    updateMonitoringRuntimeCells();
  }, 1000);
}

function renderMonitoringStatus(payload){
  if(!monitoringProcessBody) return;

  const processes = Array.isArray(payload?.monitoring) ? payload.monitoring : [];
  const fallbackMessage = payload?.message || 'Waiting for the automation backend.';

  if(monitoringLastUpdated){
    monitoringLastUpdated.textContent = payload?.monitoring_updated_at
      ? `Last updated ${formatMonitoringTimestamp(payload.monitoring_updated_at)}`
      : fallbackMessage;
  }

  if(!processes.length){
    monitoringProcessBody.innerHTML = `<tr><td colspan="7" class="incident-empty">${escapeHtml(fallbackMessage)}</td></tr>`;
    return;
  }

  monitoringProcessBody.innerHTML = processes.map((item) => {
    const fileName = String(item?.file || 'Unknown');
    const status = String(item?.status || 'Pending');
    const pidText = item?.pid == null ? 'N/A' : String(item.pid);
    const startedAt = item?.started_at ? String(item.started_at) : '';
    const stoppedAt = item?.stopped_at ? String(item.stopped_at) : '';
    const details = [
      item?.incident_id ? `Incident ${String(item.incident_id)}` : '',
      item?.message ? String(item.message) : '',
      item?.last_exit_code == null ? '' : `Exit code ${String(item.last_exit_code)}`
    ].filter(Boolean).join(' | ');

    return `
      <tr>
        <td class="mono">${escapeHtml(fileName)}</td>
        <td><span class="badge ${getMonitoringStatusTone(status)}">${escapeHtml(status)}</span></td>
        <td class="mono">${escapeHtml(pidText)}</td>
        <td class="mono">${escapeHtml(formatMonitoringTimestamp(startedAt))}</td>
        <td class="mono" data-monitoring-runtime data-started-at="${escapeHtml(startedAt)}" data-stopped-at="${escapeHtml(stoppedAt)}">${escapeHtml(formatMonitoringDuration(startedAt, stoppedAt))}</td>
        <td class="mono">${escapeHtml(formatMonitoringTimestamp(stoppedAt))}</td>
        <td>${escapeHtml(details || 'No additional details.')}</td>
      </tr>
    `;
  }).join('');

  updateMonitoringRuntimeCells();
}

function updateSystemConfigFieldState(){
  const runEnabled = !!autoRunEnabled?.checked;
  const stopEnabled = !!autoStopEnabled?.checked;
  const runWeekly = autoRunFrequency?.value === 'weekly';
  const stopWeekly = autoStopFrequency?.value === 'weekly';

  if(autoRunFrequency) autoRunFrequency.disabled = !runEnabled;
  if(autoRunDay) autoRunDay.disabled = !runEnabled || !runWeekly;
  if(autoRunTime) autoRunTime.disabled = !runEnabled;
  if(autoStopFrequency) autoStopFrequency.disabled = !stopEnabled;
  if(autoStopDay) autoStopDay.disabled = !stopEnabled || !stopWeekly;
  if(autoStopTime) autoStopTime.disabled = !stopEnabled;
}

function applySystemConfigSchedule(schedule, force = false){
  if(!schedule || typeof schedule !== 'object') return;
  if(systemConfigDirty && !force) return;

  const autoRun = schedule.auto_run || {};
  const autoStop = schedule.auto_stop || {};

  if(autoRunEnabled) autoRunEnabled.checked = !!autoRun.enabled;
  if(autoRunFrequency) autoRunFrequency.value = autoRun.frequency || 'daily';
  if(autoRunDay) autoRunDay.value = autoRun.day_of_week || 'monday';
  if(autoRunTime) autoRunTime.value = autoRun.time || '08:00';

  if(autoStopEnabled) autoStopEnabled.checked = !!autoStop.enabled;
  if(autoStopFrequency) autoStopFrequency.value = autoStop.frequency || 'daily';
  if(autoStopDay) autoStopDay.value = autoStop.day_of_week || 'monday';
  if(autoStopTime) autoStopTime.value = autoStop.time || '18:00';

  updateSystemConfigFieldState();
  systemConfigDirty = false;
}

function collectSystemConfigSchedule(){
  return {
    auto_run: {
      enabled: !!autoRunEnabled?.checked,
      frequency: autoRunFrequency?.value || 'daily',
      day_of_week: autoRunDay?.value || 'monday',
      time: autoRunTime?.value || ''
    },
    auto_stop: {
      enabled: !!autoStopEnabled?.checked,
      frequency: autoStopFrequency?.value || 'daily',
      day_of_week: autoStopDay?.value || 'monday',
      time: autoStopTime?.value || ''
    }
  };
}

function validateSystemConfigSchedule(schedule){
  const validateEntry = (entry, label) => {
    if(!entry.enabled) return null;
    if(!/^\d{2}:\d{2}$/.test(entry.time || '')) return `${label} time must be provided.`;
    if(entry.frequency === 'weekly' && !entry.day_of_week) return `${label} day must be selected for weekly schedules.`;
    return null;
  };

  return validateEntry(schedule.auto_run, 'Automatic run') || validateEntry(schedule.auto_stop, 'Automatic termination');
}

function renderAutomationStatus(payload){
  const status = payload?.status || 'Error';
  const systemState = payload?.system_state || (payload?.is_running ? 'Running' : 'Stopped');
  const tone = getSystemConfigStatusTone(status);

  if(systemConfigStatusText) systemConfigStatusText.textContent = status;
  if(systemConfigStatusBadge){
    systemConfigStatusBadge.textContent = status;
    systemConfigStatusBadge.classList.remove('good', 'warn', 'bad');
    systemConfigStatusBadge.classList.add(tone);
  }
  if(systemConfigSystemState) systemConfigSystemState.textContent = systemState;
  if(systemConfigNextRun) systemConfigNextRun.textContent = formatScheduleTimestamp(payload?.next_run_at);
  if(systemConfigNextStop) systemConfigNextStop.textContent = formatScheduleTimestamp(payload?.next_stop_at);

  const feedbackText = payload?.message || payload?.last_error || 'Automation backend ready.';
  setSystemConfigFeedback(feedbackText, tone === 'bad' ? 'bad' : tone === 'good' ? 'good' : 'warn');

  if(runAutomationBtn){
    runAutomationBtn.disabled = !!payload?.is_running || status === 'Terminating';
  }
  if(terminateAutomationBtn){
    terminateAutomationBtn.disabled = !payload?.is_running || status === 'Terminating';
  }

  if(payload?.schedule){
    applySystemConfigSchedule(payload.schedule);
  }

  renderMonitoringStatus(payload);
}

async function fetchAutomationStatus(options = {}){
  const { announce = false, connectIfRunning = false } = options;
  try {
    const response = await fetch(automationApiUrl('/api/system-config/status'), { cache: 'no-store' });
    if(!response.ok) throw new Error(`status request failed with ${response.status}`);
    const payload = await response.json();
    renderAutomationStatus(payload);

    if(announce && !automationBackendAnnounced){
      const message = payload?.is_running
        ? '[RedFlagged] Automation backend connected - tools.py is running.'
        : '[RedFlagged] Automation backend connected.';
      appendLog(message, payload?.is_running ? 'c-good' : 'c-muted');
      automationBackendAnnounced = true;
    }

    if(connectIfRunning && payload?.is_running && !sse){
      connectSSE();
    }

    return payload;
  } catch (error) {
    renderAutomationStatus({
      status: 'Error',
      message: 'Automation backend not reachable. Start automation/dashboard_server.py to enable controls.',
      is_running: false,
      next_run_at: null,
      next_stop_at: null
    });
    if(announce && !automationBackendAnnounced){
      appendLog('[RedFlagged] Demo mode - no backend server detected.', 'c-muted');
      automationBackendAnnounced = true;
    }
    return null;
  }
}

async function runSystemAutomation(){
  try {
    const response = await fetch(automationApiUrl('/api/system-config/run'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: 'settings-panel' })
    });
    const payload = await response.json();
    renderAutomationStatus(payload);
    if(response.ok && payload?.ok){
      appendLog('[RedFlagged] Starting tools.py from System Configuration...', 'c-accent');
      connectSSE();
      showNotification({
        title: 'Automation Started',
        text: 'tools.py is now running.',
        status: 'Success',
        source: 'System Configuration'
      });
      return;
    }
    showNotification({
      title: 'Run Skipped',
      text: payload?.message || 'tools.py is already running or unavailable.',
      status: 'Warning',
      source: 'System Configuration'
    });
  } catch (error) {
    showNotification({
      title: 'Run Failed',
      text: 'Unable to start tools.py because the automation backend is not reachable.',
      status: 'Error',
      source: 'System Configuration'
    });
  }
}

async function terminateSystemAutomation(){
  try {
    const response = await fetch(automationApiUrl('/api/system-config/terminate'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source: 'settings-panel' })
    });
    const payload = await response.json();
    renderAutomationStatus(payload);
    if(response.ok && payload?.ok){
      appendLog('[RedFlagged] Terminating tools.py from System Configuration...', 'c-warn');
      showNotification({
        title: 'Termination Requested',
        text: 'The running tools.py process is being stopped.',
        status: 'Warning',
        source: 'System Configuration'
      });
      return;
    }
    showNotification({
      title: 'Terminate Skipped',
      text: payload?.message || 'tools.py is not running.',
      status: 'Warning',
      source: 'System Configuration'
    });
  } catch (error) {
    showNotification({
      title: 'Terminate Failed',
      text: 'Unable to stop tools.py because the automation backend is not reachable.',
      status: 'Error',
      source: 'System Configuration'
    });
  }
}

async function saveSystemAutomationSchedule(){
  const schedule = collectSystemConfigSchedule();
  const validationError = validateSystemConfigSchedule(schedule);
  if(validationError){
    setSystemConfigFeedback(validationError, 'bad');
    showNotification({
      title: 'Schedule Invalid',
      text: validationError,
      status: 'Error',
      source: 'System Configuration'
    });
    return;
  }

  try {
    const response = await fetch(automationApiUrl('/api/system-config/schedule'), {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(schedule)
    });
    const payload = await response.json();
    if(!response.ok || !payload?.ok){
      const message = payload?.message || 'Unable to save the schedule configuration.';
      setSystemConfigFeedback(message, 'bad');
      showNotification({
        title: 'Schedule Save Failed',
        text: message,
        status: 'Error',
        source: 'System Configuration'
      });
      return;
    }

    applySystemConfigSchedule(payload.schedule, true);
    renderAutomationStatus(payload);
    setSystemConfigFeedback('Automation schedule saved successfully.', 'good');
    showNotification({
      title: 'Schedule Saved',
      text: 'Automatic run and termination settings were updated.',
      status: 'Success',
      source: 'System Configuration'
    });
  } catch (error) {
    setSystemConfigFeedback('Unable to save the schedule because the backend is not reachable.', 'bad');
    showNotification({
      title: 'Schedule Save Failed',
      text: 'Unable to save the schedule because the automation backend is not reachable.',
      status: 'Error',
      source: 'System Configuration'
    });
  }
}

async function startIncidentResolveAutomation(incidentId, options = {}){
  const normalizedIncidentId = String(incidentId || '').trim();
  if(!normalizedIncidentId) throw new Error('Incident ID is unavailable for resolve automation.');

  const normalizedStatus = String(options.status || 'Resolved').trim() || 'Resolved';
  const normalizedClassification = String(options.classification || '').trim();
  const normalizedComment = String(options.comment || '').trim();

  const response = await fetch(automationApiUrl('/api/incidents/resolve'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      incidentId: normalizedIncidentId,
      status: normalizedStatus,
      classification: normalizedClassification,
      comment: normalizedComment,
      source: 'resolve-modal'
    })
  });
  const payload = await response.json().catch(() => ({}));
  if(!response.ok || !payload?.ok){
    throw new Error(payload?.message || 'Unable to start incident_resolved.py.');
  }

  connectSSE();
  return payload;
}

function initSystemConfigControls(){
  const scheduleInputs = [
    autoRunEnabled,
    autoRunFrequency,
    autoRunDay,
    autoRunTime,
    autoStopEnabled,
    autoStopFrequency,
    autoStopDay,
    autoStopTime
  ].filter(Boolean);

  scheduleInputs.forEach((input) => {
    input.addEventListener('change', () => {
      setSystemConfigDirty(true);
      updateSystemConfigFieldState();
      setSystemConfigFeedback('Unsaved schedule changes.', 'warn');
    });
    input.addEventListener('input', () => {
      setSystemConfigDirty(true);
    });
  });

  if(runAutomationBtn){
    runAutomationBtn.addEventListener('click', () => {
      runSystemAutomation();
    });
  }
  if(terminateAutomationBtn){
    terminateAutomationBtn.addEventListener('click', () => {
      terminateSystemAutomation();
    });
  }
  if(saveAutomationScheduleBtn){
    saveAutomationScheduleBtn.addEventListener('click', () => {
      saveSystemAutomationSchedule();
    });
  }

  updateSystemConfigFieldState();
}

function getEmbeddedArtifactsPayload(){
  const cachedPayload = window.__REDFLAGGED_ARTIFACTS__;
  if(!cachedPayload || typeof cachedPayload !== 'object'){
    return null;
  }

  return {
    updatedAt: String(cachedPayload.updated_at || ''),
    files: Array.isArray(cachedPayload.files) ? cachedPayload.files : []
  };
}

function hasArtifactFiles(payload){
  return !!(payload && Array.isArray(payload.files) && payload.files.length > 0);
}

function hasArtifactPayload(payload){
  return !!(payload && Array.isArray(payload.files));
}

function avpKey(row){
  return String(row?.rowId || row?.relativePath || `${row?.incidentId || ''}::${row?.artifact || ''}`);
}

function getArtifactAlertStatus(row){
  const dbInfoMatch = findDbInfoIncidentRowForArtifact(row);
  return String(dbInfoMatch?.status || 'No db_info match').trim() || 'No db_info match';
}

function getArtifactStatusKey(status){
  const normalizedStatus = String(status || '').trim().toLowerCase();
  if(/resolved|closed/.test(normalizedStatus)) return 'resolved';
  if(/dismissed/.test(normalizedStatus)) return 'dismissed';
  if(/active|open|new|pending|progress|investigat/.test(normalizedStatus)) return 'active';
  return 'other';
}

function avpGetStatus(row){
  return getArtifactAlertStatus(row);
}

function canDeleteArtifact(row){
  return getArtifactStatusKey(getArtifactAlertStatus(row)) === 'resolved';
}

function getFilteredArtifactRows(){
  const artifactSearch = artifactsIncidentFilter ? artifactsIncidentFilter.value.trim().toLowerCase() : '';
  const searchField = avpSearchField ? avpSearchField.value : 'all';
  const statusFilter = document.getElementById('avp-status-filter');
  const statusVal = statusFilter ? statusFilter.value : 'all';

  let filteredArtifactRows = Array.isArray(artifactRows) ? artifactRows.slice() : [];

  if(artifactSearch){
    filteredArtifactRows = filteredArtifactRows.filter((row) => {
      const searchTargets = searchField === 'incident'
        ? [row.incidentId, row.alertName]
        : [row.incidentId, row.artifact, row.alertName, row.relativePath];

      return searchTargets.some((value) => String(value || '').toLowerCase().includes(artifactSearch));
    });
  }
  if(statusVal !== 'all'){
    filteredArtifactRows = filteredArtifactRows.filter((row) => getArtifactStatusKey(avpGetStatus(row)) === statusVal);
  }
  return filteredArtifactRows;
}

function updateArtifactSearchPlaceholder(){
  if(!artifactsIncidentFilter) return;
  const searchField = avpSearchField ? avpSearchField.value : 'all';

  if(searchField === 'incident'){
    artifactsIncidentFilter.setAttribute('aria-label', 'Search by incident name or incident ID');
    artifactsIncidentFilter.placeholder = 'Search incident name or incident ID…';
    return;
  }

  artifactsIncidentFilter.setAttribute('aria-label', 'Search by alert name, incident ID, or artifact name');
  artifactsIncidentFilter.placeholder = 'Search alert name, incident ID, or artifact…';
}

function applyArtifactAlertColumnState(){
  const columnWidth = isArtifactAlertColumnExpanded ? '360px' : '140px';

  if(avpAlertNameCol){
    avpAlertNameCol.style.width = columnWidth;
  }

  if(avpArtifactsTable){
    avpArtifactsTable.classList.toggle('is-alert-column-expanded', isArtifactAlertColumnExpanded);
  }

  if(avpAlertColumnToggle){
    avpAlertColumnToggle.setAttribute('aria-expanded', isArtifactAlertColumnExpanded ? 'true' : 'false');
    avpAlertColumnToggle.title = isArtifactAlertColumnExpanded ? 'Collapse Alert Name column' : 'Expand Alert Name column';
  }

  if(avpAlertColumnToggleHint){
    avpAlertColumnToggleHint.textContent = isArtifactAlertColumnExpanded ? 'Collapse column' : 'Expand column';
  }
}

function getArtifactFilterSnapshot(){
  const statusFilter = document.getElementById('avp-status-filter');
  const searchField = avpSearchField ? avpSearchField.value : 'all';
  const searchValue = artifactsIncidentFilter ? artifactsIncidentFilter.value.trim() : '';
  const statusValue = statusFilter ? statusFilter.value : 'all';
  return `${searchField}::${statusValue}::${searchValue}`;
}

function syncArtifactFilterState(force = false){
  if(!artifactsTabContent || !artifactsTabContent.classList.contains('active')) return;
  const snapshot = getArtifactFilterSnapshot();
  if(!force && snapshot === lastArtifactFilterSnapshot) return;
  lastArtifactFilterSnapshot = snapshot;
  renderArtifactsByIncident();
}

function avpStatusBadge(status){
  const statusKey = getArtifactStatusKey(status);
  const map = {
    resolved: { cls: 'good', label: String(status || 'Resolved') },
    active: { cls: 'warn', label: String(status || 'Active') },
    dismissed: { cls: 'warn', label: String(status || 'Dismissed') },
    other: { cls: 'bad', label: String(status || 'Other') }
  };
  const cfg = map[statusKey] || map.other;
  return `<span class="badge ${cfg.cls}">${escapeHtml(cfg.label)}</span>`;
}

function avpFileIcon(artifact){
  const ext = (artifact || '').split('.').pop().toLowerCase();
  const icons = { json: '{ }', csv: '⊟', xlsx: '⊞', zip: '⊕', txt: '≡', eml: '✉', log: '▤' };
  return `<span class="avp-file-icon" aria-hidden="true">${icons[ext] || '⊡'}</span>`;
}

function renderAvpSummaryPills(allRows){
  const pills = document.getElementById('avp-summary-pills');
  if(!pills) return;
  const counts = { resolved: 0, active: 0, dismissed: 0, other: 0 };
  allRows.forEach((row) => {
    const statusKey = getArtifactStatusKey(avpGetStatus(row));
    counts[statusKey] = (counts[statusKey] || 0) + 1;
  });
  const total = allRows.length;
  const labels = { resolved: 'Resolved', active: 'Active', dismissed: 'Dismissed', other: 'Other' };
  const clsMap = { resolved: 'good', active: 'warn', dismissed: 'warn', other: 'bad' };
  pills.innerHTML = `<span class="avp-summary-total">${total} artifact${total !== 1 ? 's' : ''}</span>` +
    Object.entries(counts).filter(([,v]) => v > 0).map(([k, v]) =>
      `<span class="badge ${clsMap[k]} avp-pill">${v} ${labels[k]}</span>`
    ).join('');
}

function artifactFileUrl(relativePath){
  const normalizedPath = String(relativePath || '')
    .split('/')
    .filter(Boolean)
    .map((segment) => encodeURIComponent(segment))
    .join('/');
  return normalizedPath ? `artifacts/${normalizedPath}` : '#';
}

async function ensureArtifactsDirectoryHandle(){
  if(artifactsDirectoryHandle) return artifactsDirectoryHandle;
  if(typeof window.showDirectoryPicker !== 'function'){
    throw new Error('This browser does not support deleting local artifacts from the dashboard.');
  }

  artifactsDirectoryHandle = await window.showDirectoryPicker({ mode: 'readwrite', id: 'redflagged-artifacts-root' });
  return artifactsDirectoryHandle;
}

async function getExistingDirectoryHandle(parentHandle, directoryName){
  try {
    return await parentHandle.getDirectoryHandle(directoryName);
  } catch (_error) {
    return null;
  }
}

async function fileExistsInDirectory(baseHandle, pathParts){
  try {
    let directoryHandle = baseHandle;
    for(let index = 0; index < pathParts.length - 1; index += 1){
      directoryHandle = await directoryHandle.getDirectoryHandle(pathParts[index]);
    }
    await directoryHandle.getFileHandle(pathParts[pathParts.length - 1]);
    return true;
  } catch (_error) {
    return false;
  }
}

async function resolveArtifactsDeleteContext(rootHandle, relativePath){
  const pathParts = relativePath.split('/').filter(Boolean);
  if(!pathParts.length){
    throw new Error('Artifact path is invalid.');
  }

  if(await fileExistsInDirectory(rootHandle, pathParts)){
    return {
      artifactsHandle: rootHandle,
      dashboardHandle: null,
      pathParts
    };
  }

  const dashboardArtifactsHandle = await getExistingDirectoryHandle(rootHandle, 'artifacts');
  if(dashboardArtifactsHandle && await fileExistsInDirectory(dashboardArtifactsHandle, pathParts)){
    return {
      artifactsHandle: dashboardArtifactsHandle,
      dashboardHandle: rootHandle,
      pathParts
    };
  }

  throw new Error('Choose the dashboard folder or the artifacts folder so the selected file can be deleted.');
}

function formatArtifactInventoryTimestamp(date = new Date()){
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

function buildArtifactInventoryPayload(files){
  return {
    updated_at: formatArtifactInventoryTimestamp(),
    files: Array.isArray(files) ? files.slice() : []
  };
}

async function writeTextToFile(fileHandle, content){
  const writable = await fileHandle.createWritable();
  await writable.write(content);
  await writable.close();
}

async function persistArtifactInventoryPayload(dashboardHandle, payload){
  if(!dashboardHandle){
    return false;
  }

  const automationHandle = await dashboardHandle.getDirectoryHandle('automation', { create: true });
  const jsHandle = await dashboardHandle.getDirectoryHandle('js', { create: true });
  const inventoryFileHandle = await automationHandle.getFileHandle('artifacts_inventory.json', { create: true });
  const artifactJsFileHandle = await jsHandle.getFileHandle('artifactData.js', { create: true });

  await writeTextToFile(inventoryFileHandle, `${JSON.stringify(payload, null, 2)}\n`);
  await writeTextToFile(artifactJsFileHandle, `window.__REDFLAGGED_ARTIFACTS__ = ${JSON.stringify(payload, null, 2)};\n`);
  return true;
}

async function deleteArtifactFromDisk(row){
  const relativePath = String(row?.relativePath || '').trim();
  if(!relativePath){
    throw new Error('Artifact path is unavailable for deletion.');
  }

  const response = await fetch(automationApiUrl('/api/artifacts/delete'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ relative_path: relativePath })
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch (_error) {
    payload = null;
  }

  if(!response.ok || !payload?.ok){
    throw new Error(payload?.message || 'Unable to delete the selected artifact.');
  }

  return payload;
}

function renderArtifactsByIncident(){
  if(!artifactsByIncidentBody) return;

  const allArtifactRows = Array.isArray(artifactRows) ? artifactRows : [];
  renderAvpSummaryPills(allArtifactRows);

  const filteredRows = getFilteredArtifactRows();
  if(filteredRows.length === 0){
    artifactsByIncidentBody.innerHTML = '<tr><td colspan="7" class="incident-empty">No artifacts match the current filters.</td></tr>';
    return;
  }

  artifactsByIncidentBody.innerHTML = filteredRows.map(row => {
    const status = avpGetStatus(row);
    const key = avpKey(row);
    const safeKey = escapeHtml(key);
    const deleteAllowed = canDeleteArtifact(row);
    const deleteButtonTitle = deleteAllowed
      ? `Delete ${row.artifact}`
      : `Delete is only available for resolved alerts. Current status: ${status}`;
    const openUrl = artifactFileUrl(row.relativePath);
    const actionCell = `
      <div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end;">
        <a class="avp-btn" href="${openUrl}" target="_blank" rel="noopener noreferrer">Open</a>
        <button type="button" class="avp-btn avp-btn-delete${deleteAllowed ? '' : ' is-locked'}" data-avp-key="${safeKey}" data-avp-delete-allowed="${deleteAllowed ? 'true' : 'false'}" aria-disabled="${deleteAllowed ? 'false' : 'true'}" title="${escapeHtml(deleteButtonTitle)}">Delete</button>
      </div>`;

    return `<tr class="avp-row avp-row-${getArtifactStatusKey(status)}" data-avp-key="${safeKey}">
      <td class="avp-td-icon">${avpFileIcon(row.artifact)}</td>
      <td class="avp-td-name">
        <div class="avp-artifact-name">${escapeHtml(row.artifact || 'N/A')}</div>
        <div class="avp-artifact-meta mono">${escapeHtml(row.relativePath || row.folderPath || '—')}</div>
      </td>
      <td class="avp-td-alert">
        <div class="avp-alert-name" title="${escapeHtml(row.alertName || 'Unknown')}">${escapeHtml(row.alertName || 'Unknown')}</div>
      </td>
      <td class="mono avp-td-id">${escapeHtml(row.incidentId || 'N/A')}</td>
      <td class="mono avp-td-date">${escapeHtml(row.date || '—')}</td>
      <td class="avp-td-status">${avpStatusBadge(status)}</td>
      <td class="avp-td-actions">${actionCell}</td>
    </tr>`;
  }).join('');
}

function buildArtifactRows(files){
  const sourceFiles = Array.isArray(files) ? files : [];

  function normalizeDate(value){
    if(!value) return 'N/A';
    const d = new Date(value);
    if(Number.isNaN(d.getTime())) return String(value);
    return d.toISOString().slice(0, 10);
  }

  if(sourceFiles.length === 0){
    return [];
  }

  return sourceFiles.map((file, index) => ({
    incidentId: String(file.incident_id || 'N/A'),
    ticket: String(file.ticket || '—'),
    date: normalizeDate(file.modified_iso),
    status: getArtifactAlertStatus(file),
    alertStatus: getArtifactAlertStatus(file),
    alertName: String(file.alert_name || file.folder_name || 'Unknown Alert'),
    severity: 'Medium',
    assignedTo: DEFAULT_ASSIGNEE_NAMES[index % DEFAULT_ASSIGNEE_NAMES.length],
    actionUrl: artifactFileUrl(file.relative_path),
    source: 'Artifacts Folder',
    detection: String(file.alert_name || file.folder_name || file.name || `Artifact ${index + 1}`),
    actor: '',
    location: String(file.folder_path || 'artifacts'),
    policy: 'Artifact Inventory',
    recommendation: canDeleteArtifact(file) ? 'Eligible for deletion' : 'Retain until resolved',
    artifact: String(file.name || `artifact_${index + 1}.txt`),
    odinSummary: `Artifact inventory entry for ${String(file.alert_name || file.folder_name || file.name || `Artifact ${index + 1}`)}.`,
    relativePath: String(file.relative_path || ''),
    folderPath: String(file.folder_path || ''),
    size: Number(file.size || 0),
    modifiedIso: String(file.modified_iso || ''),
    deletable: typeof file.deletable === 'boolean' ? file.deletable : undefined,
    rowId: String(file.relative_path || `${file.name || 'artifact'}::${index}`)
  }));
}

function mergeIncidentRowState(previousRows, nextRows){
  const history = new Map();
  (Array.isArray(previousRows) ? previousRows : []).forEach((row) => {
    const key = row.rowId || `${row.incidentId || ''}::${row.ticket || ''}::${row.artifact || ''}`;
    history.set(key, row);
  });

  return (Array.isArray(nextRows) ? nextRows : []).map((row) => {
    const key = row.rowId || `${row.incidentId || ''}::${row.ticket || ''}::${row.artifact || ''}`;
    const prev = history.get(key);
    if(!prev) return row;

    return {
      ...row,
      classification: row.classification || prev.classification || '',
      severity: row.severity || prev.severity || 'Medium',
      resolutionNote: prev.resolutionNote || row.resolutionNote || '',
      status: /resolved|closed|contained/i.test(String(prev.status || '')) ? prev.status : row.status
    };
  });
}

function renderIncidentsTable(rows){
  if(!incidentsTableBody || !incidentsPagination) return;

  lastRenderedRows = rows;
  incidentRowRegistry = {};
  const sourceRowCount = Array.isArray(rows) ? rows.length : 0;
  const activeDbInfoHeaders = Array.isArray(dbInfoColumnHeaders) ? dbInfoColumnHeaders : [];
  const visibleDbInfoHeaders = getVisibleDbInfoHeaders(activeDbInfoHeaders);
  const useDbInfoColumns = visibleDbInfoHeaders.length > 0;

  applyIncidentTableColumns(useDbInfoColumns ? visibleDbInfoHeaders : []);

  function normalizeSeverity(s){
    const n = String(s || '').trim().toLowerCase();
    if(n === 'high' || n === 'critical') return 'High';
    if(n === 'low' || n === 'info' || n === 'informational') return 'Low';
    return 'Medium';
  }

  function getSeverityRank(s){
    const n = normalizeSeverity(s);
    return n === 'High' ? 2 : n === 'Medium' ? 1 : 0;
  }

  function getSeverityBadgeClass(s){
    const n = normalizeSeverity(s);
    return n === 'High' ? 'bad' : n === 'Medium' ? 'warn' : 'good';
  }

  function getStatusClass(s){
    const n = (s || '').toLowerCase();
    if(/resolved|closed|complete/.test(n)) return 'good';
    if(/progress|open|investigat/.test(n)) return 'warn';
    return 'warn';
  }

  function formatAssets(assets){
    const count = Array.isArray(assets) ? assets.length : (typeof assets === 'number' ? assets : 1);
    const label = Array.isArray(assets) && assets.length > 0
      ? `<span class="idf-asset-tooltip">${escapeHtml(assets.slice(0,3).join(', '))}${assets.length > 3 ? ` +${assets.length - 3} more` : ''}</span>`
      : '';
    return `<span class="idf-asset-pill">${count} asset${count !== 1 ? 's' : ''}${label}</span>`;
  }

  function renderDbInfoCell(header, rawValue){
    const normalizedHeader = String(header || '').trim().toLowerCase();
    const value = rawValue == null || rawValue === '' ? '—' : String(rawValue);
    const cellClasses = [];

    if(/incident id/.test(normalizedHeader)) {
      cellClasses.push('mono', 'idf-td-id');
    } else if(/time|date/.test(normalizedHeader)) {
      cellClasses.push('mono', 'idf-td-activity');
    } else if(/alert name/.test(normalizedHeader)) {
      cellClasses.push('idf-td-name');
    } else if(/user/.test(normalizedHeader)) {
      cellClasses.push('idf-td-assignee');
    }

    if(/severity/.test(normalizedHeader)) {
      return `<td><span class="badge ${getSeverityBadgeClass(value)}">${escapeHtml(normalizeSeverity(value))}</span></td>`;
    }

    if(/status/.test(normalizedHeader)) {
      return `<td><span class="badge ${getStatusClass(value)}">${escapeHtml(value)}</span></td>`;
    }

    const classAttribute = cellClasses.length ? ` class="${cellClasses.join(' ')}"` : '';
    return `<td${classAttribute}>${escapeHtml(value)}</td>`;
  }

  function renderRow(row, rowKey, isChild, isVisible){
    const hasChildren = !isChild && Array.isArray(row.relatedIncidents) && row.relatedIncidents.length > 0;
    const isExpanded = hasChildren && incidentExpandedIds.has(row.incidentId);
    incidentRowRegistry[rowKey] = row;

    const chevronCell = hasChildren
      ? `<td class="idf-td-chevron">
           <button type="button" class="idf-chevron-btn${isExpanded ? ' expanded' : ''}" data-expand-id="${escapeHtml(row.incidentId)}" aria-label="${isExpanded ? 'Collapse' : 'Expand'} related incidents" aria-expanded="${isExpanded}">
             <svg class="idf-chevron-icon" width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true"><path d="${isExpanded ? 'M2 4l4 4 4-4' : 'M4 2l4 4-4 4'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
           </button>
         </td>`
      : `<td class="idf-td-chevron">${isChild ? '<span class="idf-child-connector" aria-hidden="true"></span>' : ''}</td>`;

    const rowClass = [
      'incident-row',
      isChild ? 'idf-child-row' : 'idf-parent-row',
      hasChildren ? 'idf-has-children' : '',
      isExpanded ? 'idf-expanded' : ''
    ].filter(Boolean).join(' ');

    const childDataAttr = isChild ? ` data-child-row="true"` : '';
    const visibilityStyle = isVisible ? '' : ' style="display:none"';

    const nameCell = isChild
      ? `<td class="idf-td-name"><span class="idf-child-prefix" aria-hidden="true">↳</span><span class="idf-incident-name idf-child-name">${escapeHtml(row.name || row.detection || 'Unnamed')}</span></td>`
      : `<td class="idf-td-name">
           ${hasChildren ? `<span class="idf-thread-icon" title="${row.relatedIncidents.length} related incident${row.relatedIncidents.length !== 1 ? 's' : ''}" aria-label="${row.relatedIncidents.length} related incidents">
             <svg width="13" height="13" viewBox="0 0 13 13" fill="none" aria-hidden="true"><circle cx="6.5" cy="6.5" r="5.5" stroke="currentColor" stroke-width="1.4"/><path d="M4 6.5h5M7.5 4.5l2 2-2 2" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>
           </span>` : ''}
           <span class="idf-incident-name">${escapeHtml(row.name || row.detection || 'Unnamed')}</span>
         </td>`;

    return `<tr class="${rowClass}" data-row-key="${escapeHtml(rowKey)}"${childDataAttr} tabindex="0" role="row"${visibilityStyle}>
      ${chevronCell}
      ${nameCell}
      <td class="mono idf-td-id">${escapeHtml(row.incidentId)}</td>
      <td><span class="badge ${getSeverityBadgeClass(row.severity)}">${escapeHtml(normalizeSeverity(row.severity))}</span></td>
      <td class="idf-td-assets">${formatAssets(row.impactedAssets)}</td>
      <td class="mono idf-td-activity">${escapeHtml(row.firstActivity || row.date || '—')}</td>
      <td class="mono idf-td-activity">${escapeHtml(row.lastActivity || row.date || '—')}</td>
      <td><span class="badge ${getStatusClass(row.status)}">${escapeHtml(row.status || 'Unknown')}</span></td>
      <td class="idf-td-assignee">${escapeHtml(row.assignedTo || '—')}</td>
    </tr>`;
  }

  // Apply active filters
  function applyFilters(sourceRows){
    let filtered = Array.isArray(sourceRows) ? sourceRows : [];

    if(idfFilterPreset === 'open') filtered = filtered.filter(r => /open|pending|progress/i.test(r.status || ''));
    else if(idfFilterPreset === 'high') filtered = filtered.filter(r => /high|critical/i.test(r.severity || ''));
    else if(idfFilterPreset === 'assigned') filtered = filtered.filter(r => r.assignedTo && r.assignedTo !== '—');

    idfActiveFilters.forEach(f => {
      if(f.type === 'severity') filtered = filtered.filter(r => (r.severity || '').toLowerCase() === f.value.toLowerCase());
      else if(f.type === 'status') filtered = filtered.filter(r => (r.status || '').toLowerCase() === f.value.toLowerCase());
      else if(f.type === 'classification') filtered = filtered.filter(r => getIncidentClassificationBucket(r.classification).toLowerCase() === f.value.toLowerCase());
    });

    return filtered;
  }

  const filteredRows = applyFilters(rows);
  renderIdfFilterChips();

  const allRows = filteredRows.slice().sort((a, b) => {
    const sd = getSeverityRank(b?.severity) - getSeverityRank(a?.severity);
    if(sd !== 0) return sd;
    const dA = Date.parse(String(a?.date || ''));
    const dB = Date.parse(String(b?.date || ''));
    return (Number.isNaN(dB) ? 0 : dB) - (Number.isNaN(dA) ? 0 : dA);
  });

  const totalPages = Math.max(1, Math.ceil(allRows.length / INCIDENTS_PAGE_SIZE));
  incidentsPage = Math.min(Math.max(incidentsPage, 1), totalPages);

  if(allRows.length === 0){
    const emptyMessage = sourceRowCount === 0
      ? 'No incidents loaded from automation/db_info.txt yet.'
      : 'No incident records match the current filters.';
    const emptyColspan = useDbInfoColumns ? Math.max(visibleDbInfoHeaders.length, 1) : 9;
    incidentsTableBody.innerHTML = `<tr><td colspan="${emptyColspan}" class="incident-empty">${escapeHtml(emptyMessage)}</td></tr>`;
    incidentsPagination.innerHTML = '';
    return;
  }

  const start = (incidentsPage - 1) * INCIDENTS_PAGE_SIZE;
  const pageRows = allRows.slice(start, start + INCIDENTS_PAGE_SIZE);

  const htmlParts = [];
  if(useDbInfoColumns){
    pageRows.forEach((row, pi) => {
      const rowKey = `parent::${row.incidentId || row.rowId || 'db-info-row'}::${start + pi}`;
      incidentRowRegistry[rowKey] = row;
      const cellsHtml = visibleDbInfoHeaders.map((header) => {
        const value = row?.dbInfoRecord && typeof row.dbInfoRecord === 'object' ? row.dbInfoRecord[header] : '';
        return renderDbInfoCell(header, value == null ? '' : String(value));
      });
      htmlParts.push(`<tr class="incident-row idf-parent-row" data-row-key="${escapeHtml(rowKey)}" tabindex="0" role="row">${cellsHtml.join('')}</tr>`);
    });
  } else {
    pageRows.forEach((row, pi) => {
      const rowKey = `parent::${row.incidentId}::${start + pi}`;
      htmlParts.push(renderRow(row, rowKey, false, true));
      if(Array.isArray(row.relatedIncidents)){
        const isExpanded = incidentExpandedIds.has(row.incidentId);
        row.relatedIncidents.forEach((child, ci) => {
          const childKey = `child::${child.incidentId}::${start + pi}::${ci}`;
          htmlParts.push(renderRow(child, childKey, true, isExpanded));
        });
      }
    });
  }

  incidentsTableBody.innerHTML = htmlParts.join('');

  // Chevron click → toggle expand
  incidentsTableBody.querySelectorAll('.idf-chevron-btn').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const pid = btn.getAttribute('data-expand-id');
      if(incidentExpandedIds.has(pid)){
        incidentExpandedIds.delete(pid);
      } else {
        incidentExpandedIds.add(pid);
      }
      renderIncidentsTable(lastRenderedRows);
    });
  });

  // Row click → open modal
  incidentsTableBody.querySelectorAll('.incident-row').forEach(rowEl => {
    rowEl.addEventListener('click', e => {
      if(e.target.closest('.idf-chevron-btn')) return;
      const key = rowEl.getAttribute('data-row-key');
      const row = incidentRowRegistry[key];
      if(row) selectIncident(row);
    });
    rowEl.addEventListener('keydown', e => {
      if(e.key === 'Enter' || e.key === ' '){ e.preventDefault(); rowEl.click(); }
    });
  });

  if(totalPages <= 1){
    incidentsPagination.innerHTML = '';
    return;
  }

  incidentsPagination.innerHTML = `
    <button type="button" class="incidents-page-btn" id="incidents-prev" ${incidentsPage === 1 ? 'disabled' : ''}>Previous</button>
    <span class="incidents-page-info">Page ${incidentsPage} of ${totalPages}</span>
    <button type="button" class="incidents-page-btn" id="incidents-next" ${incidentsPage === totalPages ? 'disabled' : ''}>Next</button>
  `;

  const prevBtn = document.getElementById('incidents-prev');
  const nextBtn = document.getElementById('incidents-next');
  if(prevBtn){
    prevBtn.addEventListener('click', () => {
      if(incidentsPage > 1){ incidentsPage -= 1; renderIncidentsTable(lastRenderedRows); }
    });
  }
  if(nextBtn){
    nextBtn.addEventListener('click', () => {
      if(incidentsPage < totalPages){ incidentsPage += 1; renderIncidentsTable(lastRenderedRows); }
    });
  }
}

function renderIdfFilterChips(){
  const chipsRow = document.getElementById('idf-chips-row');
  if(!chipsRow) return;

  const presetLabels = { all: 'All incidents', open: 'Open', high: 'High severity', assigned: 'Assigned to me' };
  const presetLabel = presetLabels[idfFilterPreset] || 'All incidents';

  const presetChip = `<span class="idf-chip idf-chip-preset" data-preset="${escapeHtml(idfFilterPreset)}">${escapeHtml(presetLabel)}</span>`;

  const dynamicChips = idfActiveFilters.map((f, i) =>
    `<span class="idf-chip idf-chip-filter" data-filter-index="${i}">${escapeHtml(f.label)}<button type="button" class="idf-chip-remove" aria-label="Remove filter ${escapeHtml(f.label)}" data-filter-index="${i}">×</button></span>`
  ).join('');

  chipsRow.innerHTML = presetChip + dynamicChips;

  chipsRow.querySelectorAll('.idf-chip-remove').forEach(btn => {
    btn.addEventListener('click', e => {
      e.stopPropagation();
      const idx = Number(btn.getAttribute('data-filter-index'));
      idfActiveFilters.splice(idx, 1);
      renderIncidentsTable(lastRenderedRows);
    });
  });
}

function initIncidentFilterBar(){
  const filtersetSelect = document.getElementById('idf-filterset-select');
  const saveBtn = document.getElementById('idf-save-btn');
  const addFilterBtn = document.getElementById('idf-add-filter-btn');
  const resetBtn = document.getElementById('idf-reset-btn');
  const addFilterPopover = document.getElementById('idf-add-filter-popover');

  if(filtersetSelect){
    filtersetSelect.value = idfFilterPreset;
    filtersetSelect.addEventListener('change', () => {
      idfFilterPreset = filtersetSelect.value;
      idfActiveFilters = [];
      incidentsPage = 1;
      renderIncidentsTable(lastRenderedRows);
    });
  }

  if(saveBtn){
    saveBtn.addEventListener('click', () => {
      showNotification({ title: 'Filter Saved', text: 'Current filter set has been saved.', status: 'Success', source: 'Incidents' });
    });
  }

  if(resetBtn){
    resetBtn.addEventListener('click', () => {
      idfFilterPreset = 'all';
      idfActiveFilters = [];
      incidentsPage = 1;
      if(filtersetSelect) filtersetSelect.value = 'all';
      renderIncidentsTable(lastRenderedRows);
    });
  }

  if(addFilterBtn && addFilterPopover){
    addFilterBtn.addEventListener('click', e => {
      e.stopPropagation();
      const isOpen = addFilterPopover.classList.toggle('open');
      addFilterBtn.setAttribute('aria-expanded', isOpen);
    });

    addFilterPopover.addEventListener('click', e => {
      const btn = e.target.closest('[data-filter-type]');
      if(!btn) return;
      const type = btn.getAttribute('data-filter-type');
      const value = btn.getAttribute('data-filter-value');
      const label = btn.getAttribute('data-filter-label') || `${type}: ${value}`;

      const alreadyExists = idfActiveFilters.some(f => f.type === type && f.value === value);
      if(!alreadyExists){
        idfActiveFilters.push({ type, value, label });
        incidentsPage = 1;
        renderIncidentsTable(lastRenderedRows);
      }
      addFilterPopover.classList.remove('open');
      addFilterBtn.setAttribute('aria-expanded', 'false');
    });

    document.addEventListener('click', e => {
      if(!addFilterPopover.contains(e.target) && e.target !== addFilterBtn){
        addFilterPopover.classList.remove('open');
        addFilterBtn.setAttribute('aria-expanded', 'false');
      }
    });
  }
}

function renderArtifacts(files){
  if(!artifactsList) return;

  const allFiles = Array.isArray(files) ? files : [];
  const totalPages = Math.max(1, Math.ceil(allFiles.length / ARTIFACTS_PAGE_SIZE));
  artifactsPage = Math.min(Math.max(artifactsPage, 1), totalPages);

  function renderArtifactsPagination(totalItems){
    if(!artifactsPagination) return;
    if(totalItems <= ARTIFACTS_PAGE_SIZE){
      artifactsPagination.innerHTML = '';
      return;
    }

    artifactsPagination.innerHTML = `
      <button type="button" class="artifacts-page-btn" id="artifacts-prev" ${artifactsPage === 1 ? 'disabled' : ''}>Previous</button>
      <span class="artifacts-page-info">Page ${artifactsPage} of ${totalPages}</span>
      <button type="button" class="artifacts-page-btn" id="artifacts-next" ${artifactsPage === totalPages ? 'disabled' : ''}>Next</button>
    `;

    const prevBtn = document.getElementById('artifacts-prev');
    const nextBtn = document.getElementById('artifacts-next');

    if(prevBtn){
      prevBtn.addEventListener('click', () => {
        if(artifactsPage > 1){
          artifactsPage -= 1;
          renderArtifacts(allFiles);
        }
      });
    }
    if(nextBtn){
      nextBtn.addEventListener('click', () => {
        if(artifactsPage < totalPages){
          artifactsPage += 1;
          renderArtifacts(allFiles);
        }
      });
    }
  }

  if(!allFiles || allFiles.length === 0){
    artifactsList.innerHTML = `
      <li>
        <div>
          <span>No files found in artifacts folder.</span><br/>
          <small class="mono" style="color: var(--muted);">artifacts/</small>
        </div>
        <span class="badge warn">Empty</span>
      </li>`;
    renderArtifactsPagination(0);
    return;
  }

  const start = (artifactsPage - 1) * ARTIFACTS_PAGE_SIZE;
  const pageFiles = allFiles.slice(start, start + ARTIFACTS_PAGE_SIZE);

  artifactsList.innerHTML = pageFiles.map((file, idx) => {
    const safeName = escapeHtml(file.name || 'Unnamed artifact');
    const viewUrl = artifactFileUrl(file.relative_path || file.name || '');
    const status = escapeHtml(getArtifactAlertStatus(file));
    return `
      <li>
        <div>
          <span>Artifact ${start + idx + 1} - ${safeName}</span><br/>
          <small class="mono" style="color: var(--muted);">${escapeHtml(file.relative_path || 'artifacts/')} | ${formatSize(file.size)} | ${escapeHtml(file.modified_iso || 'N/A')} | ${status}</small>
        </div>
        <a class="badge good" href="${viewUrl}" target="_blank" rel="noopener noreferrer">View</a>
      </li>`;
  }).join('');

  renderArtifactsPagination(allFiles.length);
}

function loadArtifacts(){
  const embeddedPayload = getEmbeddedArtifactsPayload();
  const isLocalFileView = window.location.protocol === 'file:';

  const applyArtifactPayload = (payload) => {
    const files = Array.isArray(payload?.files) ? payload.files : [];
    artifactInventoryFiles = files.slice();
    renderArtifacts(files);
    artifactRows = mergeIncidentRowState(artifactRows, buildArtifactRows(files));
    renderArtifactsByIncident();
    return artifactRows;
  };

  if(isLocalFileView && hasArtifactPayload(embeddedPayload)){
    return Promise.resolve(applyArtifactPayload(embeddedPayload));
  }

  return fetch(ARTIFACTS_DATA_PATH, { cache: 'no-store' })
    .then((response) => {
      if(!response.ok){
        throw new Error(`artifacts inventory request failed with status ${response.status}`);
      }
      return response.json();
    })
    .then((payload) => {
      return applyArtifactPayload(payload);
    })
    .catch(() => {
      if(hasArtifactPayload(embeddedPayload)){
        return applyArtifactPayload(embeddedPayload);
      }

      artifactInventoryFiles = [];
      renderArtifacts([]);
      artifactRows = mergeIncidentRowState(artifactRows, buildArtifactRows([]));
      renderArtifactsByIncident();
      if(!artifactsList) return artifactRows;

      artifactsList.innerHTML = `
        <li>
          <div>
            <span>Unable to load artifact inventory.</span><br/>
            <small class="mono" style="color: var(--muted);">automation/artifacts_inventory.json</small>
          </div>
          <span class="badge bad">Error</span>
        </li>`;
      if(artifactsPagination) artifactsPagination.innerHTML = '';
      return artifactRows;
    });
}

function loadDbInfo(){
  const embeddedPayload = getEmbeddedDbInfoPayload();
  const isLocalFileView = window.location.protocol === 'file:';

  if(isLocalFileView && hasDbInfoRows(embeddedPayload)){
    dbInfoColumnHeaders = resolveDbInfoHeaders(embeddedPayload);
    dbInfoIncidentRows = buildIncidentRowsFromDbInfo(embeddedPayload);
    syncDashboardIncidentRows();
    renderArtifacts(artifactInventoryFiles);
    renderArtifactsByIncident();
    return Promise.resolve(dbInfoIncidentRows);
  }

  return fetch(DB_INFO_PATH, { cache: 'no-store' })
    .then((response) => {
      if(!response.ok){
        throw new Error(`db_info request failed with status ${response.status}`);
      }
      return response.text();
    })
    .then((rawText) => {
      const payload = parseDbInfoPayload(rawText);
      dbInfoColumnHeaders = resolveDbInfoHeaders(payload);
      dbInfoIncidentRows = buildIncidentRowsFromDbInfo(payload);
      syncDashboardIncidentRows();
      renderArtifacts(artifactInventoryFiles);
      renderArtifactsByIncident();
      return dbInfoIncidentRows;
    })
    .catch(() => {
      if(hasDbInfoRows(embeddedPayload)){
        dbInfoColumnHeaders = resolveDbInfoHeaders(embeddedPayload);
        dbInfoIncidentRows = buildIncidentRowsFromDbInfo(embeddedPayload);
        syncDashboardIncidentRows();
        renderArtifacts(artifactInventoryFiles);
        renderArtifactsByIncident();
        return dbInfoIncidentRows;
      }

      dbInfoColumnHeaders = [];
      dbInfoIncidentRows = [];
      syncDashboardIncidentRows();
      renderArtifacts(artifactInventoryFiles);
      renderArtifactsByIncident();
      return dbInfoIncidentRows;
    });
}

function updateStatusBadge(el, statusText){
  if(!el) return;
  el.textContent = statusText || 'In Progress';
  el.classList.remove('good', 'warn', 'bad');
  const t = (statusText || '').toLowerCase();
  if(/closed|resolved|complete|completed|success/.test(t)) el.classList.add('good');
  else if(/new|pending|progress|investigat|open/.test(t)) el.classList.add('warn');
  else if(/fail|error|blocked|critical/.test(t)) el.classList.add('bad');
  else el.classList.add('warn');
}

function loadCaseDetails(){
  fetch(automationApiUrl('/report-data'))
    .then(r => r.json())
    .then(d => {
      if(!d || !d.ok) return;
      const incidentEl = document.getElementById('case-incident-id');
      const snowEl = document.getElementById('case-snow-ticket');
      const statusEl = document.getElementById('case-status');
      const assigneeEl = document.getElementById('case-assignee');
      const sourceEl = document.getElementById('case-source');
      const odinSummaryConsoleEl = document.getElementById('odin-summary-console');

      if(incidentEl) incidentEl.textContent = d.incident_id || 'N/A';
      if(snowEl) {
        const ticketLink = d.service_now_link || d.service_now_ticket || '';
        snowEl.textContent = d.service_now_ticket || 'N/A';
        if(ticketLink && ticketLink.trim() !== '') {
          snowEl.href = ticketLink;
        }
      }
      if(assigneeEl) assigneeEl.textContent = d.assignee || 'Security Managed Services';
      if(sourceEl) sourceEl.textContent = d.source || 'Microsoft Purview + Sentinel';
      updateStatusBadge(statusEl, d.status || 'In Progress');

      if(odinSummaryConsoleEl && !selectedIncidentForOdin) {
        if(d.odin_summary && Array.isArray(d.odin_summary) && d.odin_summary.length > 0) {
          odinSummaryConsoleEl.innerHTML = d.odin_summary.map(item => `<p>${item}</p>`).join('');
        } else {
          odinSummaryConsoleEl.innerHTML = '<p style="color: #999;">No Odin Summary data available.</p>';
        }
      }
    })
    .catch(() => {});
}

// Auto-scroll helper
function scrollLog(){
  if(!log) return;
  const t = log.closest('.terminal');
  if(t) t.scrollTop = t.scrollHeight;
}

// Append a line to the log with optional colour class
function appendLog(text, cls){
  if(!log) return;
  const span = document.createElement('span');
  if(cls) span.className = cls;
  span.textContent = text + '\n';
  log.appendChild(span);
  scrollLog();
}

// SSE: live Python output
let sse = null;

function showNotification(notifData){
  const container = document.getElementById('notificationContainer');
  if(!container) return;

  const toast = document.createElement('div');
  toast.className = `notification-toast ${(notifData.status || 'info').toLowerCase()}`;
  toast.innerHTML = `
    <div class="notification-header">
      <span class="notification-title">${escapeHtml(notifData.title || 'Notification')}</span>
      <button type="button" class="notification-close" aria-label="Close notification">x</button>
    </div>
    <div class="notification-text">${escapeHtml(notifData.text || '')}</div>
    <div class="notification-meta">
      <span class="notification-status ${(notifData.status || 'info').toLowerCase()}">${escapeHtml(notifData.status || 'Info')}</span>
      <span>From: ${escapeHtml(notifData.source || 'Automation')}</span>
    </div>
  `;
  container.appendChild(toast);

  const closeBtn = toast.querySelector('.notification-close');

  function dismissToast(){
    if(!toast.isConnected) return;
    toast.style.animation = 'slideOut 0.3s ease';
    setTimeout(() => toast.remove(), 300);
  }

  if(closeBtn){
    closeBtn.addEventListener('click', () => {
      dismissToast();
    });
  }

  // Auto-dismiss notification after 3 seconds
  setTimeout(() => {
    dismissToast();
  }, 3000);
}

function connectSSE(){
  if(sse) sse.close();
  sse = new EventSource(automationApiUrl('/stream'));

  sse.onopen = () => appendLog('[RedFlagged] Connected to automation stream.', 'c-good');

  sse.addEventListener('status', (e) => {
    try {
      const payload = JSON.parse(e.data);
      renderAutomationStatus(payload);
    } catch (error) {
      console.error('Failed to parse status event:', error);
    }
  });

  sse.addEventListener('notification', (e) => {
    try {
      const notifData = JSON.parse(e.data);
      showNotification(notifData);
    } catch(err) {
      console.error('Failed to parse notification:', err);
    }
  });

  sse.onmessage = (e) => {
    const line = e.data;
    let cls = 'c-text';
    if(/error|exception|traceback|fail/i.test(line)) cls = 'c-bad';
    else if(/warn|caution/i.test(line))              cls = 'c-warn';
    else if(/success|complete|done|saved|✔|found/i.test(line)) cls = 'c-good';
    else if(/^\s*\[/.test(line))                                cls = 'c-accent';
    appendLog(line, cls);
  };

  sse.addEventListener('done', () => {
    appendLog('\n[RedFlagged] Automation run complete.', 'c-good');
    loadArtifacts();
    fetchAutomationStatus();
    sse.close();
    sse = null;
  });

  sse.onerror = () => {
    if(sse && sse.readyState === EventSource.CLOSED){
      appendLog('[RedFlagged] Stream closed.', 'c-muted');
    }
  };
}

// Manual command box
function runCommand(){
  const value = cmd.value.trim();
  if(!value) return;
  appendLog(`\n> ${value}`, 'c-accent');

  if(value === 'rf.help'){
    appendLog('  rf.run            - Start full Purview->Outlook->Odin pipeline', 'c-muted');
    appendLog('  rf.filter         - Run data_filter.py on downloads/', 'c-muted');
    appendLog('  rf.clear          - Clear this log', 'c-muted');
    appendLog('  rf.help           - Show this help', 'c-muted');
  } else if(value === 'rf.clear'){
    log.innerHTML = '';
  } else {
    // Send command to backend
    fetch(automationApiUrl('/run'), {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({command: value})})
      .then(r => r.json())
      .then(d => {
        if(d.status === 'started') connectSSE();
        else appendLog('[RedFlagged] ' + (d.message || 'Unknown response.'), 'c-warn');
      })
      .catch(() => appendLog('[RedFlagged] Backend not reachable.', 'c-bad'));
  }
  cmd.value = '';
}

if(cmd){
  cmd.addEventListener('keydown', e => { if(e.key === 'Enter') runCommand(); });
}

if(modalAgentSupportToggle && modalAgentSupportPanel){
  modalAgentSupportToggle.addEventListener('click', (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleModalAgentSupport();
  });
}

if(openModalFromAgent){
  openModalFromAgent.addEventListener('click', () => {
    if(!selectedIncidentForOdin){
      showNotification({
        title: 'No Incident Selected',
        text: 'Please select an incident to view the assigned agent.',
        status: 'Warning',
        source: 'Agent Summary'
      });
      return;
    }
    openIncidentModal(selectedIncidentForOdin);
  });
}

if(consultOdinFromSummary){
  consultOdinFromSummary.addEventListener('click', () => {
    if(!selectedIncidentForOdin){
      showNotification({
        title: 'No Incident Selected',
        text: 'Please select Incident to consult Odin',
        status: 'Warning',
        source: 'Odin Summary'
      });
      return;
    }
    openAgentSupportModal();
  });
}

if(viewIncidentFromOdin){
  viewIncidentFromOdin.addEventListener('click', () => {
    if(!selectedIncidentForOdin){
      showNotification({
        title: 'No Incident Selected',
        text: 'No Incident Selected',
        status: 'Warning',
        source: 'Odin Summary'
      });
      return;
    }

    renderOdinSummaryFromIncident(selectedIncidentForOdin);
    openIncidentModal(selectedIncidentForOdin);
  });
}

if(resolveIncidentFromOdin){
  resolveIncidentFromOdin.addEventListener('click', () => {
    if(!selectedIncidentForOdin){
      showNotification({
        title: 'No Incident Selected',
        text: 'No Incident Selected',
        status: 'Warning',
        source: 'Odin Summary'
      });
      return;
    }

    selectedIncidentForOdin = selectedIncidentForOdin;
    renderOdinSummaryFromIncident(selectedIncidentForOdin);
    openResolveModal(selectedIncidentForOdin);
  });
}

if(odinChatSend){
  odinChatSend.addEventListener('click', () => {
    sendOdinChatMessage();
  });
}

if(odinChatInput){
  odinChatInput.addEventListener('keydown', (e) => {
    if(e.key === 'Enter'){
      e.preventDefault();
      sendOdinChatMessage();
    }
  });
}

if(odinChatQuickButtons && odinChatQuickButtons.length > 0){
  odinChatQuickButtons.forEach((btn) => {
    btn.addEventListener('click', () => {
      sendOdinChatMessage(btn.getAttribute('data-prompt') || btn.textContent || '');
    });
  });
}

if(agentSupportModalClose){
  agentSupportModalClose.addEventListener('click', () => {
    closeAgentSupportModal();
  });
}

if(resolveModalClose){
  resolveModalClose.addEventListener('click', () => {
    closeResolveModal();
  });
}

if(resolveCancel){
  resolveCancel.addEventListener('click', () => {
    closeResolveModal();
  });
}

if(resolveSave){
  resolveSave.addEventListener('click', async () => {
    if(!activeResolveRow){
      closeResolveModal();
      return;
    }

    const incidentId = String(resolveIncidentId ? resolveIncidentId.textContent : '').trim();
    const originalResolveLabel = resolveSave.textContent;
    resolveSave.disabled = true;
    resolveSave.textContent = 'Starting...';
    if(resolveCancel) resolveCancel.disabled = true;

    try {
      const selectedClassification = resolveClassification ? resolveClassification.value : 'Unclassified';
      const selectedResolutionNote = resolveNote ? resolveNote.value.trim() : '';
      const selectedResolveExplanation = resolveExplanation ? resolveExplanation.value.trim() : '';
      const selectedComment = [selectedResolutionNote, selectedResolveExplanation].filter(Boolean).join('\n\n');
      const payload = await startIncidentResolveAutomation(incidentId, {
        status: 'Resolved',
        classification: selectedClassification,
        comment: selectedComment
      });
      activeResolveRow.classification = selectedClassification;
      activeResolveRow.resolutionNote = selectedResolutionNote;
      activeResolveRow.resolveExplanation = selectedResolveExplanation;
      activeResolveRow.status = 'Resolved';
      renderIncidentsTable(incidentRows);
      renderExecutiveDashboard(incidentRows);
      showNotification({
        title: 'Incident Resolved',
        text: payload?.message || `incident_resolved.py started for ${incidentId || 'incident'}.`,
        status: 'Success',
        source: 'Resolve Modal'
      });
      closeResolveModal();
    } catch (error) {
      showNotification({
        title: 'Resolve Failed',
        text: error instanceof Error ? error.message : 'Unable to start incident_resolved.py.',
        status: 'Error',
        source: 'Resolve Modal'
      });
    } finally {
      resolveSave.disabled = false;
      resolveSave.textContent = originalResolveLabel;
      if(resolveCancel) resolveCancel.disabled = false;
    }
  });
}

document.addEventListener('click', (e) => {
  const target = e.target;
  if(!(target instanceof Element)) return;
  if(target.closest('#modal-agent-support-toggle')){
    e.preventDefault();
    toggleModalAgentSupport();
  }
});

if(tabIncidents){
  tabIncidents.addEventListener('click', (e) => {
    e.preventDefault();
    setActiveTab('incidents');
    renderIncidentsTable(incidentRows);
  });
}

if(tabExecutive){
  tabExecutive.addEventListener('click', (e) => {
    e.preventDefault();
    setActiveTab('executive');
    renderExecutiveDashboard(incidentRows);
    loadExecutiveTrends();
  });
}

if(tabArtifacts){
  tabArtifacts.addEventListener('click', (e) => {
    e.preventDefault();
    setActiveTab('artifacts');
    syncArtifactFilterState(true);
  });
}

if(tabMonitoring){
  tabMonitoring.addEventListener('click', (e) => {
    e.preventDefault();
    setActiveTab('monitoring');
  });
}

if(tabSelectors){
  tabSelectors.addEventListener('click', (e) => {
    e.preventDefault();
    setActiveTab('selectors');
    if(window.RedflaggedSelectorConfig && typeof window.RedflaggedSelectorConfig.init === 'function'){
      window.RedflaggedSelectorConfig.init();
    }
  });
}

if(tabSettings){
  tabSettings.addEventListener('click', (e) => {
    e.preventDefault();
    setActiveTab('settings');
  });
}

if(artifactsIncidentFilter){
  artifactsIncidentFilter.addEventListener('input', () => { syncArtifactFilterState(true); });
  artifactsIncidentFilter.addEventListener('change', () => { syncArtifactFilterState(true); });
  artifactsIncidentFilter.addEventListener('search', () => { syncArtifactFilterState(true); });
  artifactsIncidentFilter.addEventListener('keyup', () => { syncArtifactFilterState(true); });
}

if(avpSearchField){
  updateArtifactSearchPlaceholder();
  avpSearchField.addEventListener('change', () => {
    updateArtifactSearchPlaceholder();
    syncArtifactFilterState(true);
  });
}

if(avpAlertColumnToggle){
  applyArtifactAlertColumnState();
  avpAlertColumnToggle.addEventListener('click', () => {
    isArtifactAlertColumnExpanded = !isArtifactAlertColumnExpanded;
    applyArtifactAlertColumnState();
  });
}

// AVP filter dropdowns
// AVP filter dropdowns
const avpStatusFilter = document.getElementById('avp-status-filter');
const avpExportBtn = document.getElementById('avp-export-btn');

if(avpStatusFilter){ avpStatusFilter.addEventListener('change', () => syncArtifactFilterState(true)); }

if(artifactsIncidentFilter || avpSearchField || avpStatusFilter){
  window.setInterval(() => {
    syncArtifactFilterState();
  }, 250);
}

if(avpExportBtn){
  avpExportBtn.addEventListener('click', () => {
    const rows = getFilteredArtifactRows();
    if(!rows.length){ showNotification({ title: 'Nothing to export', text: 'No artifacts match the current filters.', status: 'Warning', source: 'Artifacts' }); return; }
    const header = ['Artifact','Alert Name','Incident ID','Date Collected','Alert Status','Relative Path'];
    const csv = [header, ...rows.map(r => [r.artifact, r.alertName, r.incidentId, r.date, avpGetStatus(r), r.relativePath].map(v => `"${String(v||'').replace(/"/g,'""')}"`))].map(r => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'artifacts-inventory.csv' });
    document.body.appendChild(a); a.click(); a.remove();
    showNotification({ title: 'Exported', text: `${rows.length} artifact(s) exported.`, status: 'Success', source: 'Artifacts' });
  });
}

if(executiveYearFilter){
  executiveYearFilter.addEventListener('change', () => {
    classificationHistoryPage = 1;
    renderClassificationHistoryTable();
  });
}

if(executiveMonthFilter){
  executiveMonthFilter.addEventListener('change', () => {
    classificationHistoryPage = 1;
    renderClassificationHistoryTable();
  });
}

if(executiveClassificationFilter){
  executiveClassificationFilter.addEventListener('change', () => {
    classificationHistoryPage = 1;
    renderClassificationHistoryTable();
  });
}

if(executiveHistorySearch){
  executiveHistorySearch.addEventListener('input', () => {
    classificationHistoryPage = 1;
    renderClassificationHistoryTable();
  });
}

if(classificationHistoryExport){
  classificationHistoryExport.addEventListener('click', () => {
    exportClassificationHistoryCsv();
  });
}

if(artifactsByIncidentBody){
  artifactsByIncidentBody.addEventListener('click', async (e) => {
    const target = e.target;
    if(!(target instanceof Element)) return;

    const deleteBtn = target.closest('.avp-btn-delete');
    if(deleteBtn){
      const key = deleteBtn.getAttribute('data-avp-key') || '';
      const row = (Array.isArray(artifactRows) ? artifactRows : []).find(r => avpKey(r) === key);
      if(!row) return;
      if(!canDeleteArtifact(row)){
        showNotification({ title: 'Delete Locked', text: `${row.artifact} cannot be deleted until the alert is resolved.`, status: 'Warning', source: 'Artifacts' });
        return;
      }
      if(!window.confirm(`Delete artifact "${row.artifact}" from the artifacts folder?`)){
        return;
      }

      try {
        const deleteResult = await deleteArtifactFromDisk(row);
        const payload = deleteResult?.inventory && Array.isArray(deleteResult.inventory.files)
          ? deleteResult.inventory
          : buildArtifactInventoryPayload(artifactInventoryFiles.filter(file => String(file.relative_path || '') !== String(row.relativePath || '')));
        artifactInventoryFiles = Array.isArray(payload.files) ? payload.files.slice() : [];
        artifactRows = mergeIncidentRowState(artifactRows, buildArtifactRows(artifactInventoryFiles));
        window.__REDFLAGGED_ARTIFACTS__ = payload;
        renderArtifacts(artifactInventoryFiles);
        renderArtifactsByIncident();
        showNotification({
          title: 'Artifact Deleted',
          text: `${row.artifact} was removed and the artifact inventory was updated.`,
          status: 'Success',
          source: 'Artifacts'
        });
      } catch (error) {
        showNotification({ title: 'Delete Failed', text: error instanceof Error ? error.message : 'Unable to delete the selected artifact.', status: 'Error', source: 'Artifacts' });
      }
      return;
    }
  });
}

if(incidentModalClose){
  incidentModalClose.addEventListener('click', () => {
    closeIncidentModal();
  });
}

if(incidentModal){
  incidentModal.addEventListener('click', (e) => {
    if(e.target === incidentModal){
      closeIncidentModal();
    }
  });
}

if(agentSupportModal){
  agentSupportModal.addEventListener('click', (e) => {
    if(e.target === agentSupportModal){
      closeAgentSupportModal();
    }
  });
}

if(resolveModal){
  resolveModal.addEventListener('click', (e) => {
    if(e.target === resolveModal){
      closeResolveModal();
    }
  });
}

document.addEventListener('keydown', (e) => {
  if(e.key === 'Escape'){
    closeIncidentModal();
    closeAgentSupportModal();
    closeResolveModal();
  }
});

// Auto-connect on load if server is present
window.addEventListener('load', () => {
  initSettingsTheme();
  initSettingsTimezone();
  initSettingsFontSize();
  initSystemConfigControls();
  initIncidentFilterBar();
  ensureMonitoringRealtimeTimer();

  loadDbInfo();
  loadCaseDetails();
  loadArtifacts();
  // Start refresh intervals after initial load
  setInterval(loadDbInfo, 25000);
  setInterval(loadCaseDetails, 25000);
  setInterval(loadArtifacts, 25000);

  if(automationStatusPollHandle){
    clearInterval(automationStatusPollHandle);
  }
  automationStatusPollHandle = window.setInterval(() => {
    fetchAutomationStatus();
  }, 15000);

  fetchAutomationStatus({ announce: true, connectIfRunning: true });
});

