// One decision vocabulary for every surface where the owner decides on a paper
// (Today card, paper-page sticky verdict bar). Everything is English (frontend/README.md
// language rule; only Telegram summaries keep their chat language); reason ids are the backend's
// canonical set (api/routes/_daily_verdict.py), shared by Today, the paper page and Telegram.

// The one chip style of the «Your feedback» group (PostFeedback.jsx): 13 px, regular weight, ≥44 px tall.
export const chipClass = (on) => `min-h-11 rounded-lg border px-3 text-[13px] disabled:opacity-50 ${
  on ? 'border-forest-800 bg-forest-800 text-white' : 'border-sage-300 bg-white text-slate-700 hover:bg-slate-100'}`;

// The ids are the backend's canonical negative set (api/routes/_daily_verdict.py); the Today
// card's skip block uses the same ids/labels (unifying them is Todo #108).
export const NEG_CHIPS = [
  ['not_relevant', 'off topic'], ['low_quality', 'weak quality'], ['no_code', 'no code'],
  ['unclear_value', 'unclear value'], ['too_hard', 'too hard'],
  ['boring', 'boring / nothing new'], ['non_credible', 'low-credibility group'],
];

export const DECISIONS = [['must_read', 'must'], ['should_read', 'should'], ['could_read', 'could'], ['dont_read', 'skip']];
export const DECISION_WORD = { must_read: 'must', should_read: 'should', could_read: 'could', dont_read: 'skip' };
export const POS_CHIPS = [['has_takeaway', 'worth taking'], ['good_figures', 'good figures / methods']];

// Graded tints for the verdict (#124): the stronger the verdict, the stronger the
// Forest — must Forest solid → should Sage → could Mint → skip a hairline outline.
// Never near-black. DECISION_TINT is the resting look (also the recommendation pill);
// DECISION_HOVER only for buttons.
export const DECISION_TINT = {
  must_read: 'border-forest-800 bg-forest-800 text-white',
  should_read: 'border-sage-300 bg-sage-200 text-forest-800',
  could_read: 'border-sage-200 bg-sage-100 text-forest-800',
  dont_read: 'border-slate-300 bg-white text-slate-700',
};
// Compact content-sized pill (#127): 40 px on phones, 32 px on desktop; the invisible
// ::before grows the phone tap target to 44 px without growing the look.
export const DECISION_BTN = 'relative inline-flex h-10 items-center justify-center gap-1.5 rounded-lg border px-4 text-[13px] font-medium transition-colors before:absolute before:inset-x-0 before:-inset-y-0.5 disabled:opacity-40 sm:h-8 sm:before:inset-y-0';
export const DECISION_HOVER = {
  must_read: 'hover:bg-forest-700',
  should_read: 'hover:bg-sage-300',
  could_read: 'hover:bg-sage-200',
  dont_read: 'hover:border-rose-500 hover:text-rose-700',
};
