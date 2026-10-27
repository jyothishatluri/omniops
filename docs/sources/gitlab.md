# GitLab source (`gitlab`)

Manifest: `src/omniops/sources/manifests/gitlab.yaml` (22 tables)
Fixtures: `src/omniops/sources/fixtures/gitlab/`
API reference root: https://docs.gitlab.com/api/rest/ (REST API v4).

Checked against the official reference on 2026-10-27. The reference pages are generated from
`gitlab-org/gitlab` `doc/api/*.md` on the default branch, which is what was read (raw Markdown).
Fixtures are the documented "Example response" blocks; where the doc example is not valid JSON the
fixture note says what was changed (`//` comments stripped from projects/MR examples, one trailing
comma removed from the pipeline-jobs example, invalid `\ ` escapes in the MR-diffs example
replaced by spaces). Two text fixtures (raw file, job trace) are constructed because the docs show
no body for those plain-text endpoints.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `gitlab_projects` | GET `/groups/:id/projects?include_subgroups=true` (or `/projects?membership=true`) | https://docs.gitlab.com/api/groups/#list-projects , https://docs.gitlab.com/api/projects/#list-all-projects |
| `gitlab_commits` | GET `/projects/:id/repository/commits?since&until&with_stats=true` | https://docs.gitlab.com/api/commits/#list-repository-commits |
| `gitlab_commit` | GET `/projects/:id/repository/commits/:sha?stats=true` | https://docs.gitlab.com/api/commits/#retrieve-a-commit |
| `gitlab_commit_diff` | GET `/projects/:id/repository/commits/:sha/diff` | https://docs.gitlab.com/api/commits/#retrieve-commit-diff |
| `gitlab_commit_merge_requests` | GET `/projects/:id/repository/commits/:sha/merge_requests` | https://docs.gitlab.com/api/commits/#list-merge-requests-associated-with-a-commit |
| `gitlab_compare_commits` | GET `/projects/:id/repository/compare?from&to` (`commits[]`) | https://docs.gitlab.com/api/repositories/#compare-branches-tags-or-commits |
| `gitlab_compare_diffs` | GET `/projects/:id/repository/compare?from&to` (`diffs[]`; fails on `compare_timeout`) | https://docs.gitlab.com/api/repositories/#compare-branches-tags-or-commits |
| `gitlab_merge_requests` | GET `/projects/:id/merge_requests?updated_after&updated_before&order_by=updated_at` | https://docs.gitlab.com/api/merge_requests/#list-project-merge-requests |
| `gitlab_merged_merge_requests` | GET `/groups/:id/merge_requests` or `/projects/:id/merge_requests` `?state=merged&merged_after&merged_before` | https://docs.gitlab.com/api/merge_requests/#list-group-merge-requests |
| `gitlab_merge_request_diffs` | GET `/projects/:id/merge_requests/:merge_request_iid/diffs` | https://docs.gitlab.com/api/merge_requests/#list-merge-request-diffs |
| `gitlab_deployments` | GET `/projects/:id/deployments?updated_after&updated_before&order_by=updated_at&sort=desc` | https://docs.gitlab.com/api/deployments/#list-all-project-deployments |
| `gitlab_environments` | GET `/projects/:id/environments` | https://docs.gitlab.com/api/environments/#list-all-environments |
| `gitlab_pipelines` | GET `/projects/:id/pipelines?created_after&created_before` | https://docs.gitlab.com/api/pipelines/#list-project-pipelines |
| `gitlab_pipeline_jobs` | GET `/projects/:id/pipelines/:pipeline_id/jobs?scope[]=` | https://docs.gitlab.com/api/jobs/#list-all-jobs-by-pipeline |
| `gitlab_jobs` | GET `/projects/:id/jobs?scope[]=` | https://docs.gitlab.com/api/jobs/#list-all-jobs-for-a-project |
| `gitlab_job_log` | GET `/projects/:id/jobs/:job_id/trace` (plain text, one row per line) | https://docs.gitlab.com/api/jobs/#retrieve-a-log-file-for-a-job |
| `gitlab_releases` | GET `/projects/:id/releases?order_by=released_at&sort=desc` | https://docs.gitlab.com/api/releases/#list-releases |
| `gitlab_tags` | GET `/projects/:id/repository/tags?order_by=updated&sort=desc` | https://docs.gitlab.com/api/tags/#list-all-project-repository-tags |
| `gitlab_repository_file` | GET `/projects/:id/repository/files/:file_path?ref=` | https://docs.gitlab.com/api/repository_files/#retrieve-a-file-from-a-repository |
| `gitlab_file_lines` | GET `/projects/:id/repository/files/:file_path/raw?ref=` (text, one row per line) | https://docs.gitlab.com/api/repository_files/#retrieve-a-raw-file-from-a-repository |
| `gitlab_file_blame` | GET `/projects/:id/repository/files/:file_path/blame?ref=&range[start]&range[end]` | https://docs.gitlab.com/api/repository_files/#retrieve-file-blame-history-from-a-repository |
| `gitlab_search_blobs` | GET `/projects/:id/search?scope=blobs` or `/groups/:id/search?scope=blobs` | https://docs.gitlab.com/api/search/#search-a-project , https://docs.gitlab.com/api/search/#search-a-group |

Roles bound: `code_changes` (gitlab_merged_merge_requests, gitlab_commits), `change_events`
(gitlab_deployments, gitlab_releases).

## Auth

- `PRIVATE-TOKEN: <GITLAB_TOKEN>` header (the documented, recommended form; `Authorization: Bearer`
  also works for personal/OAuth tokens). https://docs.gitlab.com/api/rest/authentication/
- Token: personal, project or group access token with the `read_api` scope. Missing/invalid token =
  401 `{"message": "401 Unauthorized"}`; valid token without the scope = 403 `insufficient_scope`.
  Project access tokens on GitLab.com need Premium/Ultimate.
- Private projects the token cannot see answer 404 (`404 Project Not Found`).

## Base URLs

- GitLab.com: `https://gitlab.com/api/v4` (default `GITLAB_URL=https://gitlab.com`).
- Self-managed / Dedicated: `https://<host>/api/v4` (set `GITLAB_URL=https://<host>`).
- `GITLAB_GROUP` (full group path) prefixes bare project names and scopes group-wide tables.
- Project IDs: numeric ID or URL-encoded full path (`group%2Fsub%2Fproject`); file paths and refs
  with `/` are URL-encoded too (`%2F`). The manifest does this encoding.
  https://docs.gitlab.com/api/rest/#namespaced-paths

## Rate limits

- GitLab.com (tier-aware, https://docs.gitlab.com/user/gitlab_com/rate_limits/): authenticated
  API burst 100 requests/minute per user on Free (1,250 Premium, 2,000 Ultimate); sustained
  5,000/hour Free (15,000 Premium, 25,000 Ultimate); unauthenticated 60/hour per IP. Raw endpoint
  traffic 300/minute per project/commit/path. 429 responses carry `Retry-After` (seconds) and
  `RateLimit-*` headers (not on Projects/Groups/Users APIs).
- File blame: 400/minute per user and project. Files > 10 MB: 5 requests/minute.
- Self-managed: whatever the admin configured (User and IP rate limits).
- The manifest caps itself at 90 requests/minute (safe for Free-tier GitLab.com users).

## Pagination

- Offset pagination: `page` (1-based) and `per_page` (default 20, max 100) on every list; GitLab
  also returns `Link` headers and `x-next-page`/`x-total` (no `x-total` for commits on GitLab.com,
  or for > 10,000 records). https://docs.gitlab.com/api/rest/#pagination
- **The manifest uses `page`/`per_page`, not `link_header`**: the engine's `link_header` pagination
  re-sends the next URL with `params={}`, which (httpx 0.28) strips the URL's query string, so page
  2+ would re-read page 1 without filters (see engine gaps). Project jobs therefore use offset
  pagination too (keyset pagination only works by following the Link header).
- Search with advanced search: at most 10,000 results reachable.

## Time formats

- All datetime parameters: ISO 8601 `YYYY-MM-DDTHH:MM:SSZ` (`since`/`until` on commits;
  `updated_after/before`, `created_after/before`, `merged_after/before`). A `+hh:mm` offset would
  need `%2B` encoding; the manifest always sends UTC `Z`.
- Responses: object times are UTC (`2016-08-11T07:36:40.222Z`); git dates (`committed_date`,
  `authored_date`, compare/tag `created_at`) carry the committer's offset (`+02:00`). Both become
  correct UTC instants in SQL.

| Table | Server-side time filter |
|---|---|
| gitlab_commits | `since` / `until` |
| gitlab_merge_requests | `updated_after` / `updated_before` |
| gitlab_merged_merge_requests | `merged_after` / `merged_before` (+ `state=merged`) |
| gitlab_deployments | `updated_after` / `updated_before` (requires `order_by=updated_at`, always sent) |
| gitlab_pipelines | `created_after` / `created_before` |
| gitlab_jobs, gitlab_releases | none (newest pages, ts filtered in SQL) |

## Retention

- Git data, MRs, deployments and releases are kept indefinitely.
- GitLab.com archives pipelines after 1 year (still readable, not retryable). Job artifacts expire
  after 30 days by default on GitLab.com (`artifacts:expire_in`); an erased job has no log (404).
- Self-managed retention is admin-configured.

## Pitfalls

- `project` is the numeric ID or the full path (the URL path, not the display name); bare names use
  `GITLAB_GROUP`. Role bindings assume service == project name under `GITLAB_GROUP`.
- 404 = not found **or** no access.
- Deployments API rejects some filter/sort combinations with 400 (`updated_*` needs
  `order_by=updated_at`; `finished_*` needs `order_by=finished_at` and/or `status=success`; the two
  families cannot be combined). The table always uses the valid `updated_*` + `order_by=updated_at`.
- Deployments only exist for jobs with an `environment:` keyword.
- `compare_timeout: true` (2xx) means the compare diff is incomplete (commits still complete): the
  only 2xx-with-partial-data case, raised loudly by `gitlab_compare_diffs` (fixture
  `compare_timeout.json` proves the rule fires).
- Diffs are subject to the instance diff limits (`too_large`, `collapsed`, truncated file lists).
- Child pipelines are excluded from the pipelines list unless `source = 'parent_pipeline'`.
- Job traces contain ANSI escape codes and `section_start:`/`section_end:` markers.
- Group-level blob search generally needs advanced search (Premium, Elasticsearch) or exact code
  search; basic search supports blobs reliably only per project.
- Blame rows have no line numbers (the API groups lines per commit range).
- Every other error is a non-2xx JSON body (`{"message": ...}` or `{"error": ...}`).
