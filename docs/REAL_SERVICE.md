# Running WORLDLINE against a real service

Until this adapter, every WORLDLINE result came from the simulator: a scenario YAML whose decision rules are strings in the predicate DSL, evaluated by `worldline.worlds.run`. The [roadmap item](ROADMAP.md) "Genome transfer across code, not just scenarios" asks whether any of it holds up once the decision is made by real request-handling code instead. This is the first step towards an answer.

## What was built

| Piece | Path | Role |
|---|---|---|
| Example service | `examples/service_demo/app.py` | FastAPI "approvals" API. `POST /reports`, `POST /corrections`, `POST /observations`, `GET /decision?at=t`, `GET /log`, `POST /reset`. Keeps an in-memory receive log per client (`X-Worldline-Client`), stamped with the virtual time from `X-Worldline-Now`. Validates input with pydantic and refuses a clock that goes backwards (409). Its decision rule is plain Python (`escalation_decision`): escalate when at least 2 credible (confidence >= 0.6), uncorrected reports were received in the last 600 s. It imports nothing from `worldline`. |
| Recorder | `worldline/adapters/http/recorder.py` | Converts Event v1 to HTTP requests (a routing table keyed by event kind) and the service's `GET /log` back to a view `EventLog`. |
| Driver | `worldline/adapters/http/driver.py` | `HttpWorld` and `HttpTrace`, described below. |
| Search | `worldline/adapters/http/search.py` | `explore_http`, `minimize_http`, `challenge_http`, `verdicts_http`. |
| CLI | `worldline/cli/http_cmd.py` | `worldline http demo`, added as a sub-app in `worldline/cli/main.py`. The existing commands are unchanged. |
| Tests | `tests/test_http_adapter.py` | 8 tests. |

### How a world is driven

1. **Truth** is the scenario's injects, built by the simulator's own `_truth_events` (here `scenarios/port_alert.yaml`).
2. **Receipt times** come from the simulator's own delivery path, `worldline.worlds.runner._deliver_role`, with the hook from `worldline.interventions.hook_for_schedule(schedule)`. That path covers the DeliveryPolicy, the per-(role, event) RNG fork, outage transitions, FIFO clamping and the horizon, and it raises `ValueError` when a schedule breaks the time rule. Nothing in it is reimplemented.
3. **Delivery.** Each surviving delivery is sent to the service through `httpx.ASGITransport`, in received order, with `X-Worldline-Now` set to the receipt time. At each decision time the driver calls `GET /decision?at=t`. A delivery received at exactly `t` is sent before that call.
4. **Recording.** The view is rebuilt from the service's own receive log, not from what the driver meant to send. If the service rejects a request (for example a corrupted confidence of -0.8 fails validation with 422), that event is missing from the view, just as it is missing from the service's state. The decision Event's `known_event_ids` are the ids the service says it had received.
5. **Checking.** The result is an `HttpTrace`, a subclass of `worldline.worlds.Trace`. `worldline.invariants.check_all` runs on it unchanged. For `decision.consistent_with_truth`, the `evaluate_rule` callable calls the service's `escalation_decision` directly (not over HTTP) on truth as of the decision time. Every HTTP exchange (path, virtual now, status) is recorded in the trace JSON with its own `exchanges_digest`.

## What this proves

- **The core tools run against real request-handling code, not a model of it.** The three-valued checker, the mutators, the exploration loop, ddmin and the genome's precondition and instantiation code all run unchanged against a FastAPI app. Every oracle call during exploration and minimization is a complete HTTP run against the service: reset, about 34 POSTs, the decision GET and the log GET.
- **The latent defect in port_alert is a property of the rule, not of the DSL.** Delay `correction-001` until after 1500 s and the service itself answers `escalate`, while its own rule evaluated over truth says `monitor`. `decision.consistent_with_truth` FAILs with the same witness the simulator produced: `[correction-001, ais-006, patrol-001]`.
- **The simulator-extracted genome transfers to the service.** `experiments/out/genome.json` was extracted from the simulator and never saw the service. Its preconditions hold on the HTTP root run. `_instantiate` turns it into "delay `correction-001` for role `service` by 91.1 s", and the service then makes the wrong decision. This is a transfer from simulator to code under one fixed truth stream.
- **The service's decisions match the DSL rule on every world tried.** Across all 85 worlds the step-5 exploration generated (root plus guided and random branches), the service's decision equals what `worldline.worlds.run` computes with the DSL rule on the same projected scenario: 85 agree, 0 disagree. This was measured once with a script, not as part of the CLI. `tests/test_http_adapter.py::test_service_agrees_with_dsl_rule_on_perturbed_worlds` keeps a 6-world version of it.
- **Runs are deterministic.** The same schedule gives the same digest, including on a freshly constructed service instance.

## What it does not prove

- **The service is an example.** Its rule was written to match the port_alert rule. Agreement with the DSL is therefore expected, and the evidence is that the plumbing is faithful, not that arbitrary services behave this way. A third-party service is the real test (see Next steps).
- **WORLDLINE still controls delivery timing.** Receipt times are computed by the simulator's delivery model and pushed into the service through a virtual-time header. The service did not experience a real network, and WORLDLINE did not observe its natural timing. What is real is the code path from request to decision: request parsing and validation, idempotent ingest, state and the rule.
- **The service has to cooperate with virtual time.** It has to accept `X-Worldline-Now` and expose a receive log. An unmodified service would need instrumentation (see Next steps).
- **One rule, one deciding role.** The service implements only `duty_officer/escalate_or_monitor`, so `HttpWorld` runs a projection of the scenario (see Compromises). The invariants about `coast_guard` and `harbour_ops` views come out UNKNOWN, which is correct: the HTTP world has no such views.
- **No immune control.** The simulator challenge runs `convoy_lostlink_immune.yaml` as a control. No immune variant of the service exists yet, so `challenge.json` has `controls: []` and states why.
- **The guided and random numbers below are not comparable to the simulator's.** The projected world has a single role, so the mutators propose different interventions and fewer invariants can fail.

## Compromises forced by Trace duck-typing

1. **Scenario projection.** `decision_dependencies`, `_instantiate`, `_check_preconditions` and the checker's `_actor_role` all look up actors and roles in a `Scenario`. The genome's `view_as_known_at(root, d["role"], ...)` indexes `trace.views` by the scenario's role name. With the required view key `"service"`, the original roles (`harbour_ops`) would raise `KeyError`. `HttpWorld` therefore builds a projected `Scenario` with the same id, seed, clock, injects and transitions, one role `service` (with harbour_ops's delivery policy by default), and the one actor whose rule the service implements, rebound to `service`. That projection is what the checker, mutators and genome see. The decision payload also carries `role: "service"`.
2. **The rule's dependencies are still declared in DSL form.** The guided strategy and the genome read the decision's dependencies (`sighting`, `correction`) by parsing the scenario's `when:` string, not from the service's code. The service's actual behaviour is checked against truth with its own function, but which events it depends on is still a declaration. Deriving it from code (tracing, or the service declaring its inputs) is open.
3. **Private imports.** The adapter imports `worldline.worlds.runner._deliver_role` and `_truth_events`, `worldline.explorer.explore._candidate_pool`, and `worldline.genome.genome._check_preconditions` and `_instantiate`. Reusing them is the point: a reimplementation could quietly diverge from the simulator. The cost is that these private names are now relied on outside their modules.
4. **Transport metadata in request bodies.** The corruption marker (`quality = 0.0`, self-provenance) and the policy labels travel in a `meta` object that the service stores verbatim, so `absence.no_corrupted_in_decision` can still be evaluated from the service's log. A real service would not carry such a field. Without it, that invariant would have to become UNKNOWN.
5. **The HTTP conversation is outside the trace digest.** `HttpTrace.digest` is the plain `Trace` digest over truth, views and decisions, so an HTTP trace JSON loads back with `Trace.from_dict` and verifies. The request/response list is reported separately as `http.exchanges_digest`. The first version hashed the exchanges into `digest`, and `Trace.from_dict` then rejected the file as altered. A rejected request still shows in `digest`, as an event missing from the view, but a change in status code alone (for example 201 vs 200) does not.
6. **Duplicate ids follow the DSL, not best practice.** A `duplicate` intervention produces `sighting-002#dup1`. The service treats it as a new report, which matches the DSL's `count(...)` semantics, so a duplicated credible report can cause an escalation. The service's idempotency guard only catches a repeated identical id.

## How to run it

```bash
uv sync --extra dev --extra service
uv run worldline http demo --budget 60 --seed 42 --out experiments/out_http
uv run pytest -q tests/test_http_adapter.py
# the service on its own:
uv run uvicorn examples.service_demo.app:app --port 8100
curl -s -X POST localhost:8100/reports -H 'X-Worldline-Now: 1002000000000' -H 'content-type: application/json' \
  -d '{"id":"r1","source_id":"patrol_boat_a","observed_at_ns":1000000000000,"confidence":0.8}'
curl -s 'localhost:8100/decision?at=1500000000000' -H 'X-Worldline-Now: 1500000000000'
```

The command needs `experiments/out/genome.json`, which the simulator pipeline (`worldline demo`) writes. Pass `--genome` to use a different file. It writes the same file names as the simulator pipeline: `traces/root.json`, `verdicts/root.json`, `exploration.json`, `minimal_schedule.json`, `challenge.json`, and traces of failing branches.

## Measured results (step 5)

Output of `worldline http demo --budget 60 --seed 42 --out experiments/out_http`, verbatim, Windows 11, Python 3.12:

```
[service] approvals-demo driven with port_alert truth via httpx ASGI transport
[root] decision ['monitor']  digest sha256:61b1fe8fb9ad90bf5ef00ef139400c3839770b0d4c7ecff9328c7dc40e600428  (35 requests, 0 rejected)
  PASS    absence.no_corrupted_in_decision
  PASS    decision.consistent_with_truth
  UNKNOWN epistemic.label_isolation
  PASS    epistemic.no_future_knowledge
  UNKNOWN response.alert_ack
[explore guided] nodes 38  failing 12  fail/100 32.4  first decision FAIL idx 4  wall 674 ms
[explore random] nodes 47  failing 7  fail/100 15.2  first decision FAIL idx 17  wall 1207 ms
[minimize] root/sha256:9f3ac3ce419f82f09aaaff165660e5877572cbfaedfdc5e34b83a072c0c8ccc0: 3 -> 1 interventions, 2 oracle calls (each a full HTTP run), one-minimal True
  drop {'event_id': 'correction-001'} {}
  service decision under minimal schedule: ['escalate']  witness ['correction-001', 'ais-006', 'patrol-001']
[challenge] genome:94f0ad69dd5483d0 preconditions_met=True reproduced=True
  instantiated: delay {'event_id': 'correction-001', 'role': 'service'} {'by_ns': 91109074390, 'role': 'service'}
  reproduced against service approvals-demo on port_alert: decision decision:duty_officer:escalate_or_monitor (escalate_or_monitor) chose 'escalate' but truth as of 1500000000000 gives 'monitor'; actor had not received ['correction-001', 'ais-006', 'patrol-001']
wrote experiments\out_http\exploration.json, experiments\out_http\minimal_schedule.json, experiments\out_http\challenge.json and traces/ verdicts/  (total 4.03 s)
```

How to read this output:
- **Root.** "35 requests" means 34 deliveries plus the decision GET; the reset and log calls are not counted. Three radar contacts fall inside the outage and are never delivered.
- **Cost per world.** A world costs about 20 to 30 ms through the ASGI transport. The simulator takes about 4 ms for the same schedule.
- **Minimization.** The minimizer selects with the same rule as `worldline minimize`. Here it chose a branch whose 1-minimal cause is *dropping* the correction. The genome challenge separately confirms the *delay* mechanism.
- **Simulator baseline.** For reference, the simulator at the same budget and seed on the full three-role scenario gives: guided 32 nodes, 11 failing, first decision FAIL at index 1; random 46 nodes, 16 failing. These count failures of more invariants over more roles, so they are not a like-for-like comparison.

## Next steps

1. **Record real traffic.** Run the service behind a recording proxy (or ASGI middleware) that logs real requests with real receipt times as Event v1. Use that recording as the root world instead of a scenario's injects. Interventions would then perturb an observed timeline rather than a declared one.
2. **Instrument a third-party service.** Take a service whose rule nobody wrote to match a DSL, for example a small open-source approvals or cache-invalidation app. Add the two hooks it needs (a virtual clock source and a receive log) as middleware without touching its business logic. Then challenge it with the simulator genome. A non-reproduction, or a disagreement with the DSL, would be the informative outcome.
3. **Derive decision dependencies from code.** Replace the declared `when:` string (compromise 2) with dependencies observed by tracing which records the decision handler reads.
4. **Build an immune service variant**, for example one that requires corrections to be acknowledged before a decision, so the challenge has a control that should not reproduce.
5. **Promote the private helpers.** Make `_deliver_role`, `_truth_events`, `_candidate_pool`, `_check_preconditions` and `_instantiate` public, so the adapter stops depending on private names.
