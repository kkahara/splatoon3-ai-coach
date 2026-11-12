# Review-code recording: target player

This is the current policy for which player a review-code report coaches. It
supersedes the P1-A paragraph in `PHASE1A_REVIEW_CODE.md` (frozen) that has the
customer provide an in-game name hint and an operator confirm the target before
recording.

## Business rule

The coached player is the player whose view the Battle Memory replay opens on.
s3-coach assumes that this opening view belongs to the customer who submits the
code, because the customer is asked to submit a code for a match they
personally played.

- The customer does not name or pick a player from the eight-player roster.
- The operator does not identify, type or confirm the target player's name.
- Nothing in the workflow checks or challenges the opening-view assumption, and
  there is no player identification or perspective detection.

The submission form says:

> ご自身がプレイした試合のバトルメモリーコードを入力してください。ご提出いただいたリプレイの初期視点をもとに分析します。

## Operator recording

1. Open the submitted Battle Memory replay.
2. Leave the replay on its opening perspective.
3. Start recording.
4. Keep that perspective for the whole recording.
5. Stop recording and upload the video.

While a capture is running, the capture station panel on the admin recording
page shows the reminder "Keep the replay on its opening perspective while
recording." The station has no player-selection function.

## Workflow and database

- There is no `confirm_target` operator action. Recording, upload grant,
  upload verification, the analysis handoff and worker admission require
  payment (or a redeemed voucher), a verified contact and the acquisition
  state only.
- `migrations/002_drop_target_confirmation_check.sql` drops the P1-A CHECK
  that required `target_confirmed_at` for the `recording`, `uploading` and
  `ready` states. It finds the constraint by its definition and is idempotent;
  `RecordingStore.ensure_schema` runs it after `001`.
- The columns `target_player_name`, `target_confirmed_at`,
  `target_confirmed_by` and `confirmed_player_name` remain so that existing rows
  and their audit history are kept. New requests leave them NULL. Earlier
  `confirm_target` audit events still appear in the job history.
- The API rejects a `target_player_name` in a new request and a
  `confirm_target` action with 422.

## Customer status

Customers never see operator-confirmation wording. Once a request is paid (or
its voucher redeemed) and its contact is verified, the status reads
「録画の準備中です。」 until recording starts.

## Report privacy

The publisher's known values still include `target_player_name` and
`confirmed_player_name`, so a name stored on a row created before this policy
is still blocked from a published report. A missing name adds no matcher; the
review code, contact, display name, handoff reference, secret and canary checks
are unchanged (`REPORT_PUBLISHING.md`).
