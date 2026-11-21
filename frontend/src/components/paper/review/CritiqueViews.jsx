import { Disclosure, SectionAnchor } from './primitives.jsx';

function sharedPrefix(left, right) {
  const first = left.split(/\s+/);
  const second = right.split(/\s+/);
  let size = 0;
  while (size < first.length && first[size] === second[size]) size++;
  return size > 1 ? first.slice(0, size).join(' ') : '';
}

function sharedWordFrame(prefix, texts, rubric) {
  const intro = prefix.split(/\s+/);
  for (const term of Object.keys(rubric || {})) {
    const rows = texts.map(text => {
      const words = text.split(/\s+/);
      const position = words.indexOf(term, intro.length);
      if (position < 0 || words.indexOf(term, position + 1) >= 0) return null;
      return { modifier: words.slice(intro.length, position).join(' '),
        suffix: words.slice(position + 1).join(' ') };
    });
    if (rows.every(Boolean)) return { term, rows };
  }
  return null;
}

// A shared opening is a heading only when each remainder reads on its own; "The judge model used"
// + "for direct-framing…" is a sentence cut in half, so such a group is shown in full instead.
const DANGLING_START = /^(for|of|to|in|on|at|by|as|with|from|into|and|or|but|that|which|than|the|a|an)$/i;
const readsOnItsOwn = (prefix, text) => !DANGLING_START.test(text.slice(prefix.length).trim().split(/\s+/)[0] || '');

export function CritiqueViews({ flags, locations, rubric }) {
  const groups = [];
  for (const text of flags) {
    const previous = groups.at(-1);
    const prefix = previous && sharedPrefix(previous.prefix, text);
    if (prefix && text !== prefix && !previous.texts.includes(prefix)) {
      previous.prefix = prefix;
      previous.texts.push(text);
    } else groups.push({ prefix: text, texts: [text] });
  }
  for (let i = groups.length - 1; i >= 0; i--) {
    const { prefix, texts } = groups[i];
    if (texts.length > 1 && !texts.every(text => readsOnItsOwn(prefix, text))) {
      groups.splice(i, 1, ...texts.map(text => ({ prefix: text, texts: [text] })));
    }
  }
  const link = text => {
    const section = locations?.get(text);
    return section && <span className="ml-1.5 align-middle"><SectionAnchor section={section} /></span>;
  };
  return <ul className="space-y-3 text-[13px] leading-relaxed text-slate-800">
    {groups.map(({ prefix, texts }, index) => {
      const frame = texts.length > 1 && sharedWordFrame(prefix, texts, rubric);
      return <li key={index}>
      {prefix}{frame && ` … ${frame.term}`}{texts.length === 1 && link(texts[0])}
      {texts.length > 1 && <>
        <div className="mt-1 space-y-2 pl-2 border-l border-rose-200">
          {frame && <p className="text-[12px] font-medium text-slate-500">Scope / context · original wording</p>}
          {texts.map((text, i) => <p key={i}>
            {frame ? <><span data-frame-modifier>{frame.rows[i].modifier}</span>{frame.rows[i].modifier && ' '}
              <span aria-label={`shared term: ${frame.term}`}>…</span>{' '}<span data-frame-suffix>{frame.rows[i].suffix}</span></>
              : text.split(/\s+/).slice(prefix.split(/\s+/).length).join(' ')}{link(text)}</p>)}
        </div>
        <Disclosure summary="Original wording"><ul className="list-disc pl-5">
          {texts.map((text, i) => <li key={i}>{text}</li>)}
        </ul></Disclosure>
      </>}
    </li>; })}
  </ul>;
}
