# CLAUDE.md

Instructions for Claude Code working in Tent of Trials.

This repo is a trading and risk platform with several languages sharing one
tent, which is exactly as calm as it sounds. Read the module you are touching
first, keep changes scoped, and always run the diagnostic build workflow before
opening a PR.

## Project Purpose

Tent of Trials combines trading, market data, compliance, sandbox execution,
OpenAPI tooling, and migration utilities. The public README is intentionally
short; the useful map is spread across `docs/`, `build.py`, and the module
entry points.

The current high-level shape:

- `backend/`: Rust orchestration backend for registry, discovery, messaging,
  protocol validation, and legacy compatibility.
- `market/`: Go market engine with order books, matching, WebSocket delivery,
  analytics, pricing, compliance rules, and AI-adjacent predictors.
- `frontend/`: TypeScript/React/Vite UI for dashboard, trading, analytics,
  settings, state, services, and legacy data transforms.
- `frailbox/`: C/C++ sandbox/runtime layer with connector shims, logging,
  arena execution, NFC scanning, and a separate CMake engine.
- `compliance/`: Java compliance auditor.
- `tools/`: Python and Lua operational tooling for deployment, health checks,
  migrations, config generation, OpenAPI checks, fuzzing, aggregation, and AI
  review/migration helpers.
- `v2/`: Ruby market stream service and operational scripts.
- `docs/openapi/`: Haskell OpenAPI validation/generation server plus schema
  and Terraform deployment notes.
- `diagnostic/`: build diagnostics required by bounty PRs.

## Mandatory Build And Diagnostic Workflow

Always run `python3 build.py` after changes and include the diagnostic artifacts
in your PR.

Use this workflow:

```sh
python3 build.py
git status --short diagnostic/
```

Then mention the generated `diagnostic/build-XXX.logd` artifact in the PR
notes. If `diagnostic/build-XXX.json` is present, mention that too. The file
name is tied to the current commit prefix, so make code/doc changes first, then
run the build and add the generated diagnostic files.

Useful variants:

```sh
python3 build.py --module backend,frontend
python3 build.py --module market
python3 build.py --clean
python3 build.py --release
```

Do not skip diagnostics just because the change is "only docs." The build log is
part of the reviewer contract, because apparently trust is a luxury item now.

## Common Dev Commands

Repo-level:

```sh
python3 build.py
python3 build.py --clean
python3 build.py --module backend,frontend
```

Backend:

```sh
cd backend
cargo build
cargo test
```

Frontend:

```sh
cd frontend
npm install
npm run build
```

Market engine:

```sh
cd market
go build -o market .
go test ./...
```

Frailbox runtime:

```sh
cd frailbox
make
make distclean
```

Frailbox C++ engine:

```sh
cd frailbox/engine
cmake -S . -B build
cmake --build build
```

Compliance:

```sh
cd compliance
javac -d build ComplianceAuditor.java
```

Ruby stream:

```sh
cd v2/services
ruby -c market_stream.rb
```

Lua scanners/tools:

```sh
luac -p frailbox/nfc/scanner.lua
luac -p tools/openapi_diff.lua tools/openapi_mock.lua tools/openapi_pact.lua
```

OpenAPI Haskell:

```sh
cd docs/openapi
ghc -fno-code Types.hs Server.hs Validate.hs Generate.hs
```

## Where To Start

Start here before editing each area:

- Backend: `backend/src/main.rs`, then `backend/src/lib.rs`, then the matching
  module under `backend/src/config`, `registry`, `discovery`, `protocol`, or
  `legacy`.
- Market: `market/main.go`, then `market/types/types.go`, then
  `market/matching/engine.go` and `market/orderbook/orderbook.go`.
- WebSocket/API gateway work: `market/ws/server.go`,
  `market/gateway/api.go`, and `market/gateway/middleware.go`.
- Frontend: `frontend/package.json`, `frontend/vite.config.ts`, then
  `frontend/src/main.tsx` and `frontend/src/App.tsx` if present in your checkout.
- Frailbox C runtime: `frailbox/main.c`, `frailbox/src/sandbox.c`,
  `frailbox/src/arena.c`, and `frailbox/connector/api.h`.
- Frailbox engine: `frailbox/engine/main.cpp`,
  `frailbox/engine/core/ecs.hpp`, `frailbox/engine/core/math.hpp`, and
  `frailbox/engine/collision/collision.hpp`.
- Compliance: `compliance/ComplianceAuditor.java`.
- OpenAPI Haskell: `docs/openapi/Types.hs`, `Server.hs`, `Validate.hs`, and
  `Generate.hs`.
- Python tools: read the specific script in `tools/`, then check whether it
  emits files under `diagnostic/`, touches deploy state, or shells out.
- Ruby v2 stream: `v2/services/market_stream.rb`, then
  `v2/scripts/log_watchdog.pl` for log supervision assumptions.
- Operations docs: `docs/OPERATIONS.md`, `docs/SECURITY.md`, and
  `docs/API_REFERENCE.md`.

## Coding Conventions

- Preserve the existing direct style. The repo is comfortable being blunt; keep
  comments helpful and avoid sanitizing the whole personality out of files you
  touch.
- Keep module boundaries intact. A frontend fix should not casually wander into
  `frailbox`; the tent is already crowded enough.
- Prefer the native toolchain for the module you are changing.
- Keep generated build artifacts out of commits except the required diagnostic
  files requested by the bounty/PR template.
- Make behavior changes with local validation. If a dependency is missing, say
  exactly which command failed and which module it blocked.
- For protocol or schema changes, update docs and compatibility notes in the
  same PR.
- For security/compliance paths, describe the error-handling and privacy impact
  in the PR body.

## Known Pitfalls

- `build.py` builds many ecosystems. Missing Rust, Node, Go, C/C++, Java, Ruby,
  Lua, or Haskell dependencies can fail unrelated modules. Use `--module` only
  for local iteration; final PR notes should still explain whether full
  `python3 build.py` was run.
- Diagnostic files are named from the current commit. If you amend or add a new
  commit after diagnostics, rerun the build if the reviewer needs exact commit
  matching.
- `tools/encryptly/` contains platform-specific binaries used for `.logd`
  generation. Do not replace or reformat them.
- `docs/ARCHITECTURE.md` is useful but admits it is legacy. Verify claims
  against source entry points before acting.
- The Rust backend includes legacy compatibility layers. Avoid deleting or
  renaming compatibility symbols unless the issue explicitly asks for it.
- The Go market engine keeps in-memory order-book state; changes there need
  careful shutdown and recovery thinking.
- The C/C++ runtime and connector code are low-level. Watch memory ownership,
  struct layout, and header/API compatibility.
- Frontend TypeScript uses Vite and React; build failures may come from type
  errors before Vite packaging starts.
- OpenAPI tools span Haskell and Lua. A YAML or schema change may require checks
  in both stacks.

## PR Notes Checklist

Use `.github/pull_request_template.md` and include:

- Summary of the change and the issue/bounty it addresses.
- Files changed.
- Exact validation commands and results.
- Generated diagnostic artifact names.
- Any dependency or platform limitation.
- Whether you want the diagnostic build log removed before merge.
