# Author removal and attribution requests

Authors and authorized representatives can [open a removal request](https://github.com/majiayu000/claude-skill-registry-core/issues/new?template=removal-request.yml) to remove archived content, remove listings, or correct attribution. Link the original work and affected registry URLs and supply public ownership evidence. Do not publish sensitive personal information or credentials. For a private report, use the repository's GitHub private reporting channel where available.

The registry's own MIT license does not license third-party content. Missing license metadata means permission is unknown. Site guides show metadata and link to the source; they do not reproduce SKILL.md bodies.

Maintainer procedure:

1. Verify the requester and requested scope. Identify all archive entries and copied content, including copies in other repositories; do not assume the highest-starred copy is the original author.
2. Remove the affected entries from the data repository and all relevant source lists in core, or correct attribution as requested. For source-wide removals, record the repository in `sources/security_blocklist.json` with a reason explicitly stating that this is an author-requested exclusion, not a security finding. The existing reject list prevents automatic re-import. For a single skill, inspect discovery paths before closing the request; do not claim re-import is prevented by deleting a single source row.
3. Rebuild and publish from pinned core/data commits. Check archive URLs, generated guides, search shards, detail shards, and sitemap for every affected identity. Ask copied upstream repositories separately if they also need to remove the work; registry maintainers control only registry copies.
4. Link the published result in the request. Git deletion removes current published content; historical Git commits may retain it. If the request includes historical removal, handle that separately with the requester and hosting provider. Never claim history was erased by a normal commit.

No automatic takedown occurs solely because a public issue is filed. Verification and scope decisions are handled by maintainers.
