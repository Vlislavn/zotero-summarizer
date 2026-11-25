// One page-title style for Library / Search / Weekly (L-20) and one short line on
// what each page is FOR (L-05), kept together so the four "what should I read"
// surfaces stay distinguishable in one place. Titles keep the route names.
const PAGE_PURPOSE = {
  today: 'New items from your feeds — decide what enters your library.',
  library: 'Papers already in Zotero — pick what to read next, or label what you have read.',
  search: 'Find new papers on a topic from open sources (not your library).',
  weekly: 'Saved weekly digests of your RSS and Telegram reading.',
  ops: 'Operator tools: feed review, triage jobs, pending Zotero changes and system maintenance.',
  conferences: 'Late-breaking and all abstracts of conferences you define — stored only on this computer.',
};

// `actions` sits at the right of the title (the Library's mode tabs), so the title row is one row.
// `phoneSubtitle={false}` drops the purpose line below 640 px, where the list should start higher.
export default function PageTitle({ title, page, actions = null, phoneSubtitle = true, children }) {
  return (
    <header className="mb-3">
      <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
        <h1 className="font-display text-2xl font-light text-forest-800">{title}</h1>
        {actions}
      </div>
      <p className={`text-sm text-slate-600 ${phoneSubtitle ? '' : 'hidden sm:block'}`}>{PAGE_PURPOSE[page]}</p>
      {children}
    </header>
  );
}
