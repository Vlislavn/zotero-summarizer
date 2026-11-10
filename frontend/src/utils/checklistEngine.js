// Checklist-engine options (Settings). Numbers are the measured ones from the checklist-engine bench (data/perf-20261107/speed/jev-hybrid/report.md
// and telegram/faith/dispute/report.md); nothing here is a promise about accuracy.
export const LOCAL_URL = 'http://127.0.0.1:8766/v1/systemone';
// `detail` is the option's own line; `summary` (Settings overview) prefixes it with the label.
const engine = (label, detail) => ({ label, detail, summary: `${label} · ${detail}` });
export const CHECKLIST_ENGINES = {
  off: engine('Off', 'the language model answers every checklist item'),
  local: engine('Local', 'an engine on this computer answers some checklist items · not validated'),
  cloud: engine('Cloud', 'an external model answers some checklist items'),
};
export const GAIN_LINE = 'Can answer 5 checklist items (code/data released, uncertainty, ablations, baselines, outside validation) in about 0.4 s and under $0.001 per paper; unsure items still go to the language model.';
export const GAIN_DETAILS = [
  'Measured on 170 earlier reviews: it settled 39–89% of these items (most for code/data and uncertainty) and matched the language model’s answer in 95–98% of those. That shows agreement, not correctness.',
  'In Telegram it picks out one clear disagreement between commenters: on 30 hand-checked threads it flagged 9, all 9 right, and missed most others.',
  'It does not decide the paper type, dataset provenance, reproducibility detail or overstatement; those stay with the language model.',
];
export const CLOUD_CONSENT = 'Paper text is sent to an external model; retention is not guaranteed.';
export const LOCAL_NOTICE = 'Local engine: not validated. A local engine was tried on Telegram comment pairs and scored no better than chance; none has been checked on paper checklists.';
