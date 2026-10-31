# Clam basket bounded temporal facts

Status: analysis-only factual models and pure constructors. There is no
persistence, GameEvent lookup, ScenarioContext, ranking, or coaching consumer.

## Source and scope

These facts consume one `BasketOpenRun` from an available or partial
`BasketOpenRunsArtifact`. They require independently resolved Clam Blitz mode.
Every fact retains the source analysis identity, complete manifest fingerprint,
deterministic source-run identity, artifact availability and limitations, and
the observation evidence IDs.

Source availability and fact availability are separate. A fact may be
`available` when its source artifact is `partial`: an unrelated coverage gap
elsewhere in the match does not weaken the samples and bounds of this run. The
fact preserves both statuses so a later consumer can apply its own coverage
policy. An unavailable source artifact cannot produce facts.

## Observed-open span

`BasketObservedOpenSpan.observed_span_seconds` is exactly:

```text
last_observed_open_time - first_observed_open_time
```

It is elapsed video time between the boundary open observations. It is not a
minimum open duration and does not prove the basket remained open between those
observations. A single observed-open sample has an observed span of zero.

When bounded by observations, opening and closing transitions use the interval:

```text
(lower observation time, upper observation time]
```

The lower endpoint is exclusive because the earlier sample still showed the
prior state. The upper endpoint is inclusive because the later sample already
showed the new state. In other words, the transition occurred after the
lower-state observation and no later than the upper-state observation. The open
interval between the observations is where its exact time is unresolved; the
upper observation establishes that it had occurred by then. Censored sides have
no finite transition bounds and retain the source censor reason.

For action-time classification, an action exactly at an observation timestamp
is classified using that observed boundary rather than as merely inside an
unresolved transition window. Thus the first observed-open timestamp is an
observed-open boundary, and the following observed-closed timestamp is at or
after the observed closed boundary. This precedence does not change the
transition-time interval `(lower, upper]`; it records the stronger evidence
available at the sampled endpoint.

## Closing-time bounds

`BasketClosingTimeBounds` is available only when the run has a following closed
observation. Its bounds are `(last observed open, following observed closed]`.
Without that sample, the fact is unavailable and preserves the right-censor
reason. It never estimates seconds remaining.

## Action-time relation

`BasketActionTemporalRelation` compares an explicitly supplied video timestamp
to one run. It performs no GameEvent lookup and makes no claim about the action,
player, team, or scoring opportunity. Its closed vocabulary distinguishes:

- at or before a preceding closed observation;
- within an opening transition window;
- exactly at the first or last observed-open boundary;
- between observed-open boundaries, with continuity explicitly unproven;
- within a closing transition window;
- at or after a following closed observation;
- indeterminate on a censored side.

It deliberately has no `during_basket_open` relation.

## Prohibited interpretations and integrations

This layer does not provide continuous-open duration, seconds remaining,
player/team attribution, scoring-opportunity judgments, action-relative
GameEvent facts, ScenarioContext evidence, ranking, or coaching language.
Those require separate evidence-contract authorization.
