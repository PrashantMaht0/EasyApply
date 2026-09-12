# Third Party Services and Attributions

EaseApply reads public, unauthenticated, read only endpoints. It never handles credentials for
any job platform and never scrapes authenticated pages.

All outbound calls are limited to 8 concurrent connections with an 8 second timeout, sent with
an identifying `User-Agent`, and cached in SQLite so a resolved board is not re-probed.

## ATS job boards

These endpoints exist so a company careers page can render. Reading them is their intended use,
as each platform's public API documentation describes.

| Platform | Endpoint | API documentation | Terms |
|---|---|---|---|
| Greenhouse | `boards-api.greenhouse.io/v1/boards/{slug}/jobs` | https://developers.greenhouse.io/job-board.html | https://www.greenhouse.com/legal |
| Lever | `api.lever.co/v0/postings/{slug}` | https://github.com/lever/postings-api | https://www.lever.co/legal/terms-of-service/ |
| Ashby | `api.ashbyhq.com/posting-api/job-board/{slug}` | https://developers.ashbyhq.com/docs/public-job-posting-api | https://www.ashbyhq.com/terms |
| Personio | `{slug}.jobs.personio.de/search.json` | https://developer.personio.de/docs/retrieving-open-job-positions | See the API documentation |

## Syndication cross check

These are queried to determine whether a posting has already reached the open market. They are
not used to source jobs.

**RemoteOK.** Data from [RemoteOK](https://remoteok.com). RemoteOK requires a followed link back
as attribution, which appears in the application footer and in every digest email.

**Arbeitnow.** Public job board API, https://www.arbeitnow.com/api/job-board-api.
