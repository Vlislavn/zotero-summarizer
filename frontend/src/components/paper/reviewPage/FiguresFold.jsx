import PaperFigures from '../../library/PaperFigures.jsx';
import { Fold } from '../review/primitives.jsx';
import { figuresSummary } from './reviewPageModel.js';

export default function FiguresFold({ itemKey, render, renderCompleted, figuresBuildable, attach }) {
  const { attachMsg, attaching, onAttachFigures } = attach;
  return (
    <Fold id="figures" label="Figures" summary={figuresSummary(render, renderCompleted)}>
      <div className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-1">
        <button
          type="button"
          onClick={onAttachFigures}
          hidden={!renderCompleted || render.stale || itemKey.startsWith('feed:')} // a feed paper has no Zotero item
          disabled={attaching}
          className="text-[13px] font-medium text-forest-800 hover:text-forest-700 disabled:opacity-50"
          title="Attach these figures to the paper's Zotero item so they show in Zotero. Skips any already attached; needs Zotero closed (or confirm force)."
        >
          {attaching ? 'Saving…' : '+ Save figures to this paper in Zotero'}
        </button>
        {attachMsg && <span className="text-[13px] text-slate-500">{attachMsg}</span>}
      </div>
      <PaperFigures itemKey={itemKey} hasPdf={figuresBuildable} canBuild={figuresBuildable} />
    </Fold>
  );
}
