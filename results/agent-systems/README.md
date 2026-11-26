# Agent systems

These runs measure a tool plus a model, not a standalone model. They are intentionally
outside `results/p95-core` and are never inputs to the model leaderboard generator.
JMeter-only results do not supply a full-pack board score or affect model rankings,
value frontiers or heatmaps.

## Feather Wand RC1 + Sonnet 5.5

`p95-core/feather-wand-rc1-sonnet-5-5` contains all 26 valid JMeter tasks from the
Feather Wand v4.0.1-RC1 GUI Agent-mode run with Claude Sonnet 5.5 at high thinking.
Each task started from a fresh plan and conversation. Saved JMX plans were saved
through the JMeter 5.6.3 GUI. Analysis submissions are the verbatim final assistant
text from the native conversation, without JSON extraction or corrections. The
earlier valid c03 task is included once.

Every saved initial submission and all eight recorded GUI repairs were regraded
offline using the merged-main revision recorded in `metadata.json`. No provider
calls or new repairs were made. Repairs retain the actual historical Core trigger
and exact feedback, including where the merged numeric-property validator now
accepts an initial artifact that the original grader rejected. Final scores use
the recorded repair, not best-of-two selection.

The merged-main regrade at `e4105e1dd7d1b25f59ab43de69f1e05d4629efc3` scores
**65.9722 first attempt / 66.0417 after repair**. This differs from the earlier
informational patched result of 66.0684 / 65.8333 only on m05: the new native
executions score its initial plan 30.2778 rather than 32.7778, and its repair
32.0833 rather than 26.6667. The new execution grades, not the historical scores,
are published. Scored-run cost remains $9.9910393 for 570,217 output tokens and
581 native model turns.

The full, unchanged Core snapshot is stored in `pack.json`. The run has complete
JMeter coverage, not complete four-tool coverage. Scores are arithmetic averages
of the 26 task scores, matching the board's JMeter `toolScore` calculation, without
the full-board efficiency adjustment. Site builds reject manifest mismatches.

Cost includes every successful native model turn for these valid submissions and
their recorded repairs. Sonnet 5.5 list prices per million tokens are $2 fresh
input, $0.10 cache reads, $2.50 five-minute cache writes and $10 output. Reasoning
tokens are included in output and are not charged twice. Extra known spend of
$3.67 on discarded starts and credit-failed partial attempts is disclosed but is
not included in the comparison. Failed turns with no returned usage are unpriced.
Excluded and credit-failed attempts are not published.

The export can be reproduced from the private GUI artifacts, without provider
access, using `python -m scripts.publish_feather_wand`. Native execution evidence
is kept separately; only attempts, scores, usage, the manifest and provenance are
published. Local paths, session logs and provider credentials are not published.

## Feather Wand 4.0.1 + PR #278 + Terra

`p95-core/feather-wand-401-pr278-terra` contains all 26 valid JMeter tasks from the
Feather Wand v4.0.1 GUI Agent-mode run with OpenAI GPT-6.1 Terra at high reasoning,
using the locally built jar from jmeter-ai PR #278. Each task started from a fresh
plan and conversation. Saved JMX plans were saved through the JMeter 5.6.3 GUI.
Analysis submissions are the verbatim final assistant text from the native
conversation, without JSON extraction or corrections. Destructive action
confirmations were disabled for unattended GUI execution.

Every saved initial submission was regraded offline using the revision recorded in
`metadata.json`. No provider calls or new repairs were made. No recorded repair met
the historical Core trigger, so first-attempt and after-repair scores are identical:
**69.0385 / 69.0385**. Scored-run cost is $6.3951304 for 312,473 output tokens and
3,263 native model turns. Low-scoring two-turn execution tasks are valid model
outcomes, usually immediate refusals or responses that made no meaningful plan
change, not harness omissions.

The full, unchanged Core snapshot is stored in `pack.json`. The run has complete
JMeter coverage, not complete four-tool coverage. Scores are arithmetic averages
of the 26 task scores, matching the board's JMeter `toolScore` calculation, without
the full-board efficiency adjustment. Site builds reject manifest mismatches.

Cost includes every successful native model turn for these valid submissions. Terra
list prices per million tokens are $1 fresh input, $0.05 cache reads, $1.25 cache
writes with a 30-minute TTL and $5 output. Reasoning tokens are included in output
and are not charged twice. Spend from discarded harness-development starts and
VM-sleep-interrupted attempts is excluded; some of that spend is unknown because
interrupted native usage was not retained. Failed turns with no returned usage are
unpriced. Excluded attempts are not published.

The export can be reproduced from the private GUI artifacts, without provider access,
using `python -m scripts.publish_feather_wand` with the model, pricing, execution and
cost-note arguments matching `metadata.json`.
