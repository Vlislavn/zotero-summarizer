"""Pure budget admission/IO tests: no SDK imports or actual child execution."""
import pytest

from tests import setup_request_transport_bootstrap as bootstrap


@pytest.mark.parametrize("raw,expected", [
    (None, 120.0), ("0.001", 0.001), ("1", 1.0), (" 60.5 ", 60.5),
    ("120", 120.0), ("120.0", 120.0), ("1.2e2", 120.0),
])
def test_budget_accepts_only_omission_or_positive_finite_within_cap(raw, expected):
    assert bootstrap.DEFAULT_TIMEOUT_SECONDS == 120
    assert bootstrap.MAX_TIMEOUT_SECONDS == 120
    assert bootstrap._timeout_seconds(raw) == expected


@pytest.mark.parametrize("raw", [
    "", " ", "not-seconds", "120s", "true", "0", "-0", "-0.5", "-1",
    "nan", "NaN", "inf", "-inf", "+Infinity", "1e309", "1e-999",
    "120.0000001", "121",
])
def test_invalid_explicit_budget_fails_before_artifacts_launch_or_native_imports(
    raw, tmp_path, monkeypatch, capsys,
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Invalid configuration must not launch or reach native setup")

    monkeypatch.setattr(bootstrap.subprocess, "run", forbidden)
    monkeypatch.setattr(bootstrap, "_guards", forbidden)
    monkeypatch.setattr(bootstrap, "_installed_identity", forbidden)
    monkeypatch.setenv(bootstrap.TIMEOUT_ENV, raw)
    artifacts = tmp_path / "must-not-be-created"
    monkeypatch.setattr(bootstrap.sys, "argv", ["bootstrap", "--artifacts", str(artifacts)])

    with pytest.raises(ValueError, match=bootstrap.TIMEOUT_ENV):
        bootstrap._timeout_seconds(raw)
    with pytest.raises(SystemExit) as failure:
        bootstrap.main()
    assert failure.value.code == 2
    assert bootstrap.TIMEOUT_ENV in capsys.readouterr().err
    assert not artifacts.exists()
    # The existing internal entry also validates before any native imports/guards.
    with pytest.raises(ValueError, match=bootstrap.TIMEOUT_ENV):
        bootstrap._child(artifacts)


@pytest.mark.parametrize("raw,expected", [(None, 120.0), ("0.25", 0.25), ("120", 120.0)])
def test_admitted_budget_reaches_safety_net_and_receipt_without_running_child(
    raw, expected, tmp_path, monkeypatch,
):
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    allowed = tmp_path / "data/issue129-validation"
    allowed.mkdir(parents=True)
    artifacts = allowed / "budget-proof"
    monkeypatch.setattr(bootstrap.sys, "argv", ["bootstrap", "--artifacts", str(artifacts)])
    if raw is None:
        monkeypatch.delenv(bootstrap.TIMEOUT_ENV, raising=False)
    else:
        monkeypatch.setenv(bootstrap.TIMEOUT_ENV, raw)
    calls = []

    def expire(command, **kwargs):
        calls.append((command, kwargs))
        raise bootstrap.subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(bootstrap.subprocess, "run", expire)
    assert bootstrap.main() == 124
    assert len(calls) == 1
    command, options = calls[0]
    assert command[1:3] == ["-I", "-B"]
    assert options["timeout"] == expected
    assert options["env"][bootstrap.TIMEOUT_ENV] == str(expected)
    log = (artifacts / "pytest.log").read_text()
    assert f"Actual-SDK safety-net: {expected:g} seconds; maximum 120." in log
    assert f"exceeded its {expected:g}-second safety-net" in log


def test_import_audit_allows_null_sink_but_not_external_state_or_weights(tmp_path, monkeypatch):
    import dotenv
    import dotenv.main

    hooks = []
    with monkeypatch.context() as patch:
        patch.setattr(bootstrap.sys, "addaudithook", hooks.append)
        for module in (dotenv, dotenv.main):
            for name in ("load_dotenv", "find_dotenv", "dotenv_values"):
                patch.setattr(module, name, getattr(module, name))
        patch.setattr(dotenv.main.DotEnv, "__init__", dotenv.main.DotEnv.__init__)
        bootstrap._guards(tmp_path)
    audit, = hooks
    for mode in (bootstrap.os.O_WRONLY, bootstrap.os.O_RDWR):
        audit("open", (bootstrap.os.devnull, None, mode))
        with pytest.raises(AssertionError, match="write outside owned sandbox"):
            audit("open", (str(tmp_path.parent / "external-state"), None, mode))
    for name in (".env", "weights.gguf", "weights.safetensors", "weights.bin"):
        with pytest.raises(AssertionError):
            audit("open", (str(tmp_path / name), None, bootstrap.os.O_RDONLY))
