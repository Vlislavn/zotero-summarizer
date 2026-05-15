/**
 * Pre-implementation UX audit of /annotate/ (Phase 1.18 Step 1).
 *
 * Drives a realistic batch-labeling flow against the running server at
 * http://127.0.0.1:8000/annotate/ and records:
 *
 *   - screenshots of each step (saved next to this script)
 *   - timing of every user-perceived action (Doherty threshold = 400 ms)
 *   - observed friction points
 *
 * Run with: cd tests/e2e && node audit.mjs
 * Output: ./screenshots/*.png + ./audit-report.json
 */

import { chromium } from 'playwright';
import { writeFile, mkdir } from 'node:fs/promises';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(__dirname, 'screenshots');
const REPORT_PATH = join(__dirname, 'audit-report.json');
const BASE_URL = 'http://127.0.0.1:8000/annotate/';
const DOHERTY_MS = 400;

/** One observed step. */
function step(name, timeMs, observations = []) {
  return { name, time_ms: timeMs, observations };
}

async function timed(fn) {
  const t0 = Date.now();
  const result = await fn();
  return { result, ms: Date.now() - t0 };
}

async function main() {
  await mkdir(OUT_DIR, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();

  const friction = [];
  const steps = [];

  // ---------- Step 1: load /annotate/ ----------
  const load = await timed(async () => {
    await page.goto(BASE_URL, { waitUntil: 'networkidle' });
  });
  await page.screenshot({ path: join(OUT_DIR, '01-initial-load.png'), fullPage: true });
  if (load.ms > DOHERTY_MS * 2) {
    friction.push(`Initial load took ${load.ms} ms (>${DOHERTY_MS * 2} ms threshold)`);
  }
  steps.push(step('Initial page load', load.ms, []));

  // Confirm the React shell rendered
  const hasH2 = await page.locator('h2').first().textContent();
  if (!hasH2 || !/papers/i.test(hasH2)) {
    friction.push('React shell did not render the expected "Papers" heading');
  }

  // ---------- Step 2: observe what filters are shown ----------
  const priorityChips = await page.locator('button').filter({ hasText: /^(must_read|should_read|could_read|dont_read|all)$/ }).count();
  const flagChips = await page.locator('button').filter({ hasText: /^(any|weak_must_read|near_must_read|manual_override)$/ }).count();
  steps.push(step('Filter chips visible', 0, [
    `${priorityChips} priority chips`, `${flagChips} flag chips`,
  ]));
  if (priorityChips + flagChips > 7) {
    friction.push(`${priorityChips + flagChips} filter chips visible at once — possible Hick's Law / Choice Overload`);
  }

  // ---------- Step 3: select the first paper ----------
  // The default filter is must_read; the list should already be populated.
  const listItemCount = await page.locator('aside ul li').count();
  steps.push(step('List populated', 0, [`${listItemCount} items in list`]));
  if (listItemCount === 0) {
    friction.push('Paper list is empty under default filter — would need to change filter');
  }

  // Click the first list item that looks like a paper card (li, not a status message)
  const clickStart = Date.now();
  await page.locator('aside ul li button, aside ul li > div[role="button"], aside ul li').first().click({ timeout: 5000 }).catch(() => {});
  // Wait for the detail panel to populate (heading "Tags" appears once loaded)
  let detailLoaded = false;
  try {
    await page.waitForSelector('h3:has-text("Tags")', { timeout: 5000 });
    detailLoaded = true;
  } catch (_) {}
  const clickToDetail = Date.now() - clickStart;
  await page.screenshot({ path: join(OUT_DIR, '02-paper-selected.png'), fullPage: true });
  if (!detailLoaded) {
    friction.push('Clicking the first paper did not load the detail panel within 5 s');
  } else {
    steps.push(step('Click paper → detail loaded', clickToDetail, []));
    if (clickToDetail > DOHERTY_MS) {
      friction.push(`Paper-detail load took ${clickToDetail} ms (>${DOHERTY_MS} ms Doherty threshold) — verdict batch mode would feel laggy`);
    }
  }

  // ---------- Step 4: measure the visible-content geometry ----------
  // Sticky verdict panel? Capture the verdict's bounding box and the viewport scroll position.
  const verdictBox = await page.locator('text=Your verdict').first().boundingBox().catch(() => null);
  if (verdictBox) {
    const vp = page.viewportSize();
    const isAboveFold = verdictBox.y < vp.height;
    if (!isAboveFold) {
      friction.push(`Verdict panel sits below the fold at y=${Math.round(verdictBox.y)} (viewport ${vp.height} px) — user must scroll to reach it. Fitts's Law: distant target.`);
    }
    steps.push(step('Verdict panel position', 0, [
      `y=${Math.round(verdictBox.y)} px (viewport ${vp.height})`,
      isAboveFold ? 'visible without scroll' : 'BELOW THE FOLD',
    ]));
  } else {
    friction.push('Verdict panel not found on the selected paper');
  }

  // ---------- Step 5: cast a verdict + observe save latency ----------
  if (detailLoaded && verdictBox) {
    // Scroll to verdict panel
    await page.locator('text=Your verdict').first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: join(OUT_DIR, '03-verdict-panel.png'), fullPage: false });

    // Capture the selected paper's title BEFORE saving so we can detect
    // whether the optimistic advance landed us on a new paper.
    const titleBeforeSave = await page.locator('section h2').first().textContent().catch(() => '');

    // Click the could_read button
    await page.locator('button:has-text("could_read")').last().click().catch(() => {});
    await page.locator('textarea').first().fill('Pre-audit: testing the save flow under Playwright').catch(() => {});

    // Optimistic-advance contract (post-bundle): the UI changes immediately
    // when Save is clicked — no waiting on the network. We measure how long
    // it takes for the title to change instead of waiting on a "Previously:"
    // banner on the same paper (which won't exist once we've advanced).
    const saveStart = Date.now();
    await page.locator('button:has-text("Save")').first().click().catch(() => {});
    let saveMs = 0;
    let advanced = false;
    try {
      await page.waitForFunction((before) => {
        const h = document.querySelector('section h2');
        return h && h.textContent && h.textContent !== before;
      }, titleBeforeSave, { timeout: 3000 });
      saveMs = Date.now() - saveStart;
      advanced = true;
    } catch (_) {
      saveMs = Date.now() - saveStart;
    }
    await page.screenshot({ path: join(OUT_DIR, '04-after-save.png'), fullPage: true });

    if (!advanced) {
      friction.push('Click-Save did NOT advance to a new paper within 3 s — optimistic auto-advance regressed.');
    } else {
      steps.push(step('Save → optimistic advance', saveMs, ['next paper visible']));
      if (saveMs > DOHERTY_MS) {
        friction.push(`Optimistic advance took ${saveMs} ms (>${DOHERTY_MS} ms Doherty threshold).`);
      }
    }

    // ---------- Step 7: keyboard shortcuts ----------
    // First make sure focus is not in a textarea/input (post-save the form
    // may have re-rendered for the new paper; click the body to be safe).
    await page.locator('body').click({ position: { x: 10, y: 10 } }).catch(() => {});
    const titleBeforeJ = await page.locator('section h2').first().textContent().catch(() => '');
    await page.keyboard.press('j');
    await page.waitForTimeout(300);
    const titleAfterJ = await page.locator('section h2').first().textContent().catch(() => '');
    const jWorks = Boolean(titleBeforeJ && titleAfterJ && titleBeforeJ !== titleAfterJ);

    // Press '3' for could_read verdict on the new paper.
    const titleBefore3 = titleAfterJ;
    await page.keyboard.press('3');
    await page.waitForTimeout(500);
    const titleAfter3 = await page.locator('section h2').first().textContent().catch(() => '');
    const threeWorks = Boolean(titleBefore3 && titleAfter3 && titleBefore3 !== titleAfter3);

    if (!jWorks) {
      friction.push('Pressing "j" did not change the selected paper — keyboard navigation not wired up.');
    }
    if (!threeWorks) {
      friction.push('Pressing "3" did not advance to the next paper — keyboard verdict shortcut not wired up.');
    }
    steps.push(step('Keyboard shortcuts (j/k/1-4)', 0, [
      `j: ${jWorks ? 'navigated' : 'no change'}`,
      `3: ${threeWorks ? 'saved + advanced' : 'no change'}`,
    ]));

    // Clean up: walk back to the test paper and delete its verdict. Without
    // this the post-audit leaves test rows in label_verdicts. We may have
    // jumped 2 papers ahead, so navigate back via k.
    await page.keyboard.press('k').catch(() => {});
    await page.waitForTimeout(150);
    await page.keyboard.press('k').catch(() => {});
    await page.waitForTimeout(150);
    page.once('dialog', d => d.accept());
    await page.locator('button:has-text("Delete")').first().click().catch(() => {});
    await page.waitForTimeout(500);
  }

  await page.screenshot({ path: join(OUT_DIR, '05-end-state.png'), fullPage: true });

  // ---------- Step 8: inspect scrolling behaviour with a paper that has many annotations ----------
  // BALAR known-good test item with 3 annotations + 2 notes
  await page.goto(BASE_URL, { waitUntil: 'networkidle' });
  await page.waitForTimeout(500);
  // Find BALAR in the list via search
  await page.locator('input[placeholder*="Search"]').first().fill('BALAR').catch(() => {});
  await page.waitForTimeout(500);
  await page.locator('aside ul li').first().click().catch(() => {});
  await page.waitForSelector('h3:has-text("Annotations")', { timeout: 5000 }).catch(() => {});
  await page.screenshot({ path: join(OUT_DIR, '06-BALAR-top.png'), fullPage: false });

  // Scroll the right-column container to the bottom. The new layout makes
  // the <section class="... overflow-y-auto"> the scroll container; the body
  // itself doesn't scroll. We scroll any candidate container.
  await page.evaluate(() => {
    const section = document.querySelector('section.glass.overflow-y-auto') ||
                    document.querySelector('section.glass');
    if (section) section.scrollTo(0, section.scrollHeight);
    window.scrollTo(0, document.body.scrollHeight);
  });
  await page.waitForTimeout(150);
  await page.screenshot({ path: join(OUT_DIR, '07-BALAR-bottom-scrolled.png'), fullPage: false });

  const verdictAfterScroll = await page.locator('text=Your verdict').first().boundingBox().catch(() => null);
  if (verdictAfterScroll) {
    const vp = page.viewportSize();
    const isVisible = verdictAfterScroll.y >= 0 && verdictAfterScroll.y < vp.height;
    if (!isVisible) {
      friction.push(`After scrolling to the bottom of BALAR's content, verdict panel is at y=${Math.round(verdictAfterScroll.y)} (off-screen). Not sticky.`);
    } else {
      steps.push(step('Verdict panel after deep scroll', 0, [
        `y=${Math.round(verdictAfterScroll.y)} px — visible (sticky-bottom working)`,
      ]));
    }
  }

  await browser.close();

  // ---------- Report ----------
  const report = {
    base_url: BASE_URL,
    steps,
    friction_points: friction,
    summary: {
      total_steps: steps.length,
      friction_count: friction.length,
      slow_actions_over_doherty: steps.filter(s => s.time_ms > DOHERTY_MS).map(s => s.name),
    },
  };
  await writeFile(REPORT_PATH, JSON.stringify(report, null, 2));
  console.log(`\n=== AUDIT REPORT ===\n`);
  console.log(`Screenshots in: ${OUT_DIR}/`);
  console.log(`Report: ${REPORT_PATH}\n`);
  console.log(`Friction points (${friction.length}):`);
  friction.forEach((f, i) => console.log(`  ${i + 1}. ${f}`));
  console.log(`\nSlow actions (>${DOHERTY_MS} ms Doherty threshold):`);
  report.summary.slow_actions_over_doherty.forEach(n => console.log(`  - ${n}`));
}

main().catch(err => {
  console.error('Audit failed:', err);
  process.exit(1);
});
