# Phase 1: public research and messaging host boundary

Canonical integration: `integrations/hermes/plugins/komatso-public-research`.
Runtime configs remain runtime-only. Apply with `deploy_security_phase1.py`.
Do not copy configs into this repository or update Hermes to apply this change.

Messaging surfaces:
- Default Bale and Telegram: web_search, web_extract, skills_list, skill_view,
  and all 17 public_browser_* tools.
- Maintenance Bale: those tools plus delegate_task, maintenance_manual_evidence,
  maintenance_partbook_lookup.
- Maintenance Telegram is neither created nor routed by this phase.
- Native browser_* tools, generic filesystem/process/code tools, connections,
  MCP, and skill_manage remain absent. CLI selections are unchanged.

Public browser uses the existing managed Chromium executable and Playwright Core.
Each session has a fresh context, Chromium sandbox, no extensions/profile credentials,
denied downloads, blocked service workers, and a credential-free fixed-command worker.
Page JavaScript stays available. A loopback IPC proxy is an internal implementation,
not an agent-accessible local-network capability. Every HTTP(S)/WebSocket connection,
including redirects, iframes and JS subrequests, goes through this proxy. All DNS
answers must be global public addresses; dial uses the numeric validated sockaddr.
Loopback proxy bypass, QUIC, non-proxied WebRTC UDP, WebTransport and DirectSockets
are disabled. Non-HTTP navigations and post-navigation private addresses are denied.
Worker idle exit closes its proxy. Sessions are keyed by profile home + internal task id.

URL policy is invocation-scoped through the existing Gateway session ContextVars and
a public-network ContextVar inherited by delegated children. Messaging ignores private
opt-outs, proxy DNS fail-open, fake-IP exemptions and trusted-private-host exceptions.
Developer/CLI policy is preserved.

web_extract keeps Parallel / Komatso Parallel. Input policy is enforced before every
provider/cache. A streaming GET uses the existing pinned-IP SSRF-safe client with proxy
environment disabled and checks every redirect before the next request. Only providers
declaring off-host public fetching can receive messaging URLs; unknown/future local
providers fail closed until their transport boundary is integrated. Provider cloud
networking is a trusted external boundary: preflight validates the observed redirect
chain, while the provider executes its own remote fetch, outside this host/LAN.
This phase does not claim IP pinning inside Parallel's service.

Skills use the native skills_readonly toolset. Both runtime configs explicitly disable
inline shell; the core also refuses inline shell on messaging even if that config regresses.
skill_view linked-file confinement remains native and tested.

Delegation inherits exact actual parent tools, including plugin tools and disabled selections.
Missing messaging parent state yields an empty ceiling, never terminal/file defaults.
Requests are bounded by that ceiling; schema snapshots and deferred tool lookup retain it.
no_mcp is propagated. Messaging children remain leaf agents even if orchestrator config
regresses. Children skip context files/memory; fixed domain workers are unchanged.

Core patch is required because plugin observer hooks cannot enforce child construction,
the native skill renderer, or the shared URL allow-private cache. The standalone local
commit and adjacent patch cover only generic policy/delegation/readonly behavior. Preserve
or reapply it deliberately in a future Hermes update; this phase performs no update.

Tests:
- Native hermetic runner, isolated test venv under runtime, no production credentials.
- New core messaging boundary tests and canonical public transport/profile tests.
- Direct public Chromium smoke plus loopback stub egress E2E (zero stub requests).
- Manual and Part Book read-only regression; no operational DB tests.
- Twelve broader-suite failures also reproduce with the backed-up original URL module:
  eight remote-fetch tests, four Windows skill-path expectations. They are outside Phase 1.
