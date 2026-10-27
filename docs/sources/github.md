# GitHub source (`github`)

Manifest: `src/omniops/sources/manifests/github.yaml` (23 tables)
Fixtures: `src/omniops/sources/fixtures/github/`
API reference root: https://docs.github.com/en/rest?apiVersion=2022-11-28 (REST API version `2022-11-28`).

Checked against the official reference on 2026-10-27. Paths, parameters, enums and example
responses come from the reference pages below and from GitHub's own OpenAPI description
(`github/rest-api-description`, `descriptions/api.github.com/api.github.com.json`), whose
`components/examples` are exactly the "Example response" blocks rendered on docs.github.com. Every
JSON fixture is one of those examples, unmodified.

## Tables -> endpoints -> reference pages

| SQL table | HTTP | Official reference |
|---|---|---|
| `github_repos` | GET `/orgs/{org}/repos` (or `/users/{user}/repos`, `/user/repos`) | https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#list-organization-repositories |
| `github_commits` | GET `/repos/{owner}/{repo}/commits` | https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#list-commits |
| `github_commit_files` | GET `/repos/{owner}/{repo}/commits/{ref}` (one row per `files[]`) | https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#get-a-commit |
| `github_commit_pulls` | GET `/repos/{owner}/{repo}/commits/{commit_sha}/pulls` | https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#list-pull-requests-associated-with-a-commit |
| `github_compare_commits` | GET `/repos/{owner}/{repo}/compare/{base}...{head}` (`commits[]`) | https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#compare-two-commits |
| `github_compare_files` | GET `/repos/{owner}/{repo}/compare/{base}...{head}` (`files[]`) | https://docs.github.com/en/rest/commits/commits?apiVersion=2022-11-28#compare-two-commits |
| `github_pulls` | GET `/repos/{owner}/{repo}/pulls?state=all&sort=updated&direction=desc` | https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28#list-pull-requests |
| `github_merged_pulls` | GET `/search/issues?q=is:pr is:merged merged:START..END org:/repo:` | https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-issues-and-pull-requests |
| `github_pull_files` | GET `/repos/{owner}/{repo}/pulls/{pull_number}/files` | https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28#list-pull-requests-files |
| `github_deployments` | GET `/repos/{owner}/{repo}/deployments` | https://docs.github.com/en/rest/deployments/deployments?apiVersion=2022-11-28#list-deployments |
| `github_deployment_statuses` | GET `/repos/{owner}/{repo}/deployments/{deployment_id}/statuses` | https://docs.github.com/en/rest/deployments/statuses?apiVersion=2022-11-28#list-deployment-statuses |
| `github_releases` | GET `/repos/{owner}/{repo}/releases` | https://docs.github.com/en/rest/releases/releases?apiVersion=2022-11-28#list-releases |
| `github_tags` | GET `/repos/{owner}/{repo}/tags` | https://docs.github.com/en/rest/repos/repos?apiVersion=2022-11-28#list-repository-tags |
| `github_workflow_runs` | GET `/repos/{owner}/{repo}/actions/runs?created=START..END` | https://docs.github.com/en/rest/actions/workflow-runs?apiVersion=2022-11-28#list-workflow-runs-for-a-repository |
| `github_workflow_jobs` | GET `/repos/{owner}/{repo}/actions/runs/{run_id}/jobs` | https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2022-11-28#list-jobs-for-a-workflow-run |
| `github_job_logs` | GET `/repos/{owner}/{repo}/actions/jobs/{job_id}/logs` (302 -> signed plain-text URL) | https://docs.github.com/en/rest/actions/workflow-jobs?apiVersion=2022-11-28#download-job-logs-for-a-workflow-run |
| `github_check_runs` | GET `/repos/{owner}/{repo}/commits/{ref}/check-runs` | https://docs.github.com/en/rest/checks/runs?apiVersion=2022-11-28#list-check-runs-for-a-git-reference |
| `github_commit_statuses` | GET `/repos/{owner}/{repo}/commits/{ref}/status` (`statuses[]`) | https://docs.github.com/en/rest/commits/statuses?apiVersion=2022-11-28#get-the-combined-status-for-a-specific-reference |
| `github_search_code` | GET `/search/code` (Accept `application/vnd.github.text-match+json`) | https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-code |
| `github_search_issues` | GET `/search/issues` | https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-issues-and-pull-requests |
| `github_search_commits` | GET `/search/commits?q=... committer-date:START..END` | https://docs.github.com/en/rest/search/search?apiVersion=2022-11-28#search-commits |
| `github_contents` | GET `/repos/{owner}/{repo}/contents/{path}?ref=` (JSON) | https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#get-repository-content |
| `github_file_lines` | GET `/repos/{owner}/{repo}/contents/{path}?ref=` with Accept `application/vnd.github.raw+json` (text, one row per line) | https://docs.github.com/en/rest/repos/contents?apiVersion=2022-11-28#get-repository-content |

Roles bound: `code_changes` (github_merged_pulls, github_commits), `change_events`
(github_deployments, github_releases), `tickets` (github_search_issues).

## Auth

- `Authorization: Bearer <GITHUB_TOKEN>`; headers on every request:
  `Accept: application/vnd.github+json`, `X-GitHub-Api-Version: 2022-11-28`.
- Token: a fine-grained PAT with read-only Contents, Metadata, Pull requests, Actions, Checks,
  Commit statuses, Deployments and Issues on the repositories in scope; or a classic PAT (`repo`,
  `read:org`); or a GitHub App installation token (higher rate limits). Private repositories the
  token cannot see answer **404**, not 403.
- Code search requires authentication.

## Base URLs

- github.com: `https://api.github.com` (default `GITHUB_API_URL`).
- GitHub Enterprise Server: `https://<host>/api/v3` (set `GITHUB_API_URL`, and `GITHUB_UI_URL=https://<host>`
  for deep links). The `X-GitHub-Api-Version` header is honoured by GHES 3.9+.
- GitHub Enterprise Cloud with data residency: `https://api.<subdomain>.ghe.com`.
- `GITHUB_OWNER` (org or user login) prefixes bare repo names and scopes org-wide searches.

## Rate limits

- Primary: 5,000 requests/hour per user token; 15,000/hour for GitHub Apps and OAuth apps owned by
  a GitHub Enterprise Cloud org; `GITHUB_TOKEN` in Actions 1,000/hour/repo; unauthenticated 60/hour.
  Headers `x-ratelimit-limit/-remaining/-used/-reset` (reset = UTC epoch seconds), `x-ratelimit-resource`.
- Secondary: at most 100 concurrent requests; at most 900 points/minute per REST endpoint (GET = 1);
  content creation limits do not apply (read-only).
- Search: 30 requests/minute (authenticated) for all search endpoints except code search, which is
  10 requests/minute. Every search returns at most 1,000 results.
- Exceeding a limit: 403 or 429, body "API rate limit exceeded" / "You have exceeded a secondary
  rate limit"; honour `retry-after`, otherwise wait until `x-ratelimit-reset`. The manifest sets
  `retry_body_patterns` so these surface as rate-limit errors (not auth errors), and caps itself at
  120 requests/minute.
- Source: https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api

## Pagination

- Lists take `per_page` (max 100; larger values are silently reduced) and `page` (1-based). GitHub
  also returns `Link: <...>; rel="next"` headers
  (https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api).
- **The manifest uses `page`/`per_page`, not `link_header`**: the engine's `link_header` pagination
  re-sends the next URL with `params={}`, and httpx 0.28 then strips the URL's query string
  (`page=2`, filters, time window), so page 2+ would silently re-read page 1 unfiltered (see engine gaps).
- Get-a-commit paginates its `files` array (> 300 files, up to 3,000); compare lists at most 250
  commits without paging parameters and at most 300 files for the whole comparison (files only on
  the first page) - the compare tables do not paginate.
- Search: 100 per page, 1,000 results max; `incomplete_results: true` means the query timed out
  (exposed as a column; results may be partial).

## Time formats

- All timestamp parameters are ISO 8601 `YYYY-MM-DDTHH:MM:SSZ` (`since`/`until` on commits).
- Search qualifiers accept `YYYY-MM-DD`, `YYYY-MM-DDTHH:MM:SSZ`, ranges `A..B`, `>=`, `<`
  (https://docs.github.com/en/search-github/getting-started-with-searching-on-github/understanding-the-search-syntax#query-for-dates).
  Used for `merged:` (github_merged_pulls), `committer-date:` (github_search_commits) and Actions'
  `created=` parameter (github_workflow_runs).
- Response timestamps are UTC `...Z`.

| Table | Server-side time filter |
|---|---|
| github_commits | `since` / `until` (committer date) |
| github_merged_pulls | `merged:START..END` search qualifier |
| github_search_commits | `committer-date:START..END` qualifier (skipped if the query has its own date qualifier) |
| github_workflow_runs | `created=START..END` |
| github_pulls, github_deployments, github_releases | none (newest pages fetched, ts filtered in SQL) |

## Retention

- Git data (commits, tags, releases, PRs) is kept forever.
- Actions logs and artifacts: 90 days by default (configurable 1-90 days for public repos, 1-400 days
  for private repos); an expired job log returns 404/410 - not proof the job never ran.
- Since 2026-10-01 the same retention period also deletes **checks, workflow runs and commit
  statuses** (before that they were kept 400+ days): `github_workflow_runs`, `github_check_runs` and
  `github_commit_statuses` return nothing older than the repository's retention (90 days by default).
  Source: https://docs.github.com/en/organizations/managing-organization-settings/configuring-the-retention-period-for-github-actions-artifacts-and-logs-in-your-organization
- Deployment statuses are kept; transient environments may be deactivated (`inactive`).

## Pitfalls

- `repo` must be `owner/name`; bare names use `GITHUB_OWNER`. Service == repo name is an
  assumption of the role bindings; verify with `github_repos`.
- 404 = not found **or** no access (private repos).
- `sha` columns are always 40-char SHAs: compare short SHAs with `LIKE 'abc1234%'`; `ref` filters
  (URL path) accept short SHAs, branches and tags.
- There is no `merged` PR state: merged PRs are `closed` with `merged_at`; use `merged = true` or
  `github_merged_pulls`.
- Code search: must be scoped (`repo:`/`org:`/`user:` - the table appends `org:GITHUB_OWNER`
  when missing), only default branches, files < 384 KB, needs at least one non-qualifier term, no
  regex, 10 requests/minute, results only from repos the token can read (others silently omitted).
- Issue search with a GitHub App user token must contain `is:issue` or `is:pull-request` (422).
- Deployments exist only when tooling uses the Deployments API (Actions `environment:`, Argo CD
  notifications, etc.); otherwise look at `github_workflow_runs`.
- `status` and `conclusion` of workflow runs share one API parameter (`status`).
- Job logs are plain text with an RFC 3339 timestamp prefix on each line; `##[error]` marks errors.
- GitHub never returns 2xx-with-error bodies for these endpoints: errors are 4xx with a JSON
  `message` (422 "Validation Failed" for bad search queries or unknown SHAs, 409 for empty repos -
  mapped to 0 rows on github_commits).
