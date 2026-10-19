(function(){
  function formatActionLabel(key){
    if(key === 'containIncident') return 'Contain Incident';
    if(key === 'contain') return 'Contain';
    if(key === 'collect') return 'Collect';
    if(key === 'validate') return 'Validate';
    return 'Analyze';
  }

  function buildDecision(incident, actionKey){
    const key = actionKey || window.ActionEngine.getRecommendedAction(incident);
    const learningBoost = window.ActionEngine.getLearningBoost(incident.incidentId);
    const riskBefore = typeof incident.riskScore === 'number'
      ? incident.riskScore
      : window.RiskEngine.computeRiskScore(incident);
    const riskAfter = window.RiskEngine.projectRiskAfterAction(incident, key, learningBoost);

    const confidence = Math.max(60, Math.min(97, 68 + learningBoost * 3 + (key === 'containIncident' ? 14 : 0)));

    return {
      actionKey: key,
      actionLabel: formatActionLabel(key),
      confidence,
      riskBefore: Math.round(riskBefore),
      riskAfter: Math.round(riskAfter),
      summary: `Odin recommends ${formatActionLabel(key)} for ${incident.incidentId}.` +
        ` Simulated risk change: ${Math.round(riskBefore)} -> ${Math.round(riskAfter)}.`,
      what: [
        `Detection observed: ${incident.detection || 'Unknown detection'}.`,
        `Policy context: ${incident.policy || 'N/A'} (${incident.policySeverity || 'medium'} severity).`,
        `Current status is ${incident.status || 'Unknown'} with owner ${incident.assignedTo || 'Unassigned'}.`
      ],
      why: [
        'Risk score combines status, detection profile, and policy severity.',
        'Learning mode adjusts confidence based on prior successful actions.',
        'Containment-first orchestration is recommended for critical-risk cases.'
      ],
      next: [
        `Execute ${formatActionLabel(key)} from inline actions or terminal command.`,
        'Review artifact trail and timeline before final closure.',
        'Track post-action risk delta and SLA impact in Executive dashboard.'
      ]
    };
  }

  window.OdinEngine = {
    buildDecision
  };
})();
