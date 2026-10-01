# negahban

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
  `instagram_business_basic` + `instagram_business_manage_comments` permissions. In
  development mode this works for accounts you add as **Instagram Testers**; no App Review.
- An Anthropic API key (or an `ant auth login` profile).
- [`uv`](https://docs.astral.sh/uv/).

## Setup

```bash
uv sync
cp .env.example .env    # fill in IG_APP_ID / IG_APP_SECRET (the *Instagram* app ones)
```

Get a token: on the app's use case page (_API setup with Instagram login_) add your account
as a tester, then **Generate access tokens**. Store it:

```bash
uv run negahban auth --token 'IGAA...'
```

`auth` exchanges it for a 60-day token, saves it to `token.json`, and prints which account it
belongs to. Later runs refresh the token automatically when it is a week from expiring.

## Usage

```bash
uv run negahban scan                   # judge new comments on the 10 latest posts, dry run
uv run negahban scan --max-posts 0     # ... on all posts
uv run negahban scan --apply           # hide what the policy says to hide
uv run negahban log                    # recent judgements
uv run negahban log --pending          # hides/deletes decided but not yet applied
uv run negahban unhide <comment-id>    # undo a hide
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
| `--token-file PATH`    | `token.json`  | Where the access token is kept.                   |

## Layout

```
src/negahban/
  graph.py     Instagram API client: media, comments, hide/unhide/delete, token refresh
  classify.py  Claude classifier — batched, structured output, caption as context
  policy.py    verdict + confidence + allowlist -> action
  audit.py     SQLite decision log; makes runs idempotent and reversible
  report.py    terminal tables
  cli.py       auth / scan / unhide / log
```

## Not in v1

- Facebook Page comments (the Graph layer is the same shape; a second adapter).
- Webhooks — Meta only delivers them to published apps, so negahban polls.
- Auto-replies to flagged comments.
