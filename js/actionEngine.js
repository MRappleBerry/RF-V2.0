(function(){
  const learningState = {
    byIncident: {}
  };

  function ensureIncidentBucket(incidentId){
    if(!learningState.byIncident[incidentId]){
      learningState.byIncident[incidentId] = {
        actions: [],
        counts: {}
      };
    }
    return learningState.byIncident[incidentId];
  }

  function trackAction(incident, actionKey){
    const bucket = ensureIncidentBucket(incident.incidentId || 'unknown');
    bucket.actions.push({
      key: actionKey,
      at: new Date().toISOString()
    });
    bucket.counts[actionKey] = (bucket.counts[actionKey] || 0) + 1;
    incident.actionHistory = bucket.actions.slice();
    return bucket;
  }

  function getLearningBoost(incidentId){
    const bucket = learningState.byIncident[incidentId || ''];
    if(!bucket) return 0;
    const validateCount = bucket.counts.validate || 0;
    const collectCount = bucket.counts.collect || 0;
    return Math.min(8, validateCount + collectCount);
  }

  function getRecommendedAction(incident){
    const bucket = learningState.byIncident[incident.incidentId || ''];
    const status = String(incident.status || '').toLowerCase();

    if(status.includes('contained') || status.includes('resolved')) return 'monitor';
    if((incident.riskScore || 0) >= 80) return 'containIncident';
    if(!bucket || !bucket.counts.validate) return 'validate';
    if(!bucket.counts.collect) return 'collect';
    return 'contain';
  }

  function applyAction(incident, actionKey){
    const key = actionKey || 'validate';
    const riskBefore = typeof incident.riskScore === 'number'
      ? incident.riskScore
      : window.RiskEngine.computeRiskScore(incident);

    const bucket = trackAction(incident, key);
    const learningBoost = getLearningBoost(incident.incidentId);
    const riskAfter = window.RiskEngine.projectRiskAfterAction(incident, key, learningBoost);

    if(key === 'validate'){
      incident.status = 'Investigating';
      incident.recommendation = 'Collect evidence';
    }
    if(key === 'collect'){
      incident.status = 'In Progress';
      incident.recommendation = 'Contain if confirmed';
    }
    if(key === 'contain'){
      incident.status = 'Contained';
      incident.recommendation = 'Prepare resolution validation';
    }
    if(key === 'containIncident'){
      incident.status = 'Contained';
      incident.recommendation = 'Automated containment executed';
      incident.containedAt = new Date().toISOString();
    }

    incident.riskScore = Math.round(riskAfter);
    const riskBand = window.RiskEngine.getRiskBand(incident.riskScore);
    incident.riskBand = riskBand.label;
    incident.riskClass = riskBand.className;

    const confidence = Math.max(62, Math.min(98, 70 + (bucket.counts.validate || 0) * 6 + (bucket.counts.collect || 0) * 5));

    const logs = [];
    logs.push(`[Action] ${incident.incidentId} -> ${key}`);

    if(key === 'containIncident'){
      logs.push('  - Revoking risky sharing links');
      logs.push('  - Locking user account session tokens');
      logs.push('  - Marking incident as Contained');
    } else if(key === 'contain'){
      logs.push('  - Applying scoped containment controls');
      logs.push('  - Updating case owner and recommendation');
    } else if(key === 'collect'){
      logs.push('  - Pulling audit logs and artifact metadata');
      logs.push('  - Updating evidence completeness score');
    } else {
      logs.push('  - Validating signal correlation and actor context');
    }

    logs.push(`  - Risk impact: ${Math.round(riskBefore)} -> ${Math.round(riskAfter)}`);

    return {
      key,
      riskBefore: Math.round(riskBefore),
      riskAfter: Math.round(riskAfter),
      confidence,
      logs,
      recommendation: getRecommendedAction(incident),
      learning: bucket
    };
  }

  function getLearningState(){
    return learningState;
  }

  window.ActionEngine = {
    applyAction,
    getLearningBoost,
    getLearningState,
    getRecommendedAction
  };
})();
