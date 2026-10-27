# Slack source (`slack`)

Manifest: `src/omniops/sources/manifests/slack.yaml` (11 tables, read-only)
Fixtures: `src/omniops/sources/fixtures/slack/`
API reference root: https://docs.slack.dev/reference/methods (Web API, `https://slack.com/api/<method>`)

Checked against the method reference pages on docs.slack.dev on 2026-10-27. Every fixture is a
"Typical success response" or "Typical error response" block of the method's page, unmodified.

## Tables -> methods -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `slack_channel_history` | GET `/api/conversations.history?channel=&oldest=&latest=&inclusive=true&limit=200&cursor=` | https://docs.slack.dev/reference/methods/conversations.history |
| `slack_thread_replies` | GET `/api/conversations.replies?channel=&ts=&limit=200&cursor=` | https://docs.slack.dev/reference/methods/conversations.replies |
| `slack_pins` | GET `/api/pins.list?channel=` | https://docs.slack.dev/reference/methods/pins.list |
| `slack_search_messages` | GET `/api/search.messages?query=... after:YYYY-MM-DD before:YYYY-MM-DD&sort=timestamp&sort_dir=desc&count=100&page=` (user token) | https://docs.slack.dev/reference/methods/search.messages |
| `slack_search_context` | POST `/api/assistant.search.context` JSON `{query, content_types, channel_types, include_bots, sort, sort_dir, after, before, limit, cursor, action_token}` | https://docs.slack.dev/reference/methods/assistant.search.context |
| `slack_channels` | GET `/api/conversations.list?types=&exclude_archived=&limit=999&cursor=` | https://docs.slack.dev/reference/methods/conversations.list |
| `slack_channel_info` | GET `/api/conversations.info?channel=&include_num_members=true` | https://docs.slack.dev/reference/methods/conversations.info |
| `slack_users` | GET `/api/users.list?limit=500&cursor=` | https://docs.slack.dev/reference/methods/users.list |
| `slack_user` | GET `/api/users.info?user=` | https://docs.slack.dev/reference/methods/users.info |
| `slack_bot` | GET `/api/bots.info?bot=` | https://docs.slack.dev/reference/methods/bots.info |
| `slack_auth` | POST `/api/auth.test` | https://docs.slack.dev/reference/methods/auth.test |

Roles bound: `chat_history` (slack_search_messages, then slack_search_context).

## Auth

`Authorization: Bearer <token>` on every call.
- `SLACK_BOT_TOKEN` (xoxb, required; sent by default). Read scopes: `channels:history`,
  `groups:history`, `im:history`, `mpim:history`, `channels:read`, `groups:read`, `im:read`,
  `mpim:read`, `users:read` (+ `users:read.email` for emails), `pins:read`. A bot reads only
  conversations it is a member of.
- `SLACK_USER_TOKEN` (xoxp, optional): `search.messages` accepts **only user tokens** (scope
  `search:read`); `assistant.search.context` accepts user tokens (`search:read.public`,
  `search:read.private`, `search:read.im`, `search:read.mpim`, `search:read.files`,
  `search:read.users`) or bot tokens **with an `action_token`** from a message event. The search
  tables send the user token (search_context falls back to the bot token).

## Base URL

`https://slack.com/api` (Enterprise org-wide tokens additionally need `team_id` on list/search methods).

UI links: channel tables link to `https://slack.com/app_redirect?channel=<channel id>` (documented
web deep link, https://docs.slack.dev/interactivity/deep-linking; opens the channel in the user's
default workspace, 404 when the user cannot access it). Search tables return each message's
`permalink` as `url`. There is no fixed template for a message link: `chat.getPermalink` makes one.

## Rate limits

https://docs.slack.dev/apis/web-api/rate-limits - per method, per workspace, per minute; HTTP 429 with
`Retry-After` (seconds).
- Tier 2 (20+/min): conversations.list, users.list, search.messages, pins.list.
- Tier 3 (50+/min): conversations.history, conversations.replies, conversations.info, bots.info.
- Tier 4 (100+/min): users.info. auth.test: hundreds per minute.
- assistant.search.context: special, ~10/min per workspace (more for large teams) and 10/min per
  user; paginated calls count.
- 2025-05-29 change (https://docs.slack.dev/changelog/2025/05/29/rate-limit-changes-for-non-marketplace-apps):
  commercially distributed **non-Marketplace** apps (new installs) get conversations.history and
  conversations.replies at **1 request/minute, max 15 messages**; internal customer-built apps keep
  Tier 3 and `limit` up to 1000 (clarified 2025-06-03).
- Cursor methods get stricter limits when called without pagination.
- Manifest: concurrency 3, 50 requests/minute source-wide.

## Retention

Workspace message retention settings apply; free plans only expose the last 90 days of history
(`is_limited`). Search covers what the token's user can access.

## Time formats

- Message `ts` / `thread_ts` / `latest_reply`: epoch **seconds** strings with microseconds
  (`"1512085950.000216"`), also the message id. `oldest` / `latest` must be epoch seconds; ISO
  strings are silently treated as 0 (whole history).
- `created` (channels, pins), `updated` (users, bots), `topic.last_set`: epoch seconds;
  conversations `updated`: epoch **milliseconds**.
- search.messages date modifiers: `after:` / `before:` take `YYYY-MM-DD` (day granularity, user
  time zone, boundary semantics undocumented): the manifest pushes after:=date(start-36h),
  before:=date(end+38h) and re-applies the exact window in SQL.
- assistant.search.context `after` / `before`: integer UNIX seconds.

## Pagination

- Cursor methods: `response_metadata.next_cursor` -> `cursor` (empty string = done);
  conversations.history also `has_more`. limit: history max 999 (recommended <= 200), replies
  default 1000, list must be < 1000.
- search.messages: `page` (1..100) x `count` (<= 100); `paging.pages` / `pagination`.
- assistant.search.context: `response_metadata.next_cursor` -> body `cursor`, 20 per page max.

## Pitfalls

- HTTP 200 with `{"ok": false, "error": "..."}` is the normal failure shape (channel_not_found,
  not_in_channel, missing_scope, invalid_auth, not_allowed_token_type, invalid_ts_oldest...): the
  manifest's source-wide error rule raises it.
- Channel parameters are ids (C/G/D...), not names; resolve with `slack_channels`.
- History has no thread replies: use `slack_thread_replies` with the parent's `message_ts`.
- Alert integrations put content in `attachments` / `blocks`; `text` may be a short fallback.
- search.messages is legacy (Slack recommends the Real-time Search API) and collapses nearby matches.
- assistant.search.context `sort=timestamp` switches to keyword retrieval (different result set).
- `reply_users` may contain bot ids.
