/** @type {import('tailwindcss').Config} */

// "Ease Health" design system (see DESIGN.md). We REMAP Tailwind's built-in ramps
// to Ease Health hues so every existing utility class — and tones.js, the shared
// chip/band vocabulary — renders in the system with no per-component rewrite.
// New code should prefer the semantic names: forest / sage / mint / mist / linen.
const forest = {
  50: '#eef6f0', 100: '#dcecdf', 200: '#c2dec8', 300: '#9fcaa9', 400: '#6aa97b',
  500: '#418455', 600: '#2b6740', 700: '#1b4e2c', 800: '#123f1d', 900: '#0c2c14', 950: '#07200d',
};
const sage = {
  50: '#e9f6ec', 100: '#d2ecd7', 200: '#b1dbb8', 300: '#8ccb99', 400: '#5cae72',
  500: '#389455', 600: '#2a7644', 700: '#225f38', 800: '#1a4b2d', 900: '#123821', 950: '#0c2616',
};
const mist = {
  50: '#eef4f6', 100: '#dde9ed', 200: '#c2d7de', 300: '#9dbdc8', 400: '#6f9aa9',
  500: '#4c7d8d', 600: '#3c6675', 700: '#325563', 800: '#2b454f', 900: '#1f333b', 950: '#142127',
};
const ochre = {
  50: '#f8f1e4', 100: '#efe1c6', 200: '#e3cda0', 300: '#d4b274', 400: '#c0934a',
  500: '#a5772f', 600: '#876026', 700: '#6c4d20', 800: '#57401f', 900: '#49371d', 950: '#2a1f0f',
};
const clay = {
  50: '#f7ece8', 100: '#f0d8d0', 200: '#e4b8aa', 300: '#d4937f', 400: '#c16f57',
  500: '#a8543c', 600: '#8c4330', 700: '#723829', 800: '#5f3025', 900: '#512b22', 950: '#2c1612',
};
const neutral = {
  50: '#f7f6f2', 100: '#efeee8', 200: '#e4e2da', 300: '#d2d0c6', 400: '#a9a79c',
  500: '#7c7a70', 600: '#585650', 700: '#3e3c37', 800: '#2a2925', 900: '#1a1815', 950: '#100f0d',
};

export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        white: '#fffefc', // never pure white — Linen White canvas/cards
        // semantic names for new code
        forest, sage, mist, linen: '#fffefc',
        // remapped built-ins (existing utilities inherit Ease Health)
        slate: neutral, gray: neutral, zinc: neutral, stone: neutral,
        teal: forest, green: sage, emerald: sage,
        sky: mist, cyan: mist, blue: mist, indigo: mist, violet: mist, purple: mist,
        amber: ochre, yellow: ochre, orange: ochre,
        rose: clay, red: clay, pink: clay,
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'sans-serif'],
        display: ['Fraunces', 'Newsreader', 'Georgia', 'serif'],
      },
      borderRadius: {
        DEFAULT: '7px', sm: '4px', md: '7px', lg: '7px', xl: '14px', '2xl': '14px', '3xl': '18px',
      },
      // No drop shadows — elevation via surface tint + hairline border (focus rings keep working).
      boxShadow: {
        sm: 'none', DEFAULT: 'none', md: 'none', lg: 'none', xl: 'none', '2xl': 'none',
      },
    },
  },
  plugins: [],
};
