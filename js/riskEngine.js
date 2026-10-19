(function(){
  function clamp(value, min, max){
    return Math.max(min, Math.min(max, value));
  }

  function scoreFromStatus(status){
    const normalized = String(status || '').toLowerCase();
    if(normalized.includes('contained')) return 20;
    if(normalized.includes('resolved') || normalized.includes('closed')) return 15;
    if(normalized.includes('pending')) return 55;
    if(normalized.includes('investigat')) return 72;
    if(normalized.includes('progress')) return 62;
    if(normalized.includes('open')) return 78;
    return 50;
  }

  function scoreFromDetection(detection){
    const normalized = String(detection || '').toLowerCase();
    if(/exfiltration|staging|malware|phishing/.test(normalized)) return 25;
    if(/privileged|impossible travel|external sharing/.test(normalized)) return 20;
    if(/downgrade|credit card|sensitive/.test(normalized)) return 16;
    return 12;
  }

  function scoreFromPolicy(policySeverity){
    const normalized = String(policySeverity || '').toLowerCase();
    if(normalized === 'critical') return 22;
    if(normalized === 'high') return 18;
    if(normalized === 'medium') return 12;
    if(normalized === 'low') return 6;
    return 10;
  }

  function computeRiskScore(incident){
    const base = scoreFromStatus(incident.status);
    const detectionWeight = scoreFromDetection(incident.detection);
    const policyWeight = scoreFromPolicy(incident.policySeverity);
    const score = clamp(base + detectionWeight + policyWeight - 18, 0, 100);
    return Math.round(score);
  }

  function getRiskBand(score){
    if(score >= 80) return { label: 'Critical', className: 'bad' };
    if(score >= 50) return { label: 'Medium', className: 'warn' };
    return { label: 'Low', className: 'good' };
  }

  function projectRiskAfterAction(incident, actionKey, learningBoost){
    const current = typeof incident.riskScore === 'number' ? incident.riskScore : computeRiskScore(incident);
    const memoryDelta = Math.min(8, Math.max(0, Number(learningBoost) || 0));

    let reduction = 0;
    if(actionKey === 'validate') reduction = 10;
    if(actionKey === 'collect') reduction = 14;
    if(actionKey === 'contain') reduction = 28;
    if(actionKey === 'containIncident') reduction = 52;
    if(actionKey === 'analyze') reduction = 0;

    return clamp(current - reduction - memoryDelta, 0, 100);
  }

  function scoreAndSort(rows){
    const source = Array.isArray(rows) ? rows : [];
    source.forEach((row) => {
      row.riskScore = computeRiskScore(row);
      const riskBand = getRiskBand(row.riskScore);
      row.riskBand = riskBand.label;
      row.riskClass = riskBand.className;
    });
    source.sort((a, b) => (b.riskScore || 0) - (a.riskScore || 0));
    return source;
  }

  window.RiskEngine = {
    computeRiskScore,
    getRiskBand,
    projectRiskAfterAction,
    scoreAndSort
  };
})();
