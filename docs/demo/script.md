# WORLDLINE demo script

About 400 words, roughly 2 minutes 45 seconds at a calm pace. Tabs to have open, in order: the Story page, GitLab merge request 1, the release page, a terminal. Every number is on screen when you say it.

---

**[Tab: Story, landing]**

Every green pipeline tells you the same lie: the tests passed in one world. The world where the message arrived on time. Real systems fail in the other worlds, where a correction lands ninety seconds late and someone decides without it. WORLDLINE finds those worlds before they ship.

**[Tab: Story, step 1 "Replay"]**

One hour of a harbour alert, replayed 133 times. Each replay nudges when messages arrive. Red worlds broke a safety rule: 43 of them. Guided search found a wrong decision in the sixth world. Random search never did.

**[Tab: Story, step 2 "The break"]**

This is the world that broke. Three lanes: what happened, what the duty officer knew, what the coast guard knew. Watch the correction. It arrives after the officer decides. He escalates on a radar ghost that had already been retracted. Not a bug in his code. A bug in what he knew.

**[Tab: Story, step 3 "One cause"]**

Three changes produced that world. Delta debugging strips it to one and proves nothing smaller works: hold back a single correction. The exact threshold is computed, not guessed: 90.2 seconds.

**[Tab: Story, step 4 "Pattern"]**

Now the part no test suite does. The cause is abstracted into a mechanism with no names in it: the last correction a decider receives before deciding, delayed past the decision.

**[Tab: Story, step 5 "Elsewhere"]**

That mechanism is fired at systems it has never seen. A logistics convoy: reproduced. A hospital triage board: reproduced. The real USGS record of the Kamchatka magnitude 8.8, with its real magnitude revisions: reproduced. A live HTTP service, driven request by request: reproduced. The immune twin, built to resist it: correctly not reproduced.

**[Tab: GitLab, merge request 1]**

Inside GitLab, agents do the work and never grade it. The Investigator reproduced the failure in a hosted session. The Repairer opened this merge request. Code Review caught that the fix was vacuous. The Repairer fixed that. Our Skeptic still failed it, correctly. The merge request stays open, and that is the point.

**[Tab: Story, step 6 "Evidence"]**

Everything the pipeline claims is typed: validated, attested, failed, unknown. Thirty-one records, signed. The kernel recomputes every claim before attesting it. Flip to the edited copy: one record changed, the release is denied, and the broken record is named.

**[Tab: Story, step 7 "Verify"]**

Anyone can check it. Drop a bundle, press verify. Intact. Check the release gate against the commit. Authorized. No account, no trust in us, only a public key.

**[Tab: Release page]**

Every release ships its evidence as assets, publishes an installable tool, and can be verified offline from a plain download.

**[Tab: Terminal]**

`uv tool install worldline`. `worldline demo`. Authorized. Then the tampered bundle: denied.

**[Tab: Story, landing]**

WORLDLINE. Catch decisions made on late information, before they ship.

---

Delivery notes: pause one beat after "A bug in what he knew." and after "and that is the point." Keep the cursor still while speaking; move it only between sentences. If a live call is slow, keep talking; the result lands before the next tab.
