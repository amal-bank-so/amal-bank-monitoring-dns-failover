// Unit tests for the CloudFront redirect Function template.
// Run: node --test infra/terraform/tools/redirect.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const template = readFileSync(join(here, '..', 'functions', 'redirect.js.tftpl'), 'utf8');

// Same substitution Terraform's templatefile() performs for this template.
function load(vars) {
  const src = template.replace(/\$\{(\w+)\}/g, (_, k) => {
    if (!(k in vars)) throw new Error(`missing template var ${k}`);
    return String(vars[k]);
  });
  return new Function(`${src}; return handler;`)();
}

const base = { target: 'https://www.amalbankso.so', status_code: 301, preserve_path: true, preserve_query: true, max_age: 300 };
const ev = (uri, querystring = {}) => ({ request: { method: 'GET', uri, querystring, headers: {} } });

test('template has no unrendered placeholders', () => {
  assert.doesNotThrow(() => load(base));
});

test('root path redirects to target root', () => {
  const r = load(base)(ev('/'));
  assert.equal(r.statusCode, 301);
  assert.equal(r.statusDescription, 'Moved Permanently');
  assert.equal(r.headers.location.value, 'https://www.amalbankso.so/');
  assert.equal(r.headers['cache-control'].value, 'max-age=300');
});

test('preserves path and query', () => {
  const r = load(base)(ev('/a/b', { x: { value: '1' }, y: { value: 'a%20b' } }));
  assert.equal(r.headers.location.value, 'https://www.amalbankso.so/a/b?x=1&y=a%20b');
});

test('preserves multi-value parameters', () => {
  const r = load(base)(ev('/', { t: { value: '1', multiValue: [{ value: '1' }, { value: '2' }] } }));
  assert.equal(r.headers.location.value, 'https://www.amalbankso.so/?t=1&t=2');
});

test('path not preserved sends everything to the target root', () => {
  const r = load({ ...base, preserve_path: false })(ev('/deep/page', { q: { value: '1' } }));
  assert.equal(r.headers.location.value, 'https://www.amalbankso.so/?q=1');
});

test('query not preserved drops the query string', () => {
  const r = load({ ...base, preserve_query: false })(ev('/p', { q: { value: '1' } }));
  assert.equal(r.headers.location.value, 'https://www.amalbankso.so/p');
});

test('no trailing ? when there is no query', () => {
  const r = load(base)(ev('/p'));
  assert.equal(r.headers.location.value, 'https://www.amalbankso.so/p');
});

test('every allowed status code has a description', () => {
  for (const [code, text] of [[301, 'Moved Permanently'], [302, 'Found'], [307, 'Temporary Redirect'], [308, 'Permanent Redirect']]) {
    const r = load({ ...base, status_code: code })(ev('/'));
    assert.equal(r.statusCode, code);
    assert.equal(r.statusDescription, text);
  }
});

test('never produces a redirect back to amalbank.so (no loop)', () => {
  const r = load(base)(ev('/x'));
  assert.ok(!/amalbank\.so/.test(r.headers.location.value.replace('amalbankso.so', '')));
});
