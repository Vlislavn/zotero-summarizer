# Tests

Run `pytest -q --forked` under the repository's isolated test environment; compare
known macOS native-library failures to the unchanged baseline.

`test_cli_selected_environment.py` exercises fresh isolated interpreters against
synthetic default/selected projects. Import/invalid-input paths deny model SDKs and
network. Positive migration cases use real CLI, Settings and SQLite migrations;
only the embedding SDK symbol is inert and its constructor refuses all model
work. They verify configuration selection, not actual model compatibility.
Existing Faithbench exports retain exact defining object identity.

Part 1 retains the corresponding original Feedback suites without assertion changes.

Part 2 retains the corresponding original Feedback suites without assertion changes.

Part 3 retains the corresponding original Feedback suites without assertion changes.

Part 4 retains the corresponding original Feedback suites without assertion changes.
