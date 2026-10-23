"""Complete, immutable experiment attempts; no product-cache or native-write effects."""
import hashlib
import json
from pathlib import Path
from time import perf_counter
from uuid import UUID, uuid4

from zotero_summarizer.services.faithbench._interpretation_inputs import code_identity, prepare, publish_json, validate_frozen
from zotero_summarizer.services.library._digest_audit import canonical_hash
from zotero_summarizer.services.library._interpretation_qa import answer
from zotero_summarizer.services.library._interpretation_trace import Trace
from zotero_summarizer.services.library._map_reduce import ChunkBudget
from zotero_summarizer.services.library.interpretation import qualify, review


def _case(inputs, config, clients, assignment):
    key, arm = assignment['paper'], assignment['condition']
    recipe = inputs['recipe']
    paper = next(p for p in inputs['meta']['papers'] if p['item_key'] == key)
    text = inputs['texts'][key]
    content = {'title': paper['title'], 'full_text': text}
    trace = Trace(max_calls=recipe['max_client_calls'])
    record = {'assignment': assignment, 'review': None, 'questions': [], 'review_status': 'failed'}
    if code_identity() != inputs['code']:
        raise ValueError('Execution source changed')
    start = perf_counter()
    try:
        record['review'] = review({'title': paper['title'], 'text': text, 'content': content}, config,
                                  (clients['map'], clients['reduce']),
                                  ChunkBudget(recipe['context_chars'], recipe['chunk_chars'], recipe['sub_concurrency']),
                                  arm, trace)
        record['review_status'] = 'returned'
    except Exception as exc:  # Per-assignment failure, never a synthesized review or abstention.
        record['review_failure_type'] = type(exc).__name__
    for item in (i for i in inputs['items'] if i['paper_item_key'] == key):
        row = {'item_id': item['item_id'], 'question': item['question'], 'status': 'failed'}
        try:
            if record['review'] is None:
                raise ValueError('Base review unavailable')
            row['output'] = answer(record['review'], item['question'], clients['qa'],
                                   max_chars=recipe['context_chars'], condition=arm, trace=trace)
            row['status'] = 'returned'
        except Exception as exc:
            row['failure_type'] = type(exc).__name__
        record['questions'].append(row)
    record.update(seconds=perf_counter() - start, calls=trace.calls)
    return record


def _render(directory, record):
    from zotero_summarizer.models import PaperDigest
    from zotero_summarizer.services.library._paper_read_html import write_outputs
    from zotero_summarizer.services.zotero._notes import build_digest_note_html

    result = record['review']
    if result is None:
        return
    qualify(result)
    staging = directory / 'staging'
    staging.mkdir(mode=0o700)
    write_outputs(directory, result['content'], staging=staging, digest=result['digest'], interpretation=result)
    with (directory / 'zotero-note.html').open('x', encoding='utf-8') as stream:
        stream.write(build_digest_note_html(PaperDigest.model_validate(result['digest']), interpretation=result))
    for path in directory.rglob('*'):
        if path.is_file():
            path.chmod(0o600)


def execute(settings, snapshot, config, profiles, build_client):
    root, inputs = prepare(settings, snapshot, config, profiles)
    assignments = [{'paper': key, 'condition': arm, 'directory': canonical_hash([key, arm]),
                    'review_blind_id': uuid4().hex, 'card_blind_id': uuid4().hex,
                    'question_blind_ids': {item['item_id']: uuid4().hex for item in inputs['items']
                                           if item['paper_item_key'] == key}}
                   for key in inputs['evaluation'] for arm in inputs['recipe']['labels']]
    publish_json(root / 'plan.json', assignments)
    clients = {role: build_client(role, profile) for role, profile in profiles.items()}
    for assignment in assignments:
        directory = root / assignment['directory']
        directory.mkdir(mode=0o700)
        record = _case(inputs, config, clients, assignment)
        publish_json(directory / 'record.json', record)
        reopened = json.loads((directory / 'record.json').read_text())
        _render(directory, reopened)
    inventory = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in sorted(root.rglob('*')) if p.is_file()}
    publish_json(root / 'manifest.json', {'contract': 'interpretation-experiment-v1', 'files': inventory})
    read_run(root)
    return root


def read_run(directory):
    root = Path(directory)
    if root.is_symlink() or (root / 'manifest.json').is_symlink():
        raise ValueError('Symlinked experiment run or manifest')
    manifest = json.loads((root / 'manifest.json').read_text())
    paths = {str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and p != root / 'manifest.json'}
    if manifest['contract'] != 'interpretation-experiment-v1' or paths != set(manifest['files']):
        raise ValueError('Missing/foreign experiment inventory')
    for name, expected in manifest['files'].items():
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Experiment artifact identity changed')
    inputs = json.loads((root / 'inputs.json').read_text())
    plan = json.loads((root / 'plan.json').read_text())
    validate_frozen(root, inputs)
    expected = {(key, arm) for key in inputs['evaluation'] for arm in inputs['recipe']['labels']}
    if len(plan) != len(expected) or {(row['paper'], row['condition']) for row in plan} != expected:
        raise ValueError('Experiment plan omits assigned outcomes')
    records, blind_ids = [], []
    required = {'benchmark.jsonl', 'benchmark.review.csv', 'recipe.json', 'inputs.json', 'plan.json'}
    for assignment in plan:
        blind_ids.extend([assignment['review_blind_id'], assignment['card_blind_id'], *assignment['question_blind_ids'].values()])
        if assignment['directory'] != canonical_hash([assignment['paper'], assignment['condition']]):
            raise ValueError('Invalid experiment case identity')
        record = json.loads((root / assignment['directory'] / 'record.json').read_text())
        questions = {item['item_id']: item['question'] for item in inputs['items']
                     if item['paper_item_key'] == assignment['paper']}
        if (record['assignment'] != assignment or len(record['questions']) != len(questions) or
            {row['item_id']: row['question'] for row in record['questions']} != questions or
            set(assignment['question_blind_ids']) != set(questions)):
            raise ValueError('Experiment question coverage/identity changed')
        required.add(assignment['directory'] + '/record.json')
        if record['review_status'] not in {'returned', 'failed'} or any(
            row['status'] not in {'returned', 'failed'} for row in record['questions']
        ) or (record['review_status'] == 'returned') != (record['review'] is not None):
            raise ValueError('Invalid experiment terminal state')
        if record['review'] is not None:
            if record['review']['text'] != inputs['texts'][assignment['paper']]:
                raise ValueError('Experiment review source changed')
            qualify(record['review'])
            if (record['review']['interpretation']['status'] == 'accepted' and
                record['review']['scientific_call'] not in record['calls']):
                raise ValueError('Accepted optional verifier invocation changed')
            required.update(assignment['directory'] + '/' + name for name in
                            ('paper_presentation.html', 'paper_audit.json', 'zotero-note.html'))
        records.append(record)
    if required != paths or len(set(blind_ids)) != len(blind_ids) or any(UUID(value).hex != value for value in blind_ids):
        raise ValueError('Foreign/missing admitted publication inventory or blind identity')
    return inputs, records
