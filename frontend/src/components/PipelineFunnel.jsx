// Pipeline funnel strip for the Today page — a compact, scannable overview of
// where feed papers go: came in -> filtered -> awaiting you -> added -> trashed.
// Each stage carries a plain-language tooltip (Tesler's Law: the system, not the
// user, explains what a stage means) and, where one exists, deep-links to the
// page that already browses that pool (e.g. Feed Review's Gate-rejected tab).

import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchPipeline } from '../api/dailyApi.js';

const TONE = {
  in: 'bg-slate-100 text-slate-700 border-slate-200',
  filtered: 'bg-rose-50 text-rose-800 border-rose-200',
  awaiting: 'bg-amber-50 text-amber-900 border-amber-200',
  added: 'bg-emerald-50 text-emerald-800 border-emerald-200',
  trashed: 'bg-slate-100 text-slate-600 border-slate-200',
};

function StageChip({ stage, onOpen }) {
  const tone = TONE[stage.key] || TONE.in;
  const clickable = Boolean(stage.link);
  const title = stage.link ? `${stage.hint}\n\nClick to browse →` : stage.hint;
  const Tag = clickable ? 'button' : 'div';
  return (
    <Tag
      type={clickable ? 'button' : undefined}
      onClick={clickable ? () => onOpen(stage.link) : undefined}
      title={title}
      className={`flex flex-col items-start px-2.5 py-1 rounded-lg border ${tone} ${
        clickable ? 'hover:brightness-95 cursor-pointer' : 'cursor-default'
      }`}
    >
      <span className="text-[10px] uppercase tracking-wider font-semibold opacity-80">
        {stage.label}
        {clickable && <span aria-hidden className="ml-1">↗</span>}
      </span>
      <span className="mono text-sm font-bold leading-tight">{stage.count}</span>
    </Tag>
  );
}

export default function PipelineFunnel({ lookbackHours = 168 }) {
  const navigate = useNavigate();
  const { data, error } = useQuery({
    queryKey: ['daily-pipeline', { lookback_hours: lookbackHours }],
    queryFn: () => fetchPipeline({ lookback_hours: lookbackHours }),
    staleTime: 30_000,
  });

  if (error || !data?.stages?.length) return null;

  return (
    <div
      className="mt-2 flex flex-wrap items-center gap-1.5"
      aria-label="Feed pipeline overview"
    >
      {data.stages.map((stage, i) => (
        <div key={stage.key} className="flex items-center gap-1.5">
          {i > 0 && <span aria-hidden className="text-slate-300 text-xs">›</span>}
          <StageChip stage={stage} onOpen={(to) => navigate(to)} />
        </div>
      ))}
    </div>
  );
}
