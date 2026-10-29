import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import vm from 'node:vm';

const require = createRequire(import.meta.url);
const ts = require(process.env.TYPESCRIPT_PATH || 'typescript');
const source = await readFile(new URL('../src/services/api.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext }
}).outputText;

async function client(fetch) {
  const warnings = [];
  const timers = new Set();
  const context = vm.createContext({
    fetch, Response, Headers, AbortController, TypeError, URLSearchParams,
    localStorage: { getItem: () => null },
    console: { warn: (...args) => warnings.push(args.join(' ')) },
    setTimeout: (fn, ms) => { const timer = setTimeout(fn, ms); timers.add(timer); return timer; },
    clearTimeout: timer => { timers.delete(timer); clearTimeout(timer); }
  });
  const module = new vm.SourceTextModule(compiled, {
    context, initializeImportMeta: meta => { meta.env = {}; }
  });
  await module.link(specifier => {
    assert.equal(specifier, '../utils/legacyCompat');
    return new vm.SyntheticModule(['$httpLegacy', 'legacyToJson'], function () {
      this.setExport('$httpLegacy', () => { throw new Error('Legacy path must not run'); });
      this.setExport('legacyToJson', JSON.stringify);
    }, { context });
  });
  await module.evaluate();
  return { api: module.namespace, warnings, timers,
    close: () => { for (const timer of timers) clearTimeout(timer); timers.clear(); } };
}

const json = (body, status, headers = {}) => new Response(JSON.stringify(body), {
  status, headers: { 'Content-Type': 'application/json', ...headers }
});
async function check(fetch, fn) {
  const c = await client(fetch);
  try { await fn(c); } finally { c.close(); }
}

test('2xx retains data and success interceptors', async () => {
  await check(async () => json({ value: 7 }, 200), async ({ api }) => {
    let count = 0;
    const remove = api.addResponseInterceptor(response => { count++; return response; });
    const result = await api.get('/ok', undefined, { retries: 0 });
    assert.equal(result.data.value, 7);
    assert.equal(result.status, 200);
    assert.equal(count, 1);
    remove();
  });
});

test('401 nested JSON reaches error interceptors, never success', async () => {
  await check(async () => json({ error: { message: 'Expired', details: { reason: 'token' },
    requestId: 'body-id', path: '/auth', suggestion: 'Sign in' } }, 401), async (c) => {
    let errors = 0, successes = 0;
    c.api.addResponseInterceptor(r => { successes++; return r; });
    c.api.addErrorInterceptor(e => { errors++; return { ...e, intercepted: true }; });
    await assert.rejects(c.api.get('/secret', undefined, { retries: 0 }), e => {
      assert.equal(e.code, 401); assert.equal(e.message, 'Expired');
      assert.equal(e.details.reason, 'token'); assert.equal(e.requestId, 'body-id');
      assert.equal(e.path, '/auth'); assert.equal(e.suggestion, 'Sign in');
      assert.equal(e.intercepted, true); return true;
    });
    assert.equal(errors, 1); assert.equal(successes, 0);
    assert.ok(c.warnings.some(x => x.includes('Authentication failed')));
  });
});

test('429 preserves HTTP code, body and request header; default interceptor runs', async () => {
  await check(async () => json({ code: 7, message: 'Slow down', details: { limit: 3 } }, 429,
    { 'X-Request-ID': 'header-id', 'Retry-After': '10' }), async (c) => {
    await assert.rejects(c.api.post('/limited'), e => {
      assert.equal(e.code, 429); assert.equal(e.details.limit, 3);
      assert.equal(e.requestId, 'header-id'); assert.equal(e.path, '/limited');
      assert.ok(e.suggestion.includes('10')); return true;
    });
    assert.ok(c.warnings.some(x => x.includes('Rate limit')));
  });
});

test('500 text is an error with raw body details', async () => {
  await check(async () => new Response('upstream unavailable', { status: 500 }), async ({ api }) => {
    await assert.rejects(api.get('/down', undefined, { retries: 0 }), e => {
      assert.equal(e.code, 500); assert.equal(e.message, 'upstream unavailable');
      assert.equal(e.details.responseBody, 'upstream unavailable'); return true;
    });
  });
});

test('malformed JSON retains HTTP status and raw error', async () => {
  await check(async () => new Response('{broken', { status: 502,
    headers: { 'Content-Type': 'application/json' } }), async ({ api }) => {
    await assert.rejects(api.post('/broken'), e => {
      assert.equal(e.code, 502); assert.equal(e.details.responseBody, '{broken'); return true;
    });
  });
});

test('empty HTTP error retains status and fallback message', async () => {
  await check(async () => new Response('', { status: 503 }), async ({ api }) => {
    await assert.rejects(api.post('/empty'), e => {
      assert.equal(e.code, 503); assert.ok(e.message.length > 0); return true;
    });
  });
});

test('problem+json is parsed as structured error', async () => {
  await check(async () => new Response(JSON.stringify({ message: 'Invalid', details: { field: 'name' } }),
    { status: 422, headers: { 'Content-Type': 'application/problem+json' } }), async ({ api }) => {
    await assert.rejects(api.post('/problem'), e => {
      assert.equal(e.code, 422); assert.equal(e.details.field, 'name'); return true;
    });
  });
});

test('network normalization remains compatible and cleans timer', async () => {
  await check(async () => { throw new TypeError('fetch failed'); }, async ({ api, timers }) => {
    await assert.rejects(api.get('/network', undefined, { retries: 0 }), e => {
      assert.equal(e.code, 0); assert.equal(e.message, 'Network error'); return true;
    });
    assert.equal(timers.size, 0);
  });
});

test('actual timeout abort remains 408 and cleans timer', async () => {
  await check((_url, config) => new Promise((_resolve, reject) => {
    config.signal.addEventListener('abort', () => {
      const error = new Error('aborted'); error.name = 'AbortError'; reject(error);
    }, { once: true });
  }), async ({ api, timers }) => {
    await assert.rejects(api.get('/slow', undefined, { retries: 0, timeout: 5 }), e => {
      assert.equal(e.code, 408); assert.equal(e.message, 'Request timed out'); return true;
    });
    assert.equal(timers.size, 0);
  });
});

test('unreadable error body retains HTTP status', async () => {
  const response = new Response('', { status: 500 });
  response.text = async () => { throw new TypeError('body stream failed'); };
  await check(async () => response, async ({ api }) => {
    await assert.rejects(api.post('/unreadable'), e => {
      assert.equal(e.code, 500); assert.ok(e.details.responseBody.readError); return true;
    });
  });
});

test('aborted error body keeps abort normalization across realms', async () => {
  const response = new Response('', { status: 500 });
  response.text = async () => { throw new DOMException('body aborted', 'AbortError'); };
  await check(async () => response, async ({ api }) => {
    await assert.rejects(api.post('/body-abort'), e => {
      assert.equal(e.code, 408); return true;
    });
  });
});
