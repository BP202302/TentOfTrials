# API HTTP error regressions

Install the frontend dependencies, then run:

    npm ci
    npm run test:api

The tests use Node 22 and the declared TypeScript development dependency.
They transpile the actual src/services/api.ts and execute its exported methods.
Only legacyCompat (outside the tested non-legacy path) and browser/network
dependencies are mocked. No copied implementation or live backend is used.

Coverage includes successful responses, nested JSON 401, 429, raw text 500,
malformed JSON, empty bodies, problem+json, network failure, actual timeout
aborts, unreadable bodies and body aborts. Timers are checked and cleaned.

The historical generated-file warning refers to templates absent from this
repository. Issue #1 explicitly requests this focused fix to api.ts.
