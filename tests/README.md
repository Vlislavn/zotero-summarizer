# Current part 2 coverage

This checkout includes retained history/analysis tests plus incremental CSV,
UTC-overflow, journal-source and SQL-identity regressions. The part-1 paragraph
below is the prerequisite #162 coverage record, not this checkout's suite limit.
Operation/weekly suites remain later-stack work.

# Tests

Run `pytest -q --forked` under the repository's isolated test environment; compare
known macOS native-library failures to the unchanged baseline.

Part 1 retains the original Feedback capture assertions and adds duplicate
migration-namespace refusal cases. `test_feedback_analysis_report_primitives.py`
keeps recorded Markdown content inside literal code spans; downstream
analysis/operation suites are not present in this primitive-only checkout.

`test_cli_selected_environment.py` exercises fresh isolated interpreters against
synthetic default/selected projects. Import/invalid-input paths deny model SDKs and
network. Positive migration cases use real CLI, Settings and SQLite migrations;
only the embedding SDK symbol is inert and its constructor refuses all model
work. They verify configuration selection, not actual model compatibility.
Existing Faithbench exports retain exact defining object identity. Default-root
redirect cases cover shell/cwd admission and the server factory handoff. The import
guard also denies the direct Anthropic SDK; subprocess safety defaults to 10 seconds,
with `ZS_CLI_TEST_WALL_SECONDS` overrides capped at 60 seconds.
