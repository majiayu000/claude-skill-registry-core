# Registry distribution redesign

Core remains authoritative. Keep archived skill contents in data and publish the merged artifact to main. Preserve existing uncommitted work in other checkouts. The current core checkout is clean; implementation uses a separate worktree from current main.

Implement the requested minimal version in the existing pipeline:

- Exclude this registry's own repositories at collection and public-index boundaries.
- Read multiline descriptions using existing YAML parsing; repair old scalar block indicators from archived content during indexing, without rewriting archived bodies.
- Group guides by skill name and a hash of the exact Markdown body after line-ending normalization. Equal descriptions alone do not establish identity. Report repository-copy counts as copies, not independent votes or original authorship. Existing source-location indexes continue to count source locations explicitly.
- Build up to 5,800 guides with security evidence, a usable source path, quality score at least 70, and a description of at least 30 characters. Do not extend to the second wave until Search Console evidence exists.
- Keep the GitHub Pages search front end and adapt the Vercel detail layout to static guides. Include trust/quality evidence, source links, installation, known license/author metadata, matching copies, and related guides. Do not publish instruction bodies on guide pages.
- Provide a crawlable catalog, sitemap, search-result links, and archive category README entrypoints.
- Correct badge URLs, explicitly deploy after successful artifact publish, and route the redundant Vercel front end to the canonical site. Keep the existing Pages domain until the owner provides a domain.
- Add author-request intake and a maintainer removal procedure. Current archive contents remain intact.

Verify focused regression tests, full CI and coverage gates, a representative full archive build, pinned-reference publish, live Pages statistics and several generated pages. Search Console verification requires a supplied token or action by the account owner. Search-engine indexing and 2–4 weeks of measurement cannot be completed in one deployment.

## Search Console handoff

Add a URL-prefix property for `https://majiayu000.github.io/claude-skill-registry/` in [Google Search Console](https://search.google.com/search-console). Choose HTML file verification, provide the requested file content to the maintainer, and keep the file in `docs/` through future builds. After deployment, click Verify and submit `sitemap.xml`. Review Pages indexing, clicks, impressions, and query/page pairs after 2–4 weeks. This is an owner action; a working sitemap does not prove verified ownership.
