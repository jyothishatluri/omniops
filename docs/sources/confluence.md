# Confluence source (`confluence`)

Manifest: `src/omniops/sources/manifests/confluence.yaml` (5 tables)
Fixtures: `src/omniops/sources/fixtures/confluence/`
API reference roots:
- REST v1 (search): https://developer.atlassian.com/cloud/confluence/rest/v1/intro/
- REST v2 (pages, blog posts, spaces, comments): https://developer.atlassian.com/cloud/confluence/rest/v2/intro/
- CQL: https://developer.atlassian.com/cloud/confluence/advanced-searching-using-cql/ and https://developer.atlassian.com/cloud/confluence/cql-fields/

Checked against the official reference on 2026-10-27 using the OpenAPI documents behind the
reference pages: `https://developer.atlassian.com/cloud/confluence/swagger.v3.json` (v1) and
`https://dac-static.atlassian.com/cloud/confluence/openapi-v2.v3.json` (v2). Neither document
contains example responses for these operations (the pages render schema placeholders), so every
fixture is built strictly from the response schemas (`SearchPageResponseSearchResult`,
`PageSingle`, `MultiEntityResult<PageBulk>`, `MultiEntityResult<Space>`,
`MultiEntityResult<PageCommentModel>`), as each fixture note says.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `confluence_search` | GET `/wiki/rest/api/search?cql=&limit=&excerpt=highlight&expand=content.space,content.version` (follows `_links.next`) | https://developer.atlassian.com/cloud/confluence/rest/v1/api-group-search/#api-wiki-rest-api-search-get |
| `confluence_page` | GET `/wiki/api/v2/pages/{id}?body-format=storage&include-labels=true` (or `/wiki/api/v2/blogposts/{id}`) | https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/#api-pages-id-get , https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-blog-post/#api-blogposts-id-get |
| `confluence_pages` | GET `/wiki/api/v2/pages?space-id=&title=&status=&sort=-modified-date&limit=250` (space key resolved with GET `/wiki/api/v2/spaces?keys=`) | https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-page/#api-pages-get |
| `confluence_spaces` | GET `/wiki/api/v2/spaces?keys=&type=&status=&description-format=plain&sort=key&limit=250` | https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-space/#api-spaces-get |
| `confluence_page_comments` | GET `/wiki/api/v2/pages/{id}/footer-comments?body-format=storage&sort=-created-date&limit=250` (or `blogposts/{id}/footer-comments`) | https://developer.atlassian.com/cloud/confluence/rest/v2/api-group-comment/#api-pages-id-footer-comments-get |

Roles bound: `docs` (confluence_search).

## Auth

- Basic auth with Atlassian account email + API token (`CONFLUENCE_AUTH=basic`, default); the same
  token works for Jira and Confluence on one site.
- Bearer (`CONFLUENCE_AUTH=bearer`): OAuth 2.0 (3LO) access token; OAuth and scoped API tokens must
  use the gateway `https://api.atlassian.com/ex/confluence/<cloudId>` as `CONFLUENCE_URL`.
- Read-only needs: "Can use" Confluence + view permission on the spaces; restricted pages are
  invisible (404 / missing from search), not errors.

## Base URLs

`base_url` = `CONFLUENCE_URL` + `/wiki` (added when missing): `https://<site>.atlassian.net/wiki`, or
`https://api.atlassian.com/ex/confluence/<cloudId>/wiki`. v1 paths are `/rest/api/...`, v2 paths
`/api/v2/...` under it. Browser links: `_links.base` + `_links.webui` of each response.

## Rate limits

HTTP 429 with `Retry-After`, `X-RateLimit-*`, `RateLimit-Reason` (`conf-global-based`,
`conf-tenant-based`). Points-based hourly quotas (65,000 points/hour global pool) apply to
Forge/Connect/OAuth apps; API-token traffic only has burst limits.
https://developer.atlassian.com/cloud/confluence/rate-limiting/ . Manifest: concurrency 4, 300/min.

## Retention

Content is kept until deleted; trashed/archived content is excluded unless `status` asks for it
(v2) or `includeArchivedSpaces=true` (v1 search).

## Time formats

- Responses: ISO 8601 UTC (`createdAt`, `version.createdAt`, `lastModified`).
- CQL date literals: `"yyyy/MM/dd HH:mm"`, `"yyyy-MM-dd HH:mm"`, `"yyyy/MM/dd"`, `"yyyy-MM-dd"`, or
  `now("-4w")`, interpreted in the **user's configured time zone**. `modified_after` /
  `modified_before` are widened by 14 h when pushed into CQL; compare `ts` in SQL for exact bounds.

## Pagination

- v1 search: cursor embedded in `_links.next` (e.g. `/rest/api/search?next=true&cursor=...`),
  **relative to `_links.base`** (= `<site>/wiki`); followed with `next_url`. The `start` offset was
  deprecated and then removed from `/wiki/rest/api/search` on 10 Aug 2020
  (https://developer.atlassian.com/cloud/confluence/change-notice-moderize-search-rest-apis), so the
  next link is the only way to page; the engine sends it verbatim (it carries the cursor and the
  original cql/limit). `limit` is capped by
  undocumented system limits (25 when body expansions are requested).
- v2: `cursor` inside `_links.next` and the `Link` header, **relative to the site root**
  (`/wiki/api/v2/pages?cursor=...`). The engine resolves next links against `base_url`
  (`.../wiki`), which would double `/wiki`, so v2 tables read a single page with the documented
  maximum `limit=250`.

## Pitfalls

- Search exists only in v1 (`/wiki/rest/api/search`); `/wiki/rest/api/search` no longer accepts
  user fields (`user`, `user.fullname`, ...) - those live on `/wiki/rest/api/search/user`.
- v2 cannot read everything v1 search returns: blog posts need `/blogposts/{id}`
  (`content_type = 'blogpost'`), comments and attachments have their own endpoints, and v2 uses
  numeric space ids instead of keys (`POST /wiki/api/v2/content/convert-ids-to-types` maps
  ambiguous v1 content ids to v2 types).
- An unknown space key or label in CQL yields 0 results, not an error.
- `excerpt=highlight` wraps matches in `@@@hl@@@ ... @@@endhl@@@`; the manifest strips them.
- Storage-format bodies are XHTML with `ac:` macros; the plain `text` column strips tags but keeps
  CDATA (code block) contents.
- Search indexing lags edits briefly.

## Engine gaps

- `next_url` pagination resolves a relative next link against `base_url` with its leading `/`
  stripped. v1 search links are relative to `<site>/wiki` (works, base_url ends in `/wiki`), but v2
  `_links.next` / `Link` are relative to the site (or gateway) root and start with `/wiki/...`, so
  they would resolve to `/wiki/wiki/...`. A per-table next-link base (e.g. `next_url_base:
  "{{ source.base_url[:-5] }}"`) would let v2 list tables page; until then they read one page of the
  documented maximum (`limit=250`).
- No ADF (Atlassian Document Format) to text transform: `body_format = 'atlas_doc_format'` returns
  the raw ADF JSON string in `text`; the default `storage` body is converted to plain text.
