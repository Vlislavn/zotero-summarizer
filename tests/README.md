# Tests

Run `pytest -q --forked` under the repository's isolated test environment; compare
known macOS native-library failures to the unchanged baseline.

`test_cli_selected_environment.py` exercises fresh isolated interpreters against
synthetic default/selected projects. Import/invalid-input paths deny model SDKs and
network. Positive migration cases use real CLI, Settings and SQLite migrations;
only the embedding SDK symbol is inert and its constructor refuses all model
work. They verify configuration selection, not actual model compatibility.
Existing Faithbench exports retain exact defining object identity.

Part 1 retains the original Feedback capture assertions and adds duplicate
migration-namespace refusal cases. `test_feedback_analysis_report_primitives.py`
keeps recorded Markdown content inside literal code spans; downstream
analysis/operation suites are not present in this primitive-only checkout.
