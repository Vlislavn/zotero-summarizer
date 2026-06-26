# DESIGN.md — "Ease Health" (light clinical)

Adopted design language for the whole frontend (React app + the standalone paper
brief). Sourced from Refero Styles ("Ease Health — sunlit clinic on white linen")
and adapted to this app's stack. Chosen for a local-first clinical paper-triage
tool: calm, trustworthy, flat, one saturated green.

- Reference: <https://styles.refero.design/style/e9f5e976-53f7-42f5-a882-4e63b3c2f734>

## Principles (the "don'ts" are load-bearing)
- **Never pure white.** Canvas is Linen White `#fffefc`. `white` is remapped to it.
- **No drop shadows.** Elevation comes from *surface tint* (Linen → Mint → Sage) and
  hairline borders only.
- **No bold (600+).** Display sizes use light weight (300); UI uses 400/500. New or
  touched code must not reach for `font-bold`. (Existing `font-semibold` is a Phase-2
  cleanup, not a hard break.)
- **One saturated color.** Forest Ink `#0f3e17` is reserved for actions, headings,
  links, and icons. Everything else is tint or neutral.
- **No intermediate radii (8/12px).** Only 7px (nav/inputs/buttons) and 14px
  (cards/buttons); 999px pills.

## Palette
| Token | Hex | Role |
|---|---|---|
| Forest Ink | `#0f3e17` | the one saturated color — actions, headings, links |
| Sage | `#b1dbb8` | soft green emphasis backgrounds |
| Mint Veil | `#cfe7d3` | subtle card tint |
| Linen | `#e1f4df` | lightest green card surface |
| Mist Blue | `#b6ced5` | cool counterpoint — hero panels, prestige/info badges |
| Linen White | `#fffefc` | warm canvas (never pure white) |
| Hairline | `#e5e7eb` | borders, dividers (1px) |
| Charcoal | `#222222` | nav links |
| Graphite | `#333333` | secondary borders |
| Ink | `#171614` | body text (warm near-black) |

## Type
- **Display:** `Fraunces` (optical serif, weight 300–400) — faithful free substitute for
  Ease Health's *Faire Octave* (commercial; we are license-clean / offline-capable).
  Headings, the verdict word, eyebrows. Light weight only.
- **UI / body / data:** `Inter` (300/400/500) — free substitute for *Suisse Int'l*. Use
  `tabular-nums` for measurements (the app has no monospace; Inter carries the data).
- Scale (1.2 minor third, 16px base): display 40/56/74 · body 16–18 · caption 12.
- Tracking: tight (`-0.02em`) on UI text.

## How it's wired (so the whole app inherits it from few files)
- `tailwind.config.js` — Tailwind's `slate`/`teal`/`emerald`/`sky`/`amber`/`rose`/
  `indigo`/`violet` ramps and `white` are **remapped** to Ease Health hues, so every
  existing utility class (and `tones.js`, the shared chip/band vocabulary) renders in
  the system with no per-component rewrite. New code should prefer the semantic names
  `forest` / `sage` / `mint` / `mist` / `linen`. Radii → 7/14/999; shadows → flat;
  fonts → Inter (`sans`) + Fraunces (`display`).
- `src/index.css` — flat Linen-White canvas (the radial gradient is gone), Inter base,
  brand CSS vars set to Ease Health.
- `index.html` — loads Fraunces + Inter (Google Fonts; degrades to serif/system).
- The standalone brief (`services/library/_paper_read_*.py`) sets the same palette as
  CSS vars: Forest Ink replaces the verdict/gauge/tether accent, the "stain" wash is
  Sage/Mint, shadows off. Its stain→tether→gauge structure is unchanged; only the skin.

## Semantic mapping (clinical, no alarmist red)
`teal/emerald` → forest/sage greens (accent, success, highlight) · `sky/indigo/violet`
→ Mist-Blue cool (info, B-grade, prestige) · `amber` → soft ochre (caution, C-grade,
skim) · `rose` → muted clay (the strongest signal — FLAG/D — noticeable but calm, never
a fire-engine red).
