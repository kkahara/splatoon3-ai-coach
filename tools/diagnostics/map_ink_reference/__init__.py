"""Amendment 3 map-ink correctness reference tooling (Alignment v4, Track A 4b).

Implements the sealed procedure in
``analysis/review_validation/alignment/v4/DEVELOPMENT_PREREGISTRATION.md``
(Amendment 3, commit ``228e037``, as clarified by Amendment 4, commit
``570f188``). Nothing here changes production code.

Module groups:

- Blind (frame selection, extraction, point sampling, annotation, gate):
  ``population``, ``plan``, ``stage_identity``, ``domain``, ``points``,
  ``extraction``, ``render``, ``queueing``, ``session``, ``records``,
  ``server``, ``gate``, ``pilot``, ``trigger``, ``pipeline_blind``.
  These never load calibrations, map-ink observations, classifier output,
  comparison results (other than the sealed Stage 2 trigger), earlier
  team-identity answers or the A/B mapping.
- Derivation (step 2a): ``intro_latch`` replays stored ``match_intro``
  readings through the pinned production ``MatchIdentityTracker``.
- Unblinded (only after sealing): ``unblinded_pins``, ``mapping``,
  ``comparison``, ``statistics``, ``verdicts``, ``pipeline_unblinded``,
  ``criterion_4a``, ``report``.
- Neutral helpers: ``constants``, ``errors``, ``io_utils``, ``pins``, ``seal``.
- Entry boundary: ``cli`` (and ``__main__``).
"""
