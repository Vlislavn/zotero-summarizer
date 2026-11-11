"""Opt-in rendered My notes Save layout prevention; prospective canonical I test.

Source/version and served-asset binding belong to the existing parent guard and
Capture for EACH arm. The immutable wave70 witness keeps its original af7 binder.
OBSERVE and the final fragmentation oracle below are copied unchanged from it.
Collect only after parent incorporation into I/tests, with normal I conftest.
"""
import json

from tests import test_review_question_add_browser as add

browser = add.browser
helpers = add.helpers
question_browser = add.question_browser
reviewed_question_browser = add.reviewed_question_browser
pytestmark = add.pytestmark


# Read-only DOM/CSSOM/Range calls: no DOM/style/storage mutation or font/size override.
OBSERVE = r"""button => {
  const properties = ['display', 'width', 'min-width', 'max-width', 'box-sizing',
    'flex-direction', 'flex-wrap', 'flex-grow', 'flex-shrink', 'flex-basis',
    'justify-content', 'align-items', 'gap', 'column-gap', 'row-gap',
    'padding-left', 'padding-right', 'padding-top', 'padding-bottom',
    'border-left-width', 'border-right-width', 'white-space', 'overflow-wrap',
    'word-break', 'writing-mode', 'direction', 'font-family', 'font-size',
    'font-weight', 'line-height', 'letter-spacing', 'overflow', 'transform', 'zoom'];
  const rect = r => ({x: r.x, y: r.y, top: r.top, bottom: r.bottom,
    left: r.left, right: r.right, width: r.width, height: r.height});
  const css = element => {
    const style = getComputedStyle(element);
    return Object.fromEntries(properties.map(name => [name, style.getPropertyValue(name)]));
  };
  const element = node => node ? ({tag: node.tagName, id: node.id,
    classes: node.getAttribute('class'), text: node.textContent,
    rect: rect(node.getBoundingClientRect()), clientWidth: node.clientWidth,
    scrollWidth: node.scrollWidth, css: css(node)}) : null;
  const bands = rectangles => [...new Map(rectangles.map(r =>
    [`${r.top}:${r.bottom}`, {top: r.top, bottom: r.bottom}])).values()]
    .sort((a, b) => a.top - b.top || a.bottom - b.bottom);
  const visible = rectangles => rectangles.filter(r => r.width > 0 && r.height > 0);
  const ancestorNodes = [];
  for (let node = button.parentElement; node; node = node.parentElement) ancestorNodes.push(node);
  const row = button.parentElement;
  const status = button.previousElementSibling;
  const observation = {button: element(button), row: element(row), status: element(status),
    ancestors: ancestorNodes.map(node => ({tag: node.tagName, id: node.id,
      classes: node.getAttribute('class'), rect: rect(node.getBoundingClientRect()), css: css(node)})),
    viewport: {innerWidth, innerHeight, devicePixelRatio},
    fontsStatus: document.fonts.status,
    stylesheets: [...document.styleSheets].map(sheet => ({href: sheet.href, disabled: sheet.disabled})),
    resources: performance.getEntriesByType('resource').map(entry => ({name: entry.name,
      initiatorType: entry.initiatorType})), observation_error: null};
  if (!row || !status || !status.textContent.trim()) {
    observation.observation_error = 'Live notes status/row unavailable'; return observation;
  }
  const walker = document.createTreeWalker(button, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) if (walker.currentNode.data.trim()) nodes.push(walker.currentNode);
  if (nodes.length !== 1) {
    observation.observation_error = 'Expected one observable label text node'; return observation;
  }
  if ([button, ...ancestorNodes].some(node => css(node)['transform'] !== 'none') ||
      css(button)['writing-mode'] !== 'horizontal-tb') {
    observation.observation_error = 'Unsupported transformed/non-horizontal line observation'; return observation;
  }
  const node = nodes[0], start = node.data.length - node.data.trimStart().length;
  const end = node.data.trimEnd().length;
  const range = document.createRange();
  range.setStart(node, start); range.setEnd(node, end);
  observation.label_text = node.data.slice(start, end);
  observation.text_rects = [...range.getClientRects()].map(rect);
  observation.line_bands = bands(visible(observation.text_rects));
  observation.glyphs = [];
  for (let offset = start; offset < end; offset++) {
    range.setStart(node, offset); range.setEnd(node, offset + 1);
    observation.glyphs.push({offset, text: node.data[offset],
      rects: [...range.getClientRects()].map(rect)});
  }
  const glyphBands = bands(observation.glyphs.flatMap(glyph => visible(glyph.rects)));
  observation.glyph_line_bands = glyphBands;
  if (!observation.line_bands.length || observation.glyphs.some(glyph =>
      visible(glyph.rects).length !== 1) ||
      observation.line_bands.some((band, index, all) => index > 0 && band.top < all[index - 1].bottom) ||
      JSON.stringify(glyphBands) !== JSON.stringify(observation.line_bands)) {
    observation.observation_error = 'Missing/ambiguous text-node versus glyph line geometry'; return observation;
  }
  observation.line_count = observation.line_bands.length;
  observation.fragmented = observation.line_count > 1;
  return observation;
}"""


def test_today_notes_save_label_has_one_rendered_line(
    reviewed_question_browser, request, record_property,
):
    from patchright.sync_api import expect

    lab = reviewed_question_browser
    evidence = {"case": request.node.nodeid, "artifact": str(lab.artifact),
                "classification": "preparation_incomplete", "phase": "actual_today_setup",
                "case_module_path": request.node.module.__file__,
                "source_binding_owner": "existing parent guard/Capture; this field is not attestation",
                "fixture_source_receipt": str(lab.artifact / "explicit-add-loaded-source.json"),
                "cleanup_receipt_after_teardown": str(lab.artifact / "cleanup.json"),
                "boundary": "owned supplied synthetic review, real API/Chrome; no Add replay"}
    geometry = None
    try:
        row_before = add._row(lab)
        page = lab.open_browser()
        evidence["configured_viewport"] = page.viewport_size
        original = add._today_question(lab, page)
        asks = browser._post_count(lab, "/api/library/ask")
        row_after = add._row(lab)
        projection = add._snapshot(lab, lab.stable)
        questions = browser._questions(lab)
        native = add._native_rows(lab)
        evidence["fixture"] = {"submission": original, "row_before": row_before,
                               "row_after": row_after, "asks_before_geometry": asks,
                               "projection": projection, "questions": questions, "native": native}
        if asks != 1 or row_after != row_before:
            raise RuntimeError("PREPARATION_BLOCKED: admitted Today setup not preserved")
        save = page.get_by_role("button", name="Save", exact=True)
        expect(save).to_be_visible()
        evidence["phase"] = "geometry_observation"
        geometry = save.evaluate(OBSERVE)
        evidence["geometry"] = geometry
        helpers.write_json(lab.artifact / "notes-layout-geometry.json", geometry)
        record_property("notes_layout_geometry", json.dumps(geometry, sort_keys=True))
        if geometry["observation_error"]:
            raise RuntimeError("OBSERVATION_UNAVAILABLE: " + geometry["observation_error"])
        asks_after = browser._post_count(lab, "/api/library/ask")
        evidence["fixture"]["asks_after_geometry"] = asks_after
        if asks_after != asks:
            raise RuntimeError("OBSERVATION_CONTAMINATED: automatic QA count changed")
        if (add._row(lab) != row_after or add._snapshot(lab, lab.stable) != projection
                or browser._questions(lab) != questions or add._native_rows(lab) != native):
            raise RuntimeError("OBSERVATION_CONTAMINATED: persistent fixture state changed")
        expect(browser._notes_editor(page)).to_have_value(add.DIRTY)
        expect(browser._saved_questions(page).get_by_text(original["question"], exact=True)).to_be_visible()
        evidence["fixture"]["preserved_after_geometry"] = True
        lab.checkpoint("notes-layout-observed", page)
        evidence["phase"] = "single_line_oracle"
        evidence["classification"] = "measured_fragmentation" if geometry["fragmented"] else "measured_single_line"
    except Exception as error:
        evidence["classification"] = "preparation_or_observation_error"
        evidence["error"] = {"type": type(error).__name__, "message": str(error)}
        raise
    finally:
        helpers.write_json(lab.artifact / "notes-layout-observations.json", evidence)
        record_property("notes_layout_classification", evidence["classification"])
        record_property("notes_layout_phase", evidence["phase"])
        record_property("notes_layout_artifact", str(lab.artifact))
        record_property("notes_layout_case_module_path", request.node.module.__file__)
        print("NOTES_LAYOUT_WITNESS_ARTIFACT", lab.artifact)

    # This assertion alone is the wrapping rejection; setup/skip/cleanup are not RED.
    assert not geometry["fragmented"], (
        "ACTUAL_SAVE_LINE_FRAGMENTATION: text-node Range and glyphs occupy "
        f"{geometry['line_count']} distinct rendered vertical line bands; artifact={lab.artifact}"
    )
