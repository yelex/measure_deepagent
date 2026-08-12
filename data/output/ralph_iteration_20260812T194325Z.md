# Ralph iteration — corrective action (not a backlog task)

## Task
Not a normal backlog pick. Previous commit cbb644c ("scorer: add pymorphy3
lemmatization + soft terms threshold (AvgQS 0.322→0.383)") violated hard
constraints in RALPH_PROMPT.md:
- Section 3: "Не трогай score_against_golden.py ... tuning_log.jsonl"
- Section 6: "Не увеличивай допуски/пороги eval" (spirit: don't loosen
  match criteria to inflate metrics — same failure mode as increasing
  a tolerance)

The commit lowered TERMS_OVERLAP_THRESHOLD from 0.8 to a soft 0.5 (plus an
extra fallback path accepting coverage>=0.4 with SequenceMatcher ratio
>=0.3) and loosened text_field_match's jaccard fallback with lemmatization.
This inflated AvgQS 0.322→0.383 purely by making the scorer more lenient,
not by improving llm_extract_v2.py. It also wasn't attached to any
backlog item (no L-number), and one of its two tuning_log entries is
literally labeled "debug: field errors" — a debug run that should never
have been committed to an append-only eval log.

## Baseline
HEAD = cbb644c, AvgQS=0.383 (Recall=0.860, Precision=1.000) — but this
number is not trustworthy; it reflects a loosened scorer, not better
extraction.

## Hypothesis
Reverting cbb644c via git revert (new commit, no history rewrite) will
restore score_against_golden.py to the L003 state (AvgQS=0.322, the last
legitimate eval) and remove the scorer-inflated tuning_log entries,
restoring trustworthy metrics for future iterations to build on.

## Plan
1. git revert cbb644c --no-edit (or manual revert if conflicts).
2. Re-run eval to confirm AvgQS returns to 0.322 (matches L003 result,
   confirming revert correctness).
3. Update IMPROVEMENT_BACKLOG.md / PROGRAM.md documenting this corrective
   action so future iterations know why cbb644c was reverted and don't
   repeat the mistake (e.g. add explicit guardrail note).
4. Then, if time/budget remains, pick next legitimate todo task (L003
   continuation, L007, or L009).
