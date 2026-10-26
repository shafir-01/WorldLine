# Demo video shot list (target 2:40, hard limit 3:00)

Record at 1440x900 or 1920x1080, system audio off, voice over. Nothing staged: every screen shows the committed pipeline output or a live GitLab page. If a number on screen differs from the script, read the screen.

| # | Time | Screen | Voice |
|---|---|---|---|
| 1 | 0:00–0:12 | README hero in GitLab, scroll to the headline table | "A green pipeline tells you the tests passed in one world. WORLDLINE asks what the system does in the others, and which difference actually matters." |
| 2 | 0:12–0:30 | Viewer, Worlds view, guided tab, hover failing nodes | "One deterministic history of a port security incident branches into alternative worlds: delayed reports, dropped acknowledgements, corrupted corrections. Red nodes violate a guarantee. The comparison table: guided exploration, 23 failing of 69; random, 20 of 66 and no decision failure in budget." |
| 3 | 0:30–0:55 | Viewer, Timeline of the minimal branch, compare-with-root on, scrub to 1500 s | "Three roles see the same truth at different latencies. Here the duty officer decides at 1500 seconds. In this world the correction arrived five minutes late, so the rule escalates on stale information. The ringed events are the witness: truth the officer had not received." |
| 4 | 0:55–1:15 | Viewer, Mechanism view, ddmin card and genome card | "Delta debugging reduced three interventions to one and verified it is minimal. The mechanism is abstracted: delay the last correcting report a deciding role received, until after its decision. No event ids. That is the failure genome." |
| 5 | 1:15–1:35 | Mechanism view, challenge card, scroll to immune control | "We point it at a second scenario, a logistics convoy, written without knowing the defect. Reproduced: the retraction delayed to the operations centre flips continue to reroute. The immune variant, which needs two reports, correctly does not reproduce." |
| 6 | 1:35–1:55 | GitLab issue 1, then Automate > Sessions, open the Investigator session | "In GitLab, the witness is an issue. A Duo Investigator agent runs the exploration itself in a hosted session and posts the report. The Repairer opens a merge request under a contract: make the failing branch pass, weaken nothing, add a regression scenario." |
| 7 | 1:55–2:20 | Pipeline page: test, explore, kernel-verify, authorize; then Viewer Evidence view, AUTHORIZED | "The kernel stage recomputes every claim the agents made before attesting it. Thirty-one records, sixteen attested by the kernel, zero mismatches. The exact commit is authorized against a policy over evidence kinds." |
| 8 | 2:20–2:35 | Evidence view, toggle DENIED, tampered record flagged | "Edit one record in the bundle, and authorization is denied with the broken link named. An agent saying the tests passed is not evidence until something independent recomputes it." |
| 9 | 2:35–2:45 | README claim-ceiling table | "Everything here is empirically validated on synthetic scenarios, and the README says exactly what is not claimed. WORLDLINE, on GitLab." |

Capture notes: the viewer is at https://worldline-3e380d.gitlab.io; the session page is https://gitlab.com/shafir2/WorldLine/-/automate/agent-sessions/10080962; the DENIED file is `authorization_denied.json`. Trim silence; no music needed.
