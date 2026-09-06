"""Invalid CLI budgets fail before Settings, stores, encoders or providers."""

from unittest.mock import Mock

import pytest

from zotero_summarizer.cli import main
from zotero_summarizer.cli import _goldenset, _goldenset_classify, _goldenset_predict
from zotero_summarizer.settings import Settings
from tests.test_cli_hybrid_training import dataset  # noqa: F401 (shared isolated project fixture)


@pytest.fixture
def dispatch(tmp_path, monkeypatch):
    settings = Settings.load(project_root=tmp_path)
    load = Mock(return_value=settings)
    handler = Mock(return_value=0)
    monkeypatch.setattr(Settings, "load", load)
    for module, names in [
        (_goldenset, ["export", "train_classifier", "eval_baseline", "tune", "suggest_labels"]),
        (_goldenset_classify, ["classify", "classify_llm"]),
        (_goldenset_predict, ["predict_feed", "analyze_notes"]),
    ]:
        for name in names:
            monkeypatch.setattr(module, f"_goldenset_{name}", handler)
    return load, handler


INTEGER_BOUNDS = [
    ("export", "abstract-chars", 0),
    ("train-classifier", "folds", 2), ("train-classifier", "pca-dim", 1),
    ("eval-baseline", "n-repeats", 1), ("eval-baseline", "n-folds", 2),
    ("eval-baseline", "n-bootstrap", 1), ("eval-baseline", "pca-dim", 1),
    ("eval-baseline", "seed", 0),
    ("tune", "n-trials", 1), ("tune", "n-folds", 2), ("tune", "seed", 0),
    ("suggest-labels", "top-k", 1),
    ("predict-feed", "limit", 1), ("predict-feed", "folds", 2), ("predict-feed", "pca-dim", 1),
    ("classify", "folds", 2), ("classify", "pca-dim", 1),
    ("classify-llm", "limit", 1), ("classify-llm", "workers", 1),
    ("analyze-notes", "limit", 1), ("analyze-notes", "min-chars", 0), ("analyze-notes", "max-chars", 1),
]


@pytest.mark.parametrize("command,option,minimum", INTEGER_BOUNDS)
def test_integer_boundary_rejects_before_dispatch_and_accepts_minimum(dispatch, capsys, command, option, minimum):
    load, handler = dispatch
    args = ["goldenset", command]
    if option == "max-chars":
        args += ["--min-chars", "0"]
    with pytest.raises(SystemExit) as error:
        main([*args, f"--{option}", str(minimum - 1)])
    assert error.value.code == 2
    assert f"--{option}" in capsys.readouterr().err
    load.assert_not_called()
    handler.assert_not_called()

    assert main([*args, f"--{option}", str(minimum)]) == 0
    load.assert_called_once()
    handler.assert_called_once()
    assert getattr(handler.call_args.args[0], option.replace("-", "_")) == minimum


@pytest.mark.parametrize("value", ["-0.2", "1", "2", "nan", "inf", "-inf"])
def test_invalid_holdout_is_not_silently_disabled(dispatch, capsys, value):
    load, handler = dispatch
    with pytest.raises(SystemExit) as error:
        main(["goldenset", "classify", f"--holdout-fraction={value}"])
    assert error.value.code == 2
    assert "--holdout-fraction" in capsys.readouterr().err
    load.assert_not_called()
    handler.assert_not_called()


@pytest.mark.parametrize("value", ["", "nan", "inf", "0", "1.1", "0.8,0.2", "0.5,0.5", "0.1,", "text"])
def test_invalid_learning_fractions_fail_before_data_loading(dispatch, capsys, value):
    load, handler = dispatch
    with pytest.raises(SystemExit) as error:
        main(["goldenset", "eval-baseline", "--learning-curve", f"--learning-curve-fractions={value}"])
    assert error.value.code == 2
    assert "--learning-curve-fractions" in capsys.readouterr().err
    load.assert_not_called()
    handler.assert_not_called()


@pytest.mark.parametrize("command,options,flag", [
    ("analyze-notes", ["--min-chars", "500", "--max-chars", "100"], "--min-chars"),
    ("analyze-notes", ["--max-chars", "100", "--min-chars", "500"], "--min-chars"),
    ("eval-baseline", ["--learning-curve-fractions", "0.5,1"], "--learning-curve"),
    ("eval-baseline", ["--seed", str(2**32 - 1), "--n-repeats", "2"], "--seed"),
    ("tune", ["--seed", str(2**32)], "--seed"),
    ("predict-feed", ["--calibration", "none"], "--calibration"),
    ("predict-feed", ["--threshold-strategy", "f1"], "--threshold-strategy"),
])
def test_coupled_boundaries_and_removed_flags(dispatch, capsys, command, options, flag):
    load, handler = dispatch
    with pytest.raises(SystemExit) as error:
        main(["goldenset", command, *options])
    assert error.value.code == 2
    assert flag in capsys.readouterr().err
    load.assert_not_called()
    handler.assert_not_called()


@pytest.mark.parametrize("command,options,expected", [
    ("classify", ["--holdout-fraction", "0"], {"holdout_fraction": 0.0}),
    ("classify", ["--holdout-fraction", "0.999"], {"holdout_fraction": .999}),
    ("classify", ["--calibration", "none", "--threshold-strategy", "f1"],
     {"calibration": "none", "threshold_strategy": "f1"}),
    ("classify-llm", [], {"limit": None}),
    ("analyze-notes", ["--min-chars", "100", "--max-chars", "100"], {"limit": None, "max_chars": 100}),
    ("tune", ["--seed", str(2**32 - 1)], {"seed": 2**32 - 1}),
    ("eval-baseline", ["--seed", str(2**32 - 1), "--n-repeats", "1"], {"n_repeats": 1}),
    ("eval-baseline", ["--learning-curve", "--learning-curve-fractions", " .15, .5, 1 "],
     {"learning_curve_fractions": (.15, .5, 1.0)}),
])
def test_supported_sentinels_and_valid_edges_reach_handler(dispatch, command, options, expected):
    _, handler = dispatch
    assert main(["goldenset", command, *options]) == 0
    args = handler.call_args.args[0]
    assert {name: getattr(args, name) for name in expected} == expected


@pytest.mark.parametrize("fractions", [None, "0.2,0.6,1"])
def test_learning_cli_forwards_validated_fractions_and_repeat_budget(dataset, monkeypatch, fractions):
    from zotero_summarizer.services.model import eval_baseline

    settings, _ = dataset
    before = settings.golden_csv_path.read_bytes()
    run = Mock(side_effect=RuntimeError("test stops before sampling"))
    monkeypatch.setattr(eval_baseline, "run_learning_curve", run)
    options = [] if fractions is None else ["--learning-curve-fractions", fractions]
    with pytest.raises(RuntimeError, match="test stops before sampling"):
        main(["goldenset", "eval-baseline", "--project-root", str(settings.project_root),
              "--learning-curve", "--n-repeats", "2", *options])
    assert run.call_args.kwargs["n_repeats"] == 2
    assert run.call_args.kwargs["fractions"] == (
        eval_baseline.DEFAULT_LEARNING_CURVE_FRACTIONS if fractions is None else (.2, .6, 1.0)
    )
    assert settings.golden_csv_path.read_bytes() == before


@pytest.mark.parametrize("override", [False, True])
def test_llm_cli_credentials_follow_config_unless_explicitly_overridden(tmp_path, monkeypatch, override):
    from tests.test_classifier_evaluation_truth import _project, _run_report
    from zotero_summarizer.services import _adapters

    settings = _project(tmp_path, monkeypatch)
    monkeypatch.setenv("TEST_CLASSIFIER_API_KEY", "configured-test-token")
    monkeypatch.setenv("CUSTOM_API_KEY", "different-provider-test-token")

    class Client:
        def pydantic_prompt(self, *, prompt, pydantic_model):
            return pydantic_model(priority="must_read", confidence=.9, rationale="Test")

    build = Mock(return_value=Client())
    monkeypatch.setattr(_adapters, "build_llm", build)
    options = ["--api-key-env", "CUSTOM_API_KEY"] if override else []
    assert main(["goldenset", "classify-llm", "--project-root", str(tmp_path),
                 "--classifier-name", "test", "--limit", "1", "--workers", "1", *options]) == 0
    assert build.call_args.args == (
        "http://localhost", "test", "different-provider-test-token" if override else "configured-test-token",
    )
    assert _run_report(settings)["rows_with_priority"] == 1


def test_llm_cli_missing_configured_credential_does_not_use_another_provider(tmp_path, monkeypatch):
    from tests.test_classifier_evaluation_truth import _project
    from zotero_summarizer.services import _adapters

    settings = _project(tmp_path, monkeypatch)
    before = settings.golden_csv_path.read_bytes()
    monkeypatch.delenv("TEST_CLASSIFIER_API_KEY", raising=False)
    monkeypatch.setenv("CUSTOM_API_KEY", "different-provider-test-token")
    build = Mock(side_effect=AssertionError("must not build with another provider's credential"))
    monkeypatch.setattr(_adapters, "build_llm", build)
    with pytest.raises(RuntimeError, match="TEST_CLASSIFIER_API_KEY"):
        main(["goldenset", "classify-llm", "--project-root", str(tmp_path), "--limit", "1"])
    build.assert_not_called()
    assert settings.golden_csv_path.read_bytes() == before
