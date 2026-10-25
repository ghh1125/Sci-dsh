// Pure verification of observed tool events. Does not execute tools or models.
const sameCall = (a, b) => typeof a.callId === 'string' && a.callId.length > 0
  && a.callId === b.callId && a.sessionId === b.sessionId;
const hashPresent = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);

export function verifyWorkflow(events, expected = null) {
  const failures = [];
  const analyses = [];
  const loads = new Map();
  // Installed files are shared across Web sessions: a mutation invalidates every
  // session's prior instructions, but one session's load cannot serve another.
  let lastMutation = -1;
  for (let index = 0; index < events.length; index++) {
    const event = events[index];
    if (event.event === 'tool-result' && event.tool === 'skill'
      && event.skillName === 'ocean-sst-analysis' && !event.isError
      && typeof event.sessionId === 'string' && hashPresent(event.bodySha256)) {
      loads.set(event.sessionId, { index, bodySha256: event.bodySha256 });
    }
    if (event.event === 'style-updated' || event.event === 'skill-installed') lastMutation = index;
    if (event.event === 'workflow-start') {
      const end = events.slice(index + 1).find(e => e.event === 'workflow-end' && e.operationId === event.operationId);
      if (!end?.ok) failures.push(`流程未成功结束：${event.tool}`);
    }
    if (event.event !== 'analysis-start') continue;
    const endIndex = events.findIndex((e, i) => i > index && e.event === 'analysis-end'
      && sameCall(event, e) && e.output === event.output);
    const end = endIndex < 0 ? null : events[endIndex];
    const load = loads.get(event.sessionId);
    const orderOK = !event.skillRequired || Boolean(load && load.index > lastMutation
      && hashPresent(event.skillBodySha256) && load.bodySha256 === event.skillBodySha256);
    const missingMonthDetected = Boolean(event.case === 'missing-month' && end?.exitCode === 2
      && end.issues?.some(s => typeof s === 'string' && s.includes('缺少月份') && s.includes('2000-02')));
    const finished = Boolean(end?.ok || missingMonthDetected);
    if (!orderOK) failures.push(`分析前未在同一会话加载当前Skill正文：${event.output}`);
    if (!finished) failures.push(`分析没有成功结束或未检出预期缺月：${event.output}`);
    analyses.push({ ...event, index, endIndex, finished, orderOK,
      analysisSucceeded: Boolean(end?.ok), missingMonthDetected });
  }
  // A successful process may still fail the tool's later completeness checks or
  // Skill discovery. The authoritative canonical result carries those facts.
  for (const event of events) {
    if (event.event !== 'tool-result') continue;
    if (event.isError) failures.push(`工具调用失败：${event.tool}`);
    const expectedNegative = event.tool === 'sst_analyze'
      && analyses.some(analysis => sameCall(analysis, event) && analysis.missingMonthDetected);
    if (event.businessOK === false && !expectedNegative) failures.push(`业务检查未成功：${event.tool}`);
    if (event.ready === false) failures.push(`Skill已落盘但尚未被Harness发现：${event.tool}`);
  }
  if (expected) {
    const match = analyses.find(e => e.case === expected.case && e.output === expected.output
      && e.baseline?.[0] === expected.baseline[0] && e.baseline?.[1] === expected.baseline[1]
      && (!expected.style || e.style === expected.style));
    if (!match?.finished || !match?.orderOK) failures.push('没有按本次要求完成目标数据、基准期和输出目录的分析。');
    if (expected.setStyle && (!match || !events.slice(0, match.index).some(e => e.event === 'style-updated'
      && e.after === expected.setStyle && e.sessionId === match.sessionId))) {
      failures.push('分析之前没有观察到本会话要求的Skill样式修改。');
    }
    if (match?.analysisSucceeded && expected.requireFigure !== false) {
      const run = match.output.split(/[\\/]/).at(-1);
      const shown = events.some((e, i) => i > match.endIndex && e.event === 'tool-result'
        && e.tool === 'sst_show_figure' && !e.isError && e.businessOK === true
        && e.sessionId === match.sessionId && e.run === run);
      if (!shown) failures.push('目标分析成功后未返回该运行的图片展示结果。');
    }
  }
  const businessCalled = analyses.length > 0 || events.some(e => e.event === 'workflow-start'
    || (e.event === 'tool-result' && e.tool?.startsWith('sst_') && !e.isError));
  if (!businessCalled) failures.push('没有实际调用科研业务工具。');
  return { ok: failures.length === 0, failures, analyses,
    validationScope: expected ? 'preset-analysis-and-figure' : 'observed-tool-execution',
    note: expected ? '核对预设分析任务的工具证据、Skill正文和展示结果；不代表独立科学审稿或系统性能测试。'
      : '只核对实际观察到的工具调用；未自动判定任意自然语言请求的全部要求均已完成。' };
}
