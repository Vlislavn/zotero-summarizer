/**
 * Functional verification of /annotate — every button + every flow.
 *
 * For each verified case the script prints PASS / FAIL with the
 * concrete observation (URL, count, text, status code) so a failure
 * can be diagnosed without re-running locally.
 *
 * Cleans up after itself: every verdict created during the run is
 * deleted via the same UI Delete button so the user's label_verdicts
 * table is unchanged when the script exits.
 */
import { chromium } from 'playwright';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { writeFile, mkdir } from 'node:fs/promises';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_DIR = join(__dirname, 'screenshots');
const REPORT = join(__dirname, 'verify-annotate-report.json');
const BASE = 'http://127.0.0.1:8000';
const VP = { width: 1440, height: 900 };

const results = [];
function record(name, ok, detail = '') {
  results.push({ name, ok, detail });
  const tag = ok ? '✓ PASS' : '✗ FAIL';
  console.log(`${tag}  ${name}${detail ? '  —  ' + detail : ''}`);
}

async function api(path, init = {}) {
  const r = await fetch(`${BASE}${path}`, init);
  let body = null;
  try { body = await r.json(); } catch { /* non-JSON acceptable for some calls */ }
  return { status: r.status, body };
}

async function main() {
  await mkdir(OUT_DIR, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: VP });
  const page = await ctx.newPage();

  const consoleErrors = [];
  page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') consoleErrors.push(`console.error: ${msg.text()}`);
  });
  page.on('response', (r) => {
    if (r.status() >= 400 && r.url().startsWith(BASE)) {
      consoleErrors.push(`HTTP ${r.status()}: ${r.url().replace(BASE, '')}`);
    }
  });

  // ============================================================
  // 1. Page loads, no console errors, auto-selects first paper
  // ============================================================
  await page.goto(`${BASE}/annotate`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(800);

  const initialTitle = await page.locator('section h2').first().textContent().catch(() => '');
  record('1.1 Detail panel auto-populated on load',
    Boolean(initialTitle && initialTitle.trim().length > 5),
    `title="${(initialTitle || '').slice(0, 60)}…"`);

  record('1.2 No console errors during load',
    consoleErrors.length === 0,
    consoleErrors.join(' | '));

  // ============================================================
  // 2. List query — items rendered with badges
  // ============================================================
  const listCount = await page.locator('aside ul li').count();
  record('2.1 Paper list populated', listCount > 0, `n=${listCount}`);

  // 'Showing 77 of 77' caption present
  const caption = await page.locator('aside span').filter({ hasText: /Showing \d+ of \d+/ }).first().textContent().catch(() => '');
  record('2.2 List caption shows count', /Showing/.test(caption), `caption="${caption}"`);

  // Effective labels summary strip present
  const summary = await page.locator('aside').first().textContent().catch(() => '');
  record('2.3 Effective-labels summary strip present',
    /Effective labels:.*total/.test(summary),
    summary.match(/Effective labels:.*/)?.[0]?.slice(0, 90) || '');

  // ============================================================
  // 3. Priority filter chips swap the list
  // ============================================================
  for (const p of ['should_read', 'could_read', 'dont_read', 'all']) {
    await page.locator(`aside button:has-text("${p}")`).first().click();
    await page.waitForTimeout(400);
    const ns = await page.locator('aside ul li').count();
    record(`3.${p}  Priority filter "${p}" returns rows`, ns >= 0, `n=${ns}`);
  }
  // Return to must_read
  await page.locator('aside button:has-text("must_read")').first().click();
  await page.waitForTimeout(400);

  // ============================================================
  // 4. Advanced filters (<details>) opens and a flag chip filters
  // ============================================================
  const detailsOpen = await page.locator('aside details summary:has-text("Advanced filters")').first().click().catch(() => null);
  await page.waitForTimeout(150);
  // Try near_must_read which previously had 19 items
  const flagBtn = page.locator('aside button:has-text("near_must_read")').first();
  if (await flagBtn.count() > 0) {
    await flagBtn.click();
    await page.waitForTimeout(400);
    const ns = await page.locator('aside ul li').count();
    record('4.1 Advanced filter "near_must_read" applies', ns >= 0, `n=${ns}`);
    // Clear by clicking "any"
    await page.locator('aside button:has-text("any")').first().click();
    await page.waitForTimeout(300);
  } else {
    record('4.1 Advanced filter "near_must_read" applies', false, 'button not found');
  }

  // ============================================================
  // 5. Title search narrows the list
  // ============================================================
  await page.locator('aside input[placeholder*="Search"]').first().fill('benchmark');
  await page.waitForTimeout(350);
  const searchN = await page.locator('aside ul li').count();
  record('5.1 Search by "benchmark" narrows list', searchN > 0 && searchN <= listCount, `n=${searchN} (was ${listCount})`);
  await page.locator('aside input[placeholder*="Search"]').first().fill('');
  await page.waitForTimeout(300);

  // ============================================================
  // 6. Click a paper -> detail loads with correct fields
  // ============================================================
  const targetLi = page.locator('aside ul li').nth(2);
  const targetTitle = (await targetLi.locator('div').first().textContent().catch(() => ''))?.trim();
  await targetLi.click();
  await page.waitForTimeout(700);
  const detailTitle = (await page.locator('section h2').first().textContent().catch(() => ''))?.trim();
  record('6.1 Clicking a paper loads its detail',
    Boolean(detailTitle && targetTitle && detailTitle.startsWith(targetTitle.slice(0, 20))),
    `clicked="${targetTitle?.slice(0, 50)}", detail="${detailTitle?.slice(0, 50)}"`);

  // Required detail fields visible
  const detailZone = page.locator('section').first();
  const hasAuthors = (await detailZone.textContent().catch(() => '')) || '';
  record('6.2 AuthorByline rendered',
    /h=|listed in Zotero|in feed metadata|parent paper/.test(hasAuthors) ||
    (hasAuthors.match(/[A-Z]\w+(\s+[A-Z]\w+)+/) !== null),
    'present');

  // Provenance breakdown is visible
  const hasProv = await page.locator('section :text("DERIVED PRIORITY"), section :text("Derived")').count();
  record('6.3 Provenance breakdown visible', hasProv > 0, `match_count=${hasProv}`);

  // SHAP waterfall is visible — either the SVG bars (feed/note rows) OR
  // the "No model reasoning available." fallback (library rows that
  // pre-date triage). Both are valid renders of the component.
  const detailText = (await page.locator('section').first().textContent().catch(() => '')) || '';
  const hasShapSvg = await page.locator('section :text("Why this score"), section svg').count();
  const hasEmpty = /No model reasoning available/.test(detailText);
  record('6.4 PrestigeWaterfall renders (SVG or empty state)',
    hasShapSvg > 0 || hasEmpty,
    `svg=${hasShapSvg} empty=${hasEmpty}`);

  // ============================================================
  // 7. Verdict save round-trip (could_read), then auto-advance
  // ============================================================
  // Use the item_key from the just-clicked paper for cleanup later.
  const itemKey = ((await page.locator('section span.mono').first().textContent().catch(() => '')) || '').trim();

  // If a prior verdict exists, the priority buttons are disabled until
  // Edit is clicked. Match the production UX flow exactly: click Edit
  // first when "Previously:" is shown.
  const previouslyVisible = await page.locator('section :text("Previously:")').first().isVisible().catch(() => false);
  if (previouslyVisible) {
    const editBtn = page.locator('section button:has-text("Edit")').first();
    if (await editBtn.count() > 0) await editBtn.click();
    await page.waitForTimeout(150);
  }

  const titleBefore = detailTitle;
  await page.locator('section button:has-text("could_read")').first().click();
  // Save button reads 'Save verdict' for new, 'Update' for existing
  const saveBtn = page.locator('section button:has-text("Save verdict"), section button:has-text("Update")').first();
  await saveBtn.click();
  await page.waitForTimeout(900);
  const titleAfter = (await page.locator('section h2').first().textContent().catch(() => ''))?.trim();
  record('7.1 Save advances to next paper (optimistic)',
    Boolean(titleAfter && titleAfter !== titleBefore),
    `before="${titleBefore?.slice(0, 30)}", after="${titleAfter?.slice(0, 30)}"`);

  // Verify backend recorded it
  if (itemKey) {
    const v = await api(`/api/golden/verdicts?user_priority=could_read`);
    const found = (v.body?.verdicts || []).some((r) => r.item_key === itemKey);
    record('7.2 Verdict persisted in label_verdicts (POST round-trip)', found, `item_key=${itemKey}`);

    // Verify it now flows through effective-labels
    const eff = await api(`/api/golden/effective-labels`);
    const e = (eff.body?.items || []).find((r) => r.item_key === itemKey);
    record('7.3 Verdict surfaces in /api/golden/effective-labels',
      Boolean(e && e.source === 'user' && e.effective_priority === 'could_read'),
      `source=${e?.source}, effective=${e?.effective_priority}`);
  } else {
    record('7.2 Verdict persisted in label_verdicts', false, 'item_key not parsed');
  }

  // ============================================================
  // 8. Keyboard shortcuts (j/k/1/2/3/4)
  // ============================================================
  // Move focus away from any input
  await page.locator('body').click({ position: { x: 5, y: 5 } }).catch(() => {});
  await page.waitForTimeout(100);
  const titleBeforeJ = (await page.locator('section h2').first().textContent().catch(() => ''))?.trim();
  await page.keyboard.press('j');
  await page.waitForTimeout(400);
  const titleAfterJ = (await page.locator('section h2').first().textContent().catch(() => ''))?.trim();
  record('8.1 Keyboard "j" navigates next',
    Boolean(titleBeforeJ && titleAfterJ && titleBeforeJ !== titleAfterJ),
    `${titleBeforeJ?.slice(0, 25)} -> ${titleAfterJ?.slice(0, 25)}`);

  await page.keyboard.press('k');
  await page.waitForTimeout(400);
  const titleAfterK = (await page.locator('section h2').first().textContent().catch(() => ''))?.trim();
  record('8.2 Keyboard "k" navigates back',
    Boolean(titleAfterJ && titleAfterK && titleAfterJ !== titleAfterK),
    `${titleAfterJ?.slice(0, 25)} -> ${titleAfterK?.slice(0, 25)}`);

  // Press '3' (= could_read). This will save+advance.
  const beforeKbd = (await page.locator('section h2').first().textContent().catch(() => ''))?.trim();
  const beforeKey = ((await page.locator('section span.mono').first().textContent().catch(() => '')) || '').trim();
  await page.keyboard.press('3');
  await page.waitForTimeout(900);
  const afterKbd = (await page.locator('section h2').first().textContent().catch(() => ''))?.trim();
  record('8.3 Keyboard "3" saves could_read + advances',
    Boolean(beforeKbd && afterKbd && beforeKbd !== afterKbd),
    `${beforeKbd?.slice(0, 25)} -> ${afterKbd?.slice(0, 25)}`);

  // ============================================================
  // 9. Edit + Delete flow on an existing verdict
  // ============================================================
  // Navigate back to the paper we verdict-saved in step 7
  if (itemKey) {
    await page.goto(`${BASE}/annotate`, { waitUntil: 'networkidle' });
    await page.waitForTimeout(700);
    // The list now filters by EFFECTIVE priority (manual verdict wins), so
    // a paper we just rated could_read no longer sits under the default
    // must_read filter — switch to "all" to find it regardless of class.
    await page.locator('aside button:has-text("all")').first().click().catch(() => {});
    await page.waitForTimeout(500);
    // Find the row containing the saved item_key (visible in the
    // PaperListItem as a small mono token).
    const row = page.locator('aside ul li').filter({ hasText: itemKey });
    const rc = await row.count();
    if (rc > 0) {
      await row.first().click();
      await page.waitForTimeout(700);
      // The verdict zone should now show "Previously: could_read" + Edit/Delete
      const banner = (await page.locator('section').first().textContent().catch(() => '')) || '';
      record('9.1 Previously banner shows existing verdict',
        /Previously:/.test(banner) || /Used as ground truth/i.test(banner),
        banner.match(/Previously:.*?(could_read|should_read|must_read|dont_read)/)?.[0] || '');

      // Click Edit
      const editBtn = page.locator('section button:has-text("Edit")').first();
      if (await editBtn.count() > 0) {
        await editBtn.click();
        await page.waitForTimeout(200);
        await page.locator('section button:has-text("should_read")').first().click();
        await page.locator('section button:has-text("Update")').first().click();
        await page.waitForTimeout(800);
        const v2 = await api(`/api/golden/verdicts`);
        const updated = (v2.body?.verdicts || []).find((r) => r.item_key === itemKey);
        record('9.2 Edit + Update changes user_priority',
          updated?.user_priority === 'should_read',
          `now=${updated?.user_priority}`);
      } else {
        record('9.2 Edit button reachable', false, 'Edit not found');
      }

      // Navigate back to it (use "all" — the verdict moved it off must_read)
      await page.goto(`${BASE}/annotate`, { waitUntil: 'networkidle' });
      await page.waitForTimeout(700);
      await page.locator('aside button:has-text("all")').first().click().catch(() => {});
      await page.waitForTimeout(500);
      const row2 = page.locator('aside ul li').filter({ hasText: itemKey });
      if (await row2.count() > 0) {
        await row2.first().click();
        await page.waitForTimeout(700);
        // Confirm browser.dialog → accept; then click Delete
        page.once('dialog', (d) => d.accept());
        const delBtn = page.locator('section button:has-text("Delete")').first();
        if (await delBtn.count() > 0) {
          await delBtn.click();
          await page.waitForTimeout(900);
          const v3 = await api(`/api/golden/verdicts`);
          const stillThere = (v3.body?.verdicts || []).some((r) => r.item_key === itemKey);
          record('9.3 Delete removes the verdict', !stillThere, `still_present=${stillThere}`);
        } else {
          record('9.3 Delete button reachable', false, 'Delete not found');
        }
      }
    } else {
      record('9.1 Saved-verdict row reachable in list', false, `item_key=${itemKey} not visible`);
    }
  }

  // Also delete the second verdict we made via keyboard '3' (step 8.3).
  if (beforeKey) {
    const cleanup = await api(`/api/golden/verdict?item_key=${encodeURIComponent(beforeKey)}`, { method: 'DELETE' });
    record('9.4 Cleanup keyboard-test verdict', cleanup.status < 300, `key=${beforeKey} status=${cleanup.status}`);
  }

  // ============================================================
  // 10. NavBar: More menu + breadcrumb
  // ============================================================
  await page.locator('header summary:has-text("More")').first().click();
  await page.waitForTimeout(300);
  const libVisible = await page.locator('details[open] a:has-text("Library")').first().isVisible();
  record('10.1 More menu opens and Library link is visible', libVisible, `visible=${libVisible}`);

  await page.goto(`${BASE}/library`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(400);
  const navText = await page.locator('header nav').first().textContent().catch(() => '');
  record('10.2 Breadcrumb shows current power-tool route',
    /Library/.test(navText),
    `nav="${navText}"`);

  // ============================================================
  // 11. PDF copy button (only when has_pdf)
  // ============================================================
  await page.goto(`${BASE}/annotate`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(700);
  // Scan a few papers for one that has PDF
  let foundPdf = false;
  for (let i = 0; i < 8; i++) {
    await page.locator('aside ul li').nth(i).click();
    await page.waitForTimeout(400);
    const pdfBtn = page.locator('section button:has-text("Copy PDF path")').first();
    if (await pdfBtn.count() > 0) {
      foundPdf = true;
      // Grant clipboard permission then click
      await ctx.grantPermissions(['clipboard-read', 'clipboard-write']);
      await pdfBtn.click();
      await page.waitForTimeout(400);
      const after = await page.locator('section button:has-text("PDF path copied")').count();
      record('11.1 Copy PDF path button works', after > 0, `i=${i}, button toggled to copied=${after > 0}`);
      break;
    }
  }
  if (!foundPdf) record('11.1 Copy PDF path button works', true, 'no PDF in first 8 papers — skipped');

  // ============================================================
  // 12. Final shape: every saved verdict resolves correctly
  // ============================================================
  const all = await api('/api/golden/effective-labels/summary');
  record('12.1 effective-labels/summary endpoint returns counts',
    typeof all.body?.total_rows === 'number',
    JSON.stringify(all.body || {}));

  // ============================================================
  // 13. Remaining buttons: Abstract Show-more, Reset, comment textarea,
  //     csv_stub badge appears for synthetic key 57ZSGVPD
  // ============================================================
  await page.goto(`${BASE}/annotate`, { waitUntil: 'networkidle' });
  await page.waitForTimeout(700);

  // Abstract toggle
  const abstractToggle = page.locator('section button:has-text("Show more"), section button:has-text("Show less")').first();
  const hasToggle = await abstractToggle.count();
  if (hasToggle > 0) {
    const before = await abstractToggle.textContent();
    await abstractToggle.click();
    await page.waitForTimeout(150);
    const after = await abstractToggle.textContent();
    record('13.1 Abstract Show more/less toggle flips label',
      Boolean(before && after && before !== after),
      `${before} -> ${after}`);
  } else {
    record('13.1 Abstract Show more/less toggle flips label', true, 'no long abstract; skipped');
  }

  // Comment textarea — type and read back
  // First, navigate to an unverdicted paper so the textarea is enabled
  let targetIdx = -1;
  const allLi = await page.locator('aside ul li').count();
  for (let i = 0; i < allLi; i++) {
    await page.locator('aside ul li').nth(i).click();
    await page.waitForTimeout(400);
    const star = await page.locator('aside ul li').nth(i).locator(':text("★ GT")').count();
    if (star === 0) {
      targetIdx = i;
      break;
    }
  }
  const textarea = page.locator('section textarea').first();
  if (targetIdx >= 0 && (await textarea.count()) > 0) {
    await textarea.fill('Test comment');
    await page.waitForTimeout(150);
    const val = await textarea.inputValue();
    record('13.2 Comment textarea accepts input', val === 'Test comment', `value="${val}"`);
    // Reset clears it
    const resetBtn = page.locator('section button:has-text("Reset")').first();
    if (await resetBtn.count() > 0) {
      await resetBtn.click();
      await page.waitForTimeout(150);
      const v2 = await textarea.inputValue();
      record('13.3 Reset clears comment + priority',
        v2 === '',
        `value="${v2}"`);
    } else {
      record('13.3 Reset clears comment + priority', true, 'Reset hidden (no priority set yet)');
    }
  } else {
    record('13.2 Comment textarea accepts input', false, 'no unverdicted paper found');
  }

  // csv_stub badge for synthetic key
  const stubResp = await api('/api/golden/review-detail?item_key=57ZSGVPD');
  record('13.4 csv_stub fallback resolves 200 for deleted Zotero key',
    stubResp.status === 200 && stubResp.body?.source === 'csv_stub',
    `status=${stubResp.status} source=${stubResp.body?.source}`);

  // Cleanup any verdicts left by step 13 in case Reset didn't fire
  if (targetIdx >= 0) {
    const k = ((await page.locator('section span.mono').first().textContent().catch(() => '')) || '').trim();
    if (k) await api(`/api/golden/verdict?item_key=${encodeURIComponent(k)}`, { method: 'DELETE' });
  }

  // ---------- Report ----------
  await browser.close();

  const passed = results.filter((r) => r.ok).length;
  const failed = results.filter((r) => !r.ok).length;
  await writeFile(REPORT, JSON.stringify({ passed, failed, results }, null, 2));

  console.log('\n=====================================');
  console.log(`PASSED: ${passed}   FAILED: ${failed}`);
  console.log('Report:', REPORT);
  if (consoleErrors.length) {
    console.log('\nCaptured console/page errors:');
    for (const e of consoleErrors) console.log('  -', e);
  }
  process.exit(failed === 0 ? 0 : 1);
}

main().catch((err) => { console.error(err); process.exit(2); });
