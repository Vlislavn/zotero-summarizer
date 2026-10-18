import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

// These are workflow/API machine contracts, not model or permission-sandbox evals.
const root = process.env.NATIVE_WORKFLOW_ROOT
  ?? fileURLToPath(new URL('../.agents/workflows/', import.meta.url));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const assignment = 'Authorized synthetic read-only assignment. No file or tool actions.';
const programs = Object.fromEntries(await Promise.all(
  ['review', 'smoke'].map(async name => [
    name,
    new AsyncFunction('args', 'runs', await readFile(`${root}/${name}.js`, 'utf8')),
  ]),
));

for (const [name, program] of Object.entries(programs)) {
  for (const task of [undefined, null, false, 0, [], {}, '', ' \n\t']) {
    await test(`${name}: rejects non-task ${JSON.stringify(task)}`, async () => {
      let calls = 0;
      const denied = () => { calls += 1; throw new Error('Unexpected dispatch'); };
      await assert.rejects(program({ task }, { run: denied, all: denied }), Error);
      assert.equal(calls, 0);
    });
  }

  await test(`${name}: resolved non-success stops dependent fanout`, async () => {
    const receipt = { ok: false, state: 'running', output: 'Opaque nonterminal payload' };
    let batches = 0;
    const result = await program({ task: assignment }, {
      run: async () => receipt,
      all: async () => { batches += 1; return []; },
    });
    assert.equal(batches, 0);
    assert.equal(result.transportComplete, false);
    assert.equal(result[name === 'review' ? 'scout' : 'first'], receipt);
  });

  await test(`${name}: rejected scout promise cannot start fanout`, async () => {
    const failure = new Error('Synthetic native rejection');
    let batches = 0;
    await assert.rejects(program({ task: assignment }, {
      run: async () => { throw failure; },
      all: async () => { batches += 1; return []; },
    }), error => error === failure);
    assert.equal(batches, 0);
  });
}

await test('review: awaits scout and preserves both ordered opaque results', async () => {
  let release;
  const scoutDone = new Promise(resolve => { release = resolve; });
  const scout = { ok: true, output: 'OPAQUE_SCOUT', runId: 'source-run' };
  const reviews = [
    { ok: true, output: 'OPAQUE_A', runId: 'review-a' },
    { ok: true, output: 'OPAQUE_B', runId: 'review-b' },
  ];
  let batches = 0;
  const pending = programs.review({ task: assignment }, {
    run: (key, options) => {
      assert.equal(key, 'scout');
      assert.equal(options.agent, 'scout');
      assert.equal(options.context, 'fresh');
      assert.equal(options.task, assignment);
      return scoutDone;
    },
    all: async jobs => {
      batches += 1;
      assert.deepEqual(jobs.map(job => job.key), ['correctness', 'evidence']);
      for (const job of jobs) {
        assert.equal(job.agent, 'reviewer');
        assert.equal(job.context, 'fresh');
        assert.ok(job.task.startsWith(assignment));
        assert.ok(job.task.includes(scout.output));
      }
      return reviews;
    },
  });
  assert.equal(batches, 0);
  release(scout);
  const result = await pending;
  assert.equal(batches, 1);
  assert.equal(result.transportComplete, true);
  assert.equal(result.scout, scout);
  assert.equal(result.reviews, reviews);
});

await test('review: unsuccessful peer keeps the successful sibling and its order', async () => {
  const reviews = [
    { ok: false, error: 'Synthetic peer failure', runId: 'review-a' },
    { ok: true, output: 'OPAQUE_B', runId: 'review-b' },
  ];
  const result = await programs.review({ task: assignment }, {
    run: async () => ({ ok: true, output: 'OPAQUE_SCOUT' }),
    all: async () => reviews,
  });
  assert.equal(result.transportComplete, false);
  assert.equal(result.reviews, reviews);
  assert.equal(result.reviews[1], reviews[1]);
});

await test('smoke: sequential receipt precedes two fresh canary requests', async () => {
  const first = { ok: true, output: 'NATIVE_SEQUENCE' };
  const parallel = [
    { ok: true, output: 'NATIVE_LEFT' },
    { ok: true, output: 'NATIVE_RIGHT' },
  ];
  const events = [];
  const result = await programs.smoke({ task: assignment }, {
    run: async (key, options) => {
      events.push(key);
      assert.equal(key, 'sequence');
      assert.equal(options.agent, 'scout');
      assert.equal(options.context, 'fresh');
      assert.ok(options.task.startsWith(assignment));
      return first;
    },
    all: async jobs => {
      events.push('parallel');
      assert.deepEqual(jobs.map(job => job.key), ['left', 'right']);
      for (const job of jobs) {
        assert.equal(job.agent, 'scout');
        assert.equal(job.context, 'fresh');
        assert.ok(job.task.startsWith(assignment));
      }
      return parallel;
    },
  });
  assert.deepEqual(events, ['sequence', 'parallel']);
  assert.equal(result.transportComplete, true);
  assert.equal(result.first, first);
  assert.equal(result.parallel, parallel);
});

await test('smoke: an unsuccessful parallel receipt is not complete transport', async () => {
  const parallel = [{ ok: true }, { ok: false, stopped: true }];
  const result = await programs.smoke({ task: assignment }, {
    run: async () => ({ ok: true, output: 'NATIVE_SEQUENCE' }),
    all: async () => parallel,
  });
  assert.equal(result.transportComplete, false);
  assert.equal(result.parallel, parallel);
});
