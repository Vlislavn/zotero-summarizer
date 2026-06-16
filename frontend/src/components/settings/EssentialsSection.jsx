// Settings → Essentials. The always-visible core: research goals, triage
// criteria, output language, the slim default-provider editor, and the Zotero
// paths with a live status row + a [Save paths] action (restart-required).
//
// Anchors (essentials-goals / essentials-provider / essentials-zotero-paths)
// are the scroll targets the ReadinessStrip's failing pills jump to.

import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { updatePaths } from '../../api/setupApi.js';
import { useSetupStatus } from '../../hooks/useSetupStatus.js';
import { humanizeError } from '../../utils/humanizeError.js';
import { Banner, Field, SectionCard } from '../form/Fields.jsx';
import DefaultProviderField from './DefaultProviderField.jsx';

function PathStatus({ label, info }) {
  const set = Boolean(info?.set);
  const exists = Boolean(info?.exists);
  const tone = exists
    ? 'text-emerald-700'
    : set
      ? 'text-rose-700'
      : 'text-slate-400';
  const mark = exists ? '✓' : set ? '✗' : '○';
  return (
    <span className={`inline-flex items-center gap-1 ${tone}`}>
      <span aria-hidden>{mark}</span>
      {label}: {exists ? 'exists' : set ? 'missing' : 'unset'}
    </span>
  );
}

function ZoteroPaths({ form, onUpdate }) {
  const queryClient = useQueryClient();
  const { status } = useSetupStatus();
  const [restartBanner, setRestartBanner] = useState('');

  const saveMutation = useMutation({
    mutationFn: updatePaths,
    onSuccess: () => {
      setRestartBanner('Restart required to apply new paths.');
      queryClient.invalidateQueries({ queryKey: ['setup-status'] });
    },
  });

  const paths = status?.paths || {};
  const zotero = status?.zotero || {};

  function handleSavePaths() {
    setRestartBanner('');
    const body = {};
    if (form.zotero_data_dir) body.zotero_data_dir = form.zotero_data_dir;
    if (form.pdf_root) body.pdf_root = form.pdf_root;
    saveMutation.mutate(body);
  }

  return (
    <div id="essentials-zotero-paths" className="space-y-3 scroll-mt-20">
      <div className="grid md:grid-cols-2 gap-4">
        <Field
          label="Zotero data directory"
          value={form.zotero_data_dir || ''}
          onChange={(v) => onUpdate('zotero_data_dir', v)}
          placeholder={paths.zotero_data_dir?.value || '/Users/you/Zotero'}
          hint="Folder containing zotero.sqlite and storage/."
        />
        <Field
          label="PDF root (optional)"
          value={form.pdf_root || ''}
          onChange={(v) => onUpdate('pdf_root', v)}
          placeholder={paths.pdf_root?.value || 'Defaults to the Zotero storage/ folder'}
          hint="Where generated paper-read artifacts are written next to the PDF."
        />
      </div>

      {/* Live status row. */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span
          className={`inline-flex items-center gap-1 ${
            zotero.db_found ? 'text-emerald-700' : 'text-rose-700'
          }`}
        >
          <span aria-hidden>{zotero.db_found ? '✓' : '✗'}</span>
          {zotero.db_found ? `DB found · ${zotero.feed_count ?? 0} feeds` : 'DB not found'}
        </span>
        <PathStatus label="Zotero dir" info={paths.zotero_data_dir} />
        <PathStatus label="PDF root" info={paths.pdf_root} />
      </div>

      <div className="flex items-center gap-3 flex-wrap">
        <button
          type="button"
          onClick={handleSavePaths}
          disabled={saveMutation.isPending || (!form.zotero_data_dir && !form.pdf_root)}
          className="px-3 py-1.5 rounded-lg border border-slate-300 text-sm font-medium hover:bg-slate-100 disabled:opacity-40 disabled:cursor-not-allowed"
        >
          {saveMutation.isPending ? 'Saving…' : 'Save paths'}
        </button>
        <span className="text-xs text-slate-500">
          Paths are written separately from the config and need a restart.
        </span>
      </div>

      {saveMutation.error && (
        <Banner kind="error">{humanizeError(saveMutation.error)}</Banner>
      )}
      {restartBanner && <Banner kind="success">{restartBanner}</Banner>}
    </div>
  );
}

export default function EssentialsSection({ form, onUpdate, onOpenAdvanced, pathForm, onUpdatePath }) {
  return (
    <div className="space-y-4">
      <SectionCard
        title="Research goals"
        description="Free-text descriptions of what you're researching. The triage prompt uses these for goal_alignment scoring — one goal per line."
      >
        <div id="essentials-goals" className="scroll-mt-20">
          <Field
            kind="textarea"
            label="Goals (one per line)"
            value={form.research_goals_text}
            onChange={(v) => onUpdate('research_goals_text', v)}
            rows={6}
          />
        </div>
      </SectionCard>

      <SectionCard
        title="Triage criteria & output"
        description="Per-paper acceptance criteria the LLM weighs, plus the language generated text comes back in."
      >
        <div className="space-y-4">
          <Field
            kind="textarea"
            label="Triage criteria (one per line)"
            value={form.triage_criteria_text}
            onChange={(v) => onUpdate('triage_criteria_text', v)}
            rows={5}
            hint="Each line is a hard or soft criterion the LLM weighs when assigning the relevance score."
          />
          <Field
            label="Output language"
            value={form.output_language}
            onChange={(v) => onUpdate('output_language', v)}
          />
        </div>
      </SectionCard>

      <SectionCard
        title="LLM provider"
        description="The default endpoint that scores your feed. For multiple providers or per-stage routing, use Advanced."
      >
        <div id="essentials-provider" className="scroll-mt-20">
          <DefaultProviderField
            routing={form.llm_routing}
            onChange={(next) => onUpdate('llm_routing', next)}
            onOpenAdvanced={onOpenAdvanced}
          />
        </div>
      </SectionCard>

      <SectionCard
        title="Zotero"
        description="Where the app reads your library and writes generated artifacts. Changing these needs a restart."
      >
        <ZoteroPaths form={pathForm} onUpdate={onUpdatePath} />
      </SectionCard>
    </div>
  );
}
