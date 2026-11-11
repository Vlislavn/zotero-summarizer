"""Synthetic Vite header contract, not a listening proxy/browser integration test.

Import the real frontend config in Node, execute unchanged AST slices from the
installed Vite header helper/target normalizer, then dispatch those headers
through the production ASGI boundary. Dependency-layout drift is a harness error,
not permission to substitute a copied implementation. No server or socket opens.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from zotero_summarizer.api.local_access import LocalAccessMiddleware


pytestmark = pytest.mark.skipif(
    os.environ.get("ZS_VITE_PROXY_CONTRACT_SMOKE") != "1",
    reason="opt-in installed-Vite cross-language contract",
)


# Node executes this only inside the parent's separately admitted pytest arm.
NODE_OBSERVER = r"""
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync } from 'node:fs';
import { createRequire, syncBuiltinESMExports } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import url from 'node:url';
import vm from 'node:vm';
import http from 'node:http';
import https from 'node:https';
import net from 'node:net';
import tls from 'node:tls';
import dgram from 'node:dgram';

// A dependency trying to listen/connect must fail, never become live proof.
for (const [owner, key] of [
  [http, 'createServer'], [https, 'createServer'], [net, 'createServer'],
  [tls, 'createServer'], [net.Socket.prototype, 'connect'],
  [tls, 'connect'], [dgram, 'createSocket'],
]) owner[key] = () => { throw new Error(`network/server operation forbidden: ${key}`); };
syncBuiltinESMExports();

const configURL = pathToFileURL(process.argv[1]);
const crossOrigin = JSON.parse(process.argv[2]);
const frontendPackageURL = new URL('./package.json', configURL);
const require = createRequire(frontendPackageURL);
const sources = {};
function readSource(name, location) {
  const path = realpathSync(fileURLToPath(location));
  const source = readFileSync(path, 'utf8');
  sources[name] = { path, sha256: createHash('sha256').update(source).digest('hex') };
  return source;
}
function unique(nodes, name) {
  assert.equal(nodes.length, 1, `installed Vite layout changed: ${name}`);
  return nodes[0];
}
function assigns(statement, property) {
  const expression = statement.expression;
  return expression?.type === 'AssignmentExpression'
    && expression.left.type === 'MemberExpression'
    && expression.left.property.name === property;
}

// Resolve metadata from the frontend context, then follow the real import condition.
readSource('frontend_package', frontendPackageURL);
const packageURL = pathToFileURL(require.resolve('vite/package.json'));
const vitePackage = JSON.parse(readSource('vite_package', packageURL));
const importEntry = vitePackage.exports['.'].import.default;
assert.equal(typeof importEntry, 'string', 'installed Vite requires an ESM import export');
assert.ok(importEntry.startsWith('./'), 'installed Vite import export must be package-local');
const entryURL = new URL(importEntry, packageURL);
const { parseAst, version } = await import(entryURL.href);
assert.equal(typeof parseAst, 'function', 'installed ESM entry must export parseAst');
assert.equal(version, vitePackage.version, 'loaded Vite version must match package metadata');
// Follow the actual installed public entry's defineConfig export, not a filename guess.
const entry = parseAst(readSource('vite_entry', entryURL));
const configExport = unique(entry.body.filter(node => node.type === 'ExportNamedDeclaration'
  && node.source && node.specifiers.some(item => item.exported.name === 'defineConfig')), 'entry export');
const chunkURL = new URL(configExport.source.value, entryURL);
const chunk = readSource('vite_proxy_chunk', chunkURL);
const tree = parseAst(chunk);
const wrappers = tree.body.filter(node => node.type === 'ExpressionStatement'
  && node.expression.type === 'CallExpression'
  && node.expression.callee.type === 'FunctionExpression').map(node => node.expression.callee);
const common = unique(wrappers.filter(node => node.body.body.some(item => assigns(item, 'setupOutgoing'))), 'header helper');
const proxy = unique(wrappers.filter(node => node.body.body.some(item => assigns(item, 'createRightProxy'))), 'normalizer wrapper');
const normalizer = unique(proxy.body.body.filter(node => node.type === 'FunctionDeclaration'
  && node.id.name === 'createRightProxy'), 'normalizer');
const required = unique(common.body.body.filter(node => node.type === 'VariableDeclaration')
  .flatMap(node => node.declarations).filter(node => node.id.name === 'required'), 'port dependency');
const portDeclaration = unique(tree.body.filter(node => node.type === 'VariableDeclaration'
  && node.declarations.some(item => item.id.name === required.init.name)), 'port helper');
const urlImport = unique(tree.body.filter(node => node.type === 'ImportDeclaration'
  && ['url', 'node:url'].includes(node.source.value)
  && node.specifiers.some(item => item.type === 'ImportDefaultSpecifier')), 'URL import');
const urlAlias = unique(urlImport.specifiers.filter(node => node.type === 'ImportDefaultSpecifier'), 'URL binding').local.name;
const slice = node => chunk.slice(node.start, node.end);
const exported = {};
const context = vm.createContext({ exports: exported, [urlAlias]: url, parse_url: url.parse, Buffer });
// These are complete unchanged source nodes; no reimplementation of header semantics.
vm.runInContext(`${slice(portDeclaration)}\n(${slice(common)})(exports);\n${slice(normalizer)}`,
  context, { filename: fileURLToPath(chunkURL), timeout: 1000 });

const configSource = readSource('frontend_config', configURL);
const { default: config } = await import(configURL.href);
assert.equal(readFileSync(configURL, 'utf8'), configSource, 'config changed while loading');
assert.equal(typeof config.server.proxy['/api'], 'object', 'expected actual /api proxy options');
const configured = config.server.proxy['/api'];
const authority = `localhost:${config.server.port}`;
const incoming = { method: 'POST', url: '/api/proxy-contract', headers: {
  host: authority, origin: crossOrigin ? 'http://other.invalid' : `http://${authority}`,
  'sec-fetch-site': 'same-origin', 'content-type': 'application/json',
}};
let outgoing;
let passes = 0;
context.createRightProxy('web')({ ...configured }).call({ webPasses: [
  (request, response, options) => {
    outgoing = exported.setupOutgoing({}, options, request);
    passes += 1;
    return true;
  },
]}, incoming, {});
assert.equal(passes, 1, 'normalizer did not reach the installed header helper');
process.stdout.write(JSON.stringify({ sources, vite_version: version, node_version: process.version,
  node_executable: realpathSync(process.execPath), config: {
    target: configured.target, changeOrigin: configured.changeOrigin ?? null, frontend_port: config.server.port,
  }, incoming, outgoing }));
"""


@pytest.mark.parametrize(
    ("target", "cross_origin", "status"),
    [
        (None, False, 200),
        ("http://127.0.0.1:8765", False, 200),
        (None, True, 403),
        ("http://127.0.0.1:8765", True, 403),
    ],
    ids=["default-write", "override-write", "default-cross-origin", "override-cross-origin"],
)
def test_installed_vite_proxy_preserves_browser_boundary(target, cross_origin, status, record_property):
    root = Path(__file__).resolve().parents[1]
    config = root / "frontend" / "vite.config.js"
    env = os.environ.copy()
    env.pop("VITE_API_TARGET", None)
    if target is not None:
        env["VITE_API_TARGET"] = target
    node = env.get("ZS_VITE_PROXY_TEST_NODE")
    assert node and Path(node).is_absolute() and Path(node).is_file() and os.access(node, os.X_OK), (
        "Opt-in contract requires ZS_VITE_PROXY_TEST_NODE to name an absolute executable Node path"
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", NODE_OBSERVER, str(config), json.dumps(cross_origin)],
        cwd=root, env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    record_property("vite_observer_exit", str(result.returncode))
    record_property("vite_observer_stderr", result.stderr)
    assert result.returncode == 0, f"Vite source observer failed, not behavioral RED:\n{result.stderr}"
    observed = json.loads(result.stdout)
    record_property("vite_observation", json.dumps(observed, sort_keys=True))
    loaded = {}
    for name in ("zotero_summarizer.api.local_access", "zotero_summarizer.services.setup.deployment"):
        path = Path(sys.modules[name].__file__).resolve()
        loaded[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    record_property("loaded_source", json.dumps(loaded, sort_keys=True))

    incoming = observed["incoming"]["headers"]
    outgoing = observed["outgoing"]["headers"]
    assert outgoing["origin"] == incoming["origin"], "the proxy must neither erase nor forge Origin"
    assert observed["config"]["target"] == (target or "http://localhost:8000")
    reached = []

    async def handler(scope, receive, send):
        reached.append(scope["path"])
        await JSONResponse({"accepted": True})(scope, receive, send)

    client = TestClient(LocalAccessMiddleware(handler), base_url=observed["config"]["target"], client=("127.0.0.1", 50000))
    response = client.post(observed["incoming"]["url"], headers=outgoing, json={"synthetic": True})
    record_property("proxy_response_status", str(response.status_code))
    record_property("proxy_handler_paths", json.dumps(reached))
    assert response.status_code == status, f"actual Vite output rejected/accepted unexpectedly: {observed}"
    assert reached == (["/api/proxy-contract"] if status == 200 else [])
    if status == 403:
        assert response.json()["error"] == "local_access_only"
    else:
        assert outgoing["host"] == incoming["host"], "preserve browser authority, not backend authority"
