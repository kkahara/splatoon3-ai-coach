# Japanese Coaching Style Guide — v0.1

## Purpose

Japanese coaching should communicate the same evidence-supported coaching point as the canonical English coaching.

Japanese is a localization of the coaching, not a new analysis.

The translator must never add evidence, remove important evidence, strengthen uncertainty, or invent causality.

## Tone

Use Japanese that is:

* friendly
* concise
* encouraging
* calm
* respectful
* natural for a video-game coaching context
* supportive without excessive praise

Avoid:

* scolding
* sarcasm
* humiliation
* exaggerated praise
* childish language
* overly formal business Japanese
* language that sounds like a teacher reprimanding a student

Prefer natural coaching language such as:

* `いい判断です。`
* `うまく引けています。`
* `ここは無理に戦わなかったのが良かったです。`
* `味方と合わせてスペシャルを使えています。`

Do not automatically praise every action. Positive wording should only appear when the canonical English coaching explicitly identifies the action as positive.

## Preserve evidence strength

If English says:

> You may have been able to...

Japanese must retain that uncertainty.

Do not turn it into:

> ～すべきでした。

Likewise, if English says:

> This helped your team...

do not strengthen it into:

> これが勝利につながりました。

unless that stronger causal claim is explicitly present and supported.

## Facts versus interpretation

A factual observation is not automatically praise.

Examples:

> You moved backward.

This is a fact.

> Good restraint—you moved backward instead of forcing the fight while down a player.

This is positive coaching.

Japanese should preserve that distinction:

> 後ろに下がっています。

versus:

> いい判断です。人数不利のときに無理に戦わず、引けています。

Do not turn neutral factual statements into compliments.

## Positive coaching

Positive coaching may acknowledge:

* good aim or successful execution
* useful positioning
* awareness of ally positions
* awareness of opponent positions
* awareness of player-count advantage/disadvantage
* awareness of active enemy specials
* appropriate aggression when conditions support it
* appropriate disengagement when conditions are unfavorable
* avoiding excessive pursuit
* useful splats that reduce pressure on allies
* coordinated special usage
* waiting to combine specials when that improves the evidence-supported opportunity to regain control
* successful recovery or regrouping
* other explicitly supported positive scenarios

Keep praise proportional.

Prefer:

> いい判断です。

over excessive praise such as:

> 素晴らしい！完璧な判断です！最高です！

## Deaths

A death does not automatically make the preceding action negative.

If useful evidence exists before a death, Japanese coaching may acknowledge what was done well and separately identify what could be improved.

Example:

> いい位置で戦えています。ただ、ここでは人数不利になったので、もう少し早く引けると安全でした。

Do not imply:

> 死んだので、その行動は悪かった。

unless the canonical coaching explicitly makes that evidence-supported assessment.

## Splats

A splat can be positive, but do not automatically praise every splat.

Consider the context supplied by the canonical coaching.

For example:

> 相手を1枚落として、味方の負担を減らせています。

is appropriate when the coaching identifies the splat as useful team contribution.

Do not invent claims such as:

> このキルで試合を決めました。

unless the canonical English coaching explicitly supports that claim.

## Retreat / disengagement

When the evidence supports positive disengagement, natural Japanese wording can include:

> いい引き際です。

or:

> 人数不利の中で無理に追わず、うまく引けています。

Avoid describing every retreat as good. The context must support the positive interpretation.

## Specials

When the evidence supports coordinated special usage:

> 味方とスペシャルを合わせて、中央を取り返せています。

If the evidence only shows delayed special usage, do not automatically claim that waiting was good.

The positive interpretation must come from the canonical coaching/evidence.

## Splatoon terminology

Prefer natural Japanese game terminology rather than literal English translations.

Initial terminology examples:

* splat → `キル` or `相手を倒す` depending on sentence context
* death → `デス`
* respawn → `復帰`
* special → `スペシャル`
* map → `マップ`
* center/mid → `中央`
* retreat/disengage → `引く` / `下がる`
* numbers advantage → `人数有利`
* numbers disadvantage → `人数不利`
* teammate/ally → `味方`
* opponent/enemy → `相手`
* zone → `エリア`
* regain control → `取り返す` / `奪い返す`

Use the most natural wording for the sentence rather than mechanically substituting terms.

## Concision

Japanese coaching should normally be approximately as concise as the English coaching.

Do not expand a short English coaching point into a long explanation.

A single useful coaching point is preferable to several loosely related statements.

## Preserve structure

If English contains:

1. acknowledgment
2. context
3. coaching point

Japanese should preserve that structure.

Example:

English:

> Good restraint. You backed away while down a player instead of forcing the fight.

Japanese:

> いい判断です。人数不利の中で無理に戦わず、引けています。

## No new analysis

The translation model must not:

* infer an unstated cause
* invent player intent
* invent tactical reasoning
* introduce new facts
* introduce a new coaching recommendation
* change a neutral statement into criticism
* change uncertainty into certainty
* change a positive statement into a negative one
* change a negative statement into praise

The English coaching output is the source of truth.

## Implementation notes

* Runtime prompt: `src/splatoon3_ai_coach/coach/prompts/coach_translate_ja.txt`
* Translator: `src/splatoon3_ai_coach/coach/translation.py` (receives only the finalized English coaching text)
* Fixed evidence lines (headings, roster, map, special lines) are rendered from deterministic Japanese templates in `tools/public_site/localize.py`, not by the LLM.
* Translations are cached per review in `localized/coaching.ja.json`. That file is derived presentation cache, never an analytical source of truth.
