<h1 align="center">negahban</h1>

<p align="center">
  <img src="assets/negahban.png" alt="negahban — a panda on sentry duty" width="256" />
</p>

<p align="center">
  <a href="https://github.com/parham-alvani/negahban/actions/workflows/lint.yml"><img alt="lint &amp; test" src="https://img.shields.io/github/actions/workflow/status/parham-alvani/negahban/lint.yml?label=lint%20%26%20test&logo=github&style=for-the-badge&branch=main" /></a>
  <img alt="python" src="https://img.shields.io/badge/python-3.14%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" />
</p>

_نگهبان_ — a watchman for the comments on your own Instagram posts.

It reads new comments through Meta's **official Instagram API**, asks Claude what each one
is (abuse, spam, a scam, a civil complaint, or just fine), and hides the abusive and junk
ones. Every judgement is written to a local audit log so you can see what was done, why,
and undo it.

Instagram's built-in _Hidden Words_ already filters keyword lists. negahban is for what a
list can't do: sarcasm, slang and Finglish, and telling a genuine complaint from an insult.

## What it does

| Label            | Default action | Meaning                                                  |
| ---------------- | -------------- | -------------------------------------------------------- |
| `ok`             | nothing        | harmless, including jokes and teasing                    |
| `negative_legit` | **flag only**  | critical but civil — feedback, never hidden              |
| `insult`         | hide           | name-calling aimed at you or another commenter           |
| `harassment`     | hide           | threats, intimidation, doxxing                           |
| `hate`           | hide           | attacks on a group                                       |
| `sexual`         | hide           | unsolicited sexual remarks                               |
| `spam`           | hide           | promotion, follow-for-follow, engagement bait            |
| `scam`           | hide           | phishing, fake giveaways, "DM me to earn", impersonation |

Guard rails, on purpose:

- **Dry run by default.** `scan` only reports; `scan --apply` acts.
- **Hide, don't delete.** Hiding is reversible. `--delete-junk` opts in to deleting spam/scam.
- **Confidence threshold.** Anything the model is unsure about (below `--hide-threshold`,
  default 0.8) is flagged for you instead of acted on.
- **Allowlist.** Usernames in `NEGAHBAN_ALLOWLIST` are never touched.
- **Audit log.** `negahban.db` records every verdict and action; `negahban unhide <id>` reverses.

## Requirements

- A **professional** Instagram account (Business or Creator). Personal accounts cannot use
  the API — convert in Instagram's settings first.
- A Meta app with the _Manage messaging & content on Instagram_ use case and the
  `instagram_business_basic` + `instagram_business_manage_comments` permissions, **published**
  (Live mode). No App Review or Business Verification is needed to moderate your own account —
  Standard Access covers that — but the app must be live: in Development mode Meta only returns
  data created by people with a role on the app, so `GET /{media}/comments` comes back empty
  for everyone else's comments while hide/delete still succeed.
- An Anthropic API key.
- Python 3.14 and [`uv`](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
cp .env.example .env    # fill in IG_APP_ID (the *Instagram* app id) and the gopass entries
```

Secrets live in [gopass](https://www.gopass.pw/), not in `.env`:

| gopass entry (your choice of path)  | first line            | set in `.env` as              |
| ----------------------------------- | --------------------- | ----------------------------- |
| e.g. `token/meta/main-instagram-app`| the Instagram app secret | `NEGAHBAN_GOPASS_APP_SECRET` |
| e.g. `token/instagram/your.username`| the access token      | `NEGAHBAN_GOPASS_TOKEN`       |
| e.g. `token/anthropic/negahban`     | the Anthropic API key | `NEGAHBAN_GOPASS_ANTHROPIC_KEY` (else the SDK's `ANTHROPIC_API_KEY`) |

Connect the account with Instagram's own login page:

```bash
uv run negahban login
```

It opens Instagram, you approve the two permissions, and Instagram sends the browser to
negahban's [callback page](https://elaheh-dastan.github.io/negahban/callback/), which shows a
one-time code to paste back into the terminal. The code becomes a verified 60-day token in the
gopass entry (with an `expires_at:` line). Later runs refresh it in place when it is a week
from expiring.

Alternative without the browser flow: on the app's use case page (_API setup with Instagram
login_) add your account as a tester, accept the invite in the Instagram app, press
**Generate token**, put it in the gopass entry and run `uv run negahban auth`.

Without gopass: set `IG_APP_SECRET` in `.env`, leave `NEGAHBAN_GOPASS_TOKEN` unset, and seed
the token with `negahban auth --token 'IGAA...'` — it is then kept in `token.json`.

## Usage

```bash
uv run negahban scan                   # judge new comments on the 10 latest posts, dry run
uv run negahban scan --max-posts 0     # ... on all posts
uv run negahban scan --apply           # hide what the policy says to hide
uv run negahban log                    # recent judgements
uv run negahban log --pending          # hides/deletes decided but not yet applied
uv run negahban unhide <comment-id>    # undo a hide
uv run negahban hide <comment-id>      # hide one comment by hand, bypassing the classifier
```

Comments already in the log are skipped on later scans, so a `scan` run from cron every
15 minutes only pays for new comments. Use `--rescan` to re-judge everything.

### Options for `scan`

| Flag                   | Default | Purpose                                                 |
| ---------------------- | ------- | ------------------------------------------------------- |
| `--max-posts N`        | `10`    | Recent posts to inspect (`0` = all).                    |
| `--apply`              | off     | Carry out hides/deletes. Otherwise a dry run.           |
| `--hide-threshold X`   | `0.8`   | Minimum confidence before hiding; below it, flag only.  |
| `--delete-junk`        | off     | Delete spam/scam instead of hiding.                     |
| `--rescan`             | off     | Re-judge comments already in the audit log.             |
| `--db PATH`            | `negahban.db` | Audit log location.                               |
| `--token-file PATH`    | `token.json`  | Token file, only when `NEGAHBAN_GOPASS_TOKEN` is unset. |

## Layout

```
src/negahban/
  graph.py     Instagram API client: media, comments, hide/unhide/delete, token refresh
  secrets.py   gopass access and the token stores (gopass entry or token.json)
  classify.py  Claude classifier — batched, structured output, caption as context
  policy.py    verdict + confidence + allowlist -> action
  audit.py     SQLite decision log; makes runs idempotent and reversible
  report.py    terminal tables
  cli.py       login / auth / scan / hide / unhide / log
```

## Not in v1

- Facebook Page comments (the Graph layer is the same shape; a second adapter).
- Webhooks — they need a public HTTPS endpoint; negahban stays serverless and polls instead.
- Auto-replies to flagged comments.
