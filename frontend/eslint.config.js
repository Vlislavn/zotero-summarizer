import js from '@eslint/js';
import globals from 'globals';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';

export default [
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
      // Allow intentionally-unused capitalised/underscore names (e.g. _unused).
      'no-unused-vars': ['warn', { varsIgnorePattern: '^[A-Z_]', argsIgnorePattern: '^_' }],
    },
  },
  {
    // Complexity/size parity with the Python gates: a worklist, never a gate (warn only).
    files: ['**/*.{js,jsx}'],
    ignores: ['**/*.test.{js,jsx}', 'src/testSetup.js', '**/__tests__/**'],
    rules: {
      complexity: ['warn', 15],
      'max-params': ['warn', 6],
      'max-depth': ['warn', 5],
      'max-lines-per-function': ['warn', { max: 88, skipBlankLines: true, skipComments: true }],
      'no-eval': 'warn',
      'no-throw-literal': 'warn',
    },
  },
  {
    files: ['**/*.test.{js,jsx}'],
    languageOptions: { globals: { ...globals.node } },
  },
];
