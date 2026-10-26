# Product scenarios

Scenarios directly under `scenarios/` are laboratory specimens: deliberately defective and frozen so the explorer, the minimizer and the genome pipeline always have something to discover. A repair never edits a specimen. It lands here, on a copy with the same file name, which is the version a deployment would ship.

Why the split exists: the first agent repair (merge request 1) could only satisfy the repair contract by forking the specimen into a regression scenario, and the Skeptic rightly flagged that the fix never reached the target. Keeping specimen and product apart makes "the defect is still demonstrable" and "the product is repaired" simultaneously true and testable. A product scenario must pass the invariant under the specimen's minimal fault schedule, and a test must show the specimen still fails under it.

This directory is empty until the first accepted repair.
