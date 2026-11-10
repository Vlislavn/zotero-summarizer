// One page-title style for Library / Search / Weekly (L-20) and one short line on
// what each page is FOR (L-05), kept together so the four "what should I read"
// surfaces stay distinguishable in one place. Titles keep the route names.
export const PAGE_PURPOSE = {
  today: 'New items from your feeds — decide what enters your library.',
  library: 'Papers already in Zotero — pick what to read next, or label what you have read.',
  search: 'Find new papers on a topic from open sources (not your library).',
  weekly: 'Saved weekly digests of your RSS and Telegram reading.',
};

export default function PageTitle({ title, page, children }) {
  return (
    <header className="mb-3">
      <h1 className="font-display text-2xl font-light text-forest-800">{title}</h1>
      <p className="text-sm text-slate-600">{PAGE_PURPOSE[page]}</p>
      {children}
    </header>
  );
}
