# Reddit commercial-use approval packet

Submission form:
[Reddit API and commercial-use request](https://support.reddithelp.com/hc/en-us/requests/new?tf_42139884615700=api_request_type_enterprise_clone&ticket_form_id=14868593862164)

Do not submit this until the legal operator name, contact email, Reddit username, and company
details are correct. Reddit's form submission is an external legal and account action.

## Subject

Commercial-use approval request for an internal, human-led opportunity monitoring tool

## Details of inquiry

I am requesting written approval for a narrowly scoped internal tool that identifies public
Reddit posts where a person is asking for help with a business problem relevant to one of four
products I operate. The tool creates a private review queue for one human operator. It does not
post, comment, vote, send direct messages, log in to Reddit accounts, rotate accounts, or take
any action on Reddit.

The human operator reads the full thread and subreddit rules, writes any response manually, and
discloses their affiliation whenever a product is mentioned. A product link is used only when it
is relevant and helpful to the original poster.

I will use only an access method approved by Reddit. The current code will remain inactive until
written approval is received, and I will migrate it to OAuth or another access method Reddit
specifies as a condition of approval.

## Role requesting access

Independent software product operator and developer.

## Current use of Reddit data

No recurring commercial collection is active. Development and qualification logic were tested
with synthetic fixtures. The repository is public for transparency and review.

## Purpose of the product or service

The tool helps one internal operator find relevant support questions and buyer-intent posts for
four products: FreshCarrier, QuoteTier, Restaurant Roster, and Revenue Recovery. It reduces
irrelevant self-promotion by requiring multiple independent relevance signals before showing a
post for manual review.

## What Reddit data will deliver to users or customers

Nothing is redistributed to customers or exposed in a customer-facing product. The only user is
the internal operator. The private alert contains the post title, URL, subreddit, limited body
context, matched terms, and the internal product it may fit.

## Data requested

- Public submission ID
- Public submission title and limited body text
- Submission URL and subreddit
- Creation timestamp
- Removal or availability status when provided

Reddit usernames are not retained by the portfolio scanner. Comments, votes, private messages,
email addresses, inferred sensitive traits, and off-platform identity data are not collected.

## Scope and frequency

The initial scope is 31 explicitly configured business and trade communities. The target polling
interval is 15 minutes, with batching, strict request ceilings, rate-limit handling, and immediate
stopping on a sustained rate-limit response. I am happy to reduce the community list or polling
frequency to meet Reddit's requirements.

## Storage, retention, and access

Data is stored locally in a private SQLite database accessible to one operator. Portfolio matches
and unreferenced submission content automatically expire after 30 days. Webhook credentials stay
outside source control. The tool does not train or fine-tune any model with Reddit data.

## Benefit to Redditors

The workflow is designed to produce fewer and more relevant replies. It prevents automated
engagement, reminds the operator to disclose their affiliation, and requires a meaningful match
before a thread is surfaced. Redditors retain the choice to engage, ignore, report, or block the
human account normally.

## Source code

https://github.com/lawrence18365/reddit-opportunity-router

## Intended communities

The active list is documented in `config/portfolio.yml`. It covers trucking and freight,
service-business quoting, restaurant operations, and UK construction or home-improvement
businesses. The application can include the exact current list as an attachment if requested.

## Fields requiring operator input

- Legal operator or company name
- Contact and corporate email address
- Reddit username
- Developer username, if different
- Company website and description
- Company size
- Data budget, if Reddit requires a paid agreement
