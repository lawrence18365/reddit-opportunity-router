# Reddit Opportunity Router

This fork turns Subscope into one human-led opportunity desk for a portfolio of products.
It finds and qualifies Reddit posts, alerts you immediately after a match is found, and
leaves every Reddit action to you. It never signs in, comments, sends DMs, votes, or rotates
accounts.

## Rollout

The active order in `config/portfolio.yml` is:

1. FreshCarrier, free access to 10 fresh carrier leads
2. QuoteTier, free Good, Better, Best quoting
3. Restaurant Roster, 14-day free trial
4. Revenue Recovery, free quote leakage audit

Bridal OS, RateTap Mexico, and Affordable Email Marketing are seeded as disabled profiles.
Enable them only after one of the first four products has a repeatable conversion loop.

## Quick start

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'

.venv/bin/subscope portfolio validate
.venv/bin/subscope portfolio route --input examples/sample-posts.json
.venv/bin/subscope portfolio status
```

The route command is offline. It is the safest way to tune signals before fetching anything.

## Notifications

macOS desktop notifications are active by default. Slack, Discord, Telegram, and generic
webhooks activate only when their environment variables exist.

```bash
mkdir -p ~/.config/subscope
cp config/notifications.env.example ~/.config/subscope/notifications.env
chmod 600 ~/.config/subscope/notifications.env
# Edit that private file and uncomment the channel variables you want.

.venv/bin/subscope portfolio test-notification
```

Webhook secrets stay outside the repository. Each delivered alert is deduplicated by Reddit
post, project, and channel. Failed deliveries remain retryable.

Portfolio matches and unreferenced post content expire after 30 days. Reddit usernames are not
retained by the portfolio scanner.

## Scan once

```bash
.venv/bin/subscope portfolio scan
```

A scan batches the configured communities into a small number of public RSS reads. Every new
match is written to local SQLite and sent to each configured notification channel immediately.
The output contains the matched signals, a score, the right free-trial or free-audit CTA, and
a tracked URL with product, campaign, and Reddit post attribution.

Use `--dry-run --no-notify` while tuning. Dry runs do not update cursors or SQLite state.

For high-intent discovery across the last seven days, use the focused search mode. This is the
mode used by the background watcher because busy community feeds can crowd out relevant posts.
Searches include the target subreddit names in the Reddit query so unrelated communities cannot
consume the 100-result feed. Matches are labelled `high_intent` or `review`; the review tier keeps
useful near-matches visible without pretending they cleared the strict conversion threshold.

```bash
.venv/bin/subscope portfolio search --days 7
```

Each subreddit can set `cta_policy: profile_only`. Matches from those communities suppress the
product link and tell the operator to provide a useful answer while keeping affiliation details
in the Reddit profile.

## Run continuously on macOS

The included LaunchAgent scans every 15 minutes. Reddit's current
[Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)
says commercial Reddit-data use requires explicit written approval. Do not enable scheduled
commercial monitoring until your intended use is approved.

```bash
./scripts/install-macos-watch.sh
```

Stop it with:

```bash
./scripts/uninstall-macos-watch.sh
```

The uninstall script moves the LaunchAgent file to Trash. Match history remains in the local
SQLite database.

## Human reply rules

- Read the whole thread and the community rules before replying.
- Help first. Do not force a CTA into a thread that only needs an answer.
- If the product is relevant, disclose that you built it or run the service.
- Link only when it genuinely helps the original poster.
- Never automate Reddit comments, DMs, votes, logins, or account behavior.
- Record trial or audit conversions against the UTM campaign in the alert link.

## Add a product

Copy one project block in `config/portfolio.yml`. Give it a unique lowercase `id`, a rollout
priority, an offer, target communities, and signals for pain, intent, competitor, context, and
audience. Keep `enabled: false` until the offline fixtures produce precise matches.

The two-group minimum is intentional. A generic word such as `quote`, `schedule`, or `leads`
cannot alert by itself.
