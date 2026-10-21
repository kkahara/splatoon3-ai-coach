# Privacy and Terms follow-up

Privacy and Terms pages are not published. The footer links About and Contact only, so those pages are not linked before the copy exists.

Write the pages only after the retention note and the refund note are decided. The copy has to match the code, including behavior that was easy to understate:

- Account rows store more than a name, email, and password hash: verification time, an unsalted SHA-256 of the signup IP, role, last login, last seen, and the email-notice preference. Sessions and email-token tables store token hashes. Feedback text is stored.
- A logged-in submission always stores the account email. An empty display name falls back to the account name. Guest notice addresses are stored before they are copied onto the submission. Video records store the original filename, size, content type, duration, and dimensions.
- `s3_session` and `notice_browser` are `HttpOnly` and `SameSite=Lax`. `Secure` is set only when `PUBLIC_BASE_URL` starts with `https://`. The notice cookie lasts 365 days; the server row goes idle after 180 days. Site language and the remembered game language live in `localStorage`.
- Telemetry columns do not contain an email, a display name, prompt text, or coaching prose. Each run does store a user id that joins to the account until expiry, plus submission id, guest flag, game language, and video measurements.
- Contact messages are not written to the database or telemetry. When SMTP is unset, the complete message is written to the local outbox. When SMTP is set, the mail provider stores it. Do not say contact messages are never stored.

The Nintendo fan-project line already in the footer can stay as it is. Do not add governing-law, arbitration, or liability clauses unless they are chosen separately.
