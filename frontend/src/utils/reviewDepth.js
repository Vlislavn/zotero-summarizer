// Review-depth options. One choice for how carefully the next paper review runs. The costs are measured on the
// owner's bench papers (data/perf-20261107/speed reports); no raw model knobs are exposed.
export const REVIEW_DEPTHS = {
  thorough: {
    label: 'Thorough', summary: 'Thorough · the model reasons before writing and quality checks run 3 times',
    change: 'Slowest, most stable grades.',
  },
  fast: {
    label: 'Fast', summary: 'Fast · about 2× quicker, lower quality: grades and findings can change, more reviews fail',
    change: 'About 2× quicker, lower quality. The model writes without reasoning first and quality checks run once.',
    measured: 'Fast, measured on 4 papers: the writing step took 50–75% less time and quality checks used half the calls, but wording changed on 3 of 4 papers, 1 failed verification and 1 grade changed.',
  },
};
