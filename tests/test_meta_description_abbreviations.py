"""Keep abbreviation periods out of generated metadata sentence boundaries."""

import html
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from build_static_skill_pages import (  # noqa: E402
    _meta_description,
    build_static_skill_pages,
    skill_page_slug,
)


VIDEO_DESCRIPTION = (
    "Convert an MP4 (e.g. a HyperFrames render or a camera export) into an optimized "
    "animated GIF with palette generation, frame-rate control, and optional resizing for sharing."
)
VIDEO_SNIPPET = (
    "Convert an MP4 (e.g. a HyperFrames render or a camera export) into an optimized "
    "animated GIF with palette generation… Install video-to-gif for Claude Code."
)
MORE_DETAIL = " Additional workflow details and examples for testing and deployment." * 4


class MetaDescriptionAbbreviationTests(unittest.TestCase):
    def test_long_first_sentence_uses_ellipsis_instead_of_abbreviation_boundary(self):
        description = _meta_description({"name": "video-to-gif", "description": VIDEO_DESCRIPTION})

        self.assertEqual(description, VIDEO_SNIPPET)
        self.assertLessEqual(len(description), 160)

    def test_abbreviations_do_not_end_a_complete_first_sentence(self):
        for abbreviation in ("e.g.", "i.e.", "E.G.", "U.S.", "U.K.", "Dr.", "vs.", "etc."):
            with self.subTest(abbreviation=abbreviation):
                sentence = f"Use {abbreviation} examples to test the workflow."
                description = _meta_description({"name": "guide", "description": sentence + MORE_DETAIL})

                self.assertEqual(description, sentence + " Install guide for Claude Code.")
                self.assertLessEqual(len(description), 160)

    def test_multiple_abbreviations_are_skipped_before_real_boundary(self):
        sentence = "Compare U.S. and U.K. workflows, e.g. PDF exports."

        self.assertEqual(
            _meta_description({"name": "guide", "description": sentence + MORE_DETAIL}),
            sentence + " Install guide for Claude Code.",
        )

    def test_nonterminal_abbreviations_can_introduce_capitalized_words(self):
        for sentence in ("Export examples, e.g. PDF files.", "Use i.e. JSON data.", "Ask Dr. Smith for examples."):
            with self.subTest(sentence=sentence):
                self.assertEqual(
                    _meta_description({"name": "guide", "description": sentence + MORE_DETAIL}),
                    sentence + " Install guide for Claude Code.",
                )

    def test_contextual_abbreviations_can_end_a_sentence(self):
        for sentence in ("Export PDFs, CSVs, etc.", "Create charts for the U.S.", "Set a timeout of 500 ms."):
            for continuation in (MORE_DETAIL, " Additional details " + "for the workflow " * 20):
                with self.subTest(sentence=sentence, continuation=continuation):
                    self.assertEqual(
                        _meta_description({"name": "guide", "description": sentence + continuation}),
                        sentence + " Install guide for Claude Code.",
                    )

    def test_ordinary_sentence_endings_still_stop_the_snippet(self):
        for sentence in ("Use the CLI.", "Need a PDF?", "Export now!", "Use v1.2 and example.com tools."):
            with self.subTest(sentence=sentence):
                self.assertEqual(
                    _meta_description({"name": "guide", "description": sentence + MORE_DETAIL}),
                    sentence + " Install guide for Claude Code.",
                )

    def test_short_description_and_missing_description_are_unchanged(self):
        self.assertEqual(
            _meta_description({"name": "guide", "description": "Use examples, e.g. PDF files."}),
            "Use examples, e.g. PDF files. Install guide for Claude Code.",
        )
        self.assertEqual(_meta_description({"name": "guide"}), "Install guide for Claude Code.")

    def test_long_text_without_a_sentence_boundary_keeps_bounded_ellipsis(self):
        for name, tail in (
            ("guide", " Install guide for Claude Code."),
            ("long-name-" * 15, " Install this Claude Code skill."),
        ):
            with self.subTest(name=name):
                description = _meta_description({"name": name, "description": "workflow " * 40})
                self.assertTrue(description.endswith("…" + tail))
                self.assertLessEqual(len(description), 160)

    def test_rendered_meta_open_graph_and_twitter_share_corrected_snippet(self):
        record = {
            "id": "video-abbreviation-regression",
            "name": "video-to-gif",
            "description": VIDEO_DESCRIPTION,
            "repo": "acme/skills",
            "path": "skills/video-to-gif",
            "branch": "main",
            "category": "design",
            "install": "acme/skills/skills/video-to-gif",
            "quality_grade": "A",
            "quality_score": 75,
            "security_status": "passed",
            "install_status": "known_good",
            "security_scan": {
                "scanner_version": "1.1.6",
                "ruleset_sha256": "a" * 64,
                "scanned_at": "2026-10-22T00:00:00Z",
                "content_sha256": "b" * 64,
                "checks": ["frontmatter", "dangerous_patterns", "credentials"],
                "finding_types": {},
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            build_static_skill_pages([record], output)
            page = (output / "skills" / skill_page_slug(record) / "index.html").read_text()

        for attribute in ('name="description"', 'property="og:description"', 'name="twitter:description"'):
            with self.subTest(attribute=attribute):
                match = re.search(rf'<meta {attribute} content="([^"]*)"', page)
                self.assertIsNotNone(match)
                self.assertEqual(html.unescape(match.group(1)), VIDEO_SNIPPET)


if __name__ == "__main__":
    unittest.main()
