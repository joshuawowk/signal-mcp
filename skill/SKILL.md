---
name: signal-josh
description: >
  Message Josh on Signal — task-done pings, blocking questions, permission requests.
  Use whenever the user says things like "let me know when this is done", "ping me when
  …", "text me if you hit X", "ask Josh whether …", "I need permission before you …",
  or any phrase implying out-of-band notification on Signal. Also use proactively, with
  no prompt, when (a) a long-running task completes, (b) the agent is blocked on a
  decision only Josh can make, or (c) the agent is about to do something irreversible
  or high-impact (deletions, prod changes, spending money, sending external email).
  Routes through the `signal_send_message`, `signal_send_question`,
  `signal_send_permission_request`, `signal_check_replies`, and `signal_health` tools
  exposed by the signal-josh MCP server. Do NOT use for general chit-chat or marketing —
  this is a notification channel, not a chat surface.
---

# signal-josh

Reach Josh on Signal when out-of-band notification is appropriate. The MCP server lives
in `~/Repos/signal-mcp` and uses the host's existing `signal-cli` daemon (account
+14013752241). Josh's personal number is preconfigured as the default recipient.


## When to ping (decision tree)

1. **Task done?** → `signal_send_message`. Use for the end of any task that ran longer
   than a few minutes or that Josh asked to be notified about. One short paragraph:
   what ran, what came out of it, and a pointer to the file or PR.
2. **Blocked on a question only Josh can answer?** → `signal_send_question`. Use when
   the agent genuinely cannot proceed: ambiguous requirements, missing input, a fork
   in the plan. Include the question, the options (if there are clear ones), and any
   context Josh needs to decide without opening Claude.
3. **About to do something irreversible / high-impact?** → `signal_send_permission_request`.
   Use for deletions, production changes, spending money, sending external email, opening
   ports, granting access, anything Josh would normally want to confirm. Then poll
   `signal_check_replies` and wait for explicit YES before proceeding.
4. **Polling for a reply** → `signal_check_replies`. Filter by `session_id` so you
   don't pick up unrelated traffic. Reasonable cadence: every 10–30 seconds for up to
   a few minutes. If no reply by then, stop polling and tell the user in chat instead.
5. **Diagnostics** → `signal_health`. Run first if any other tool returns an error.

## Tone and format

- **Concise.** One short paragraph. Signal previews are ~100 chars on lock screens.
- **Lead with the verb / outcome.** "Done: migrated 1,240 rows…", "Blocked: which env…",
  "Permission: delete S3 bucket `foo`?".
- **Include task context.** Name the project / file / ticket so Josh can act without
  asking back.
- **Always pass `session_id`** if the caller has one. It's prefixed in `[brackets]`
  and lets `signal_check_replies` filter cleanly later. If no session_id is in the
  context, generate a short slug like `claude-2026-05-19-a7b3`.
- **No emojis** unless Josh's preceding chat or the surrounding context uses them.
- **No reassurances or polite chat** ("hope this helps!", "let me know if…"). Just the
  facts.


## Examples

Good — task done:

    [claude-2026-05-19-a7b3] Done: rebuilt the Q2 close pack in
    ~/Claude/Projects/Finance/2026-Q2-close.docx. Variance section has 3 unexplained
    items flagged.

Good — blocked:

    QUESTION [claude-2026-05-19-a7b3]
    Which Epicor environment should I run the BAQ in — pilot or live?

    1) pilot.example.com
    2) live.example.com

    Reply with the number or text.

Good — permission:

    PERMISSION [claude-2026-05-19-a7b3]
    Delete the snapshot `t630-2026-04-30` from the libvirt pool.

    Context:
    Frees ~80 GB. The snapshot is 18 days old; you said snapshots older
    than 14 days are safe to drop. No VMs depend on it.

    Reply YES / NO / details.

Bad:

    Hey Josh just wanted to let you know the thing is done :) hope that's helpful!

## Operational notes

- Replies are pulled by a polling subscriber (the container's PID 1). Expect up to
  ~5 s of lag between Josh's reply landing and `signal_check_replies` returning it.
- If `signal_health` returns `daemon_ok: false`, the host's `signal-cli-daemon.service`
  is down. Tell Josh; he runs `systemctl --user restart signal-cli-daemon.service`.
- Never call `signal_send_*` in a loop. One message per logical event.
