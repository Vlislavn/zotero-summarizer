"""A copied terminal must remain the projection of its admitted recording."""
import pytest

from tools.method_validation import support_run
from tools.method_validation.decision_adapter import project
from tools.method_validation.support import encoded, loads, sha
from test_support_fixtures import fixture, native_wire, validated


@pytest.mark.parametrize('mutation', ['terminal', 'transport_status'])
def test_reopen_rejects_replaced_terminal_with_unchanged_bound_recording(tmp_path, mutation):
    cfg, _, _, _ = fixture(tmp_path)
    config = validated(cfg)
    owner, _ = support_run.run(config, encoded(cfg))
    bundle = support_run.prepare(config)[0]
    wire = native_wire()
    wire['answers']['decision']['choice'] = 'contradicts'
    replacement = encoded(wire)
    assert replacement != (owner / 'recording-0.bin').read_bytes()
    row = loads((owner / 'pair-0.json').read_bytes())
    status, readout = project(replacement, config.arms[0], config)
    if mutation == 'terminal':
        row.update(status=status, readout=readout, support=False)
        (owner / 'terminal-0.bin').write_bytes(replacement)
    else:
        row.update(status='unavailable', readout=None, support=None)
        row['telemetry']['terminal_status'] = 'unavailable'
    (owner / 'pair-0.json').write_bytes(encoded(row))
    rows = [loads((owner / f'pair-{i}.json').read_bytes()) for i in range(3)]
    report = support_run.report(rows, config, bundle)
    (owner / 'report.json').write_bytes(encoded(report))
    (owner / 'report.md').write_text(support_run.readable(report))
    manifest = loads((owner / 'manifest.json').read_bytes())
    for name in ('terminal-0.bin', 'pair-0.json', 'report.json', 'report.md'):
        manifest['files'][name] = sha((owner / name).read_bytes())
    raw = encoded(manifest)
    (owner / 'manifest.json').write_bytes(raw)
    (owner / 'ACK').write_bytes(sha(raw).encode())
    with pytest.raises(ValueError, match='recording'):
        support_run.reopen(owner)
