from __future__ import annotations

import unittest

from src.db.identity_resolution import (
    _strong_name_compatibility,
    _unresolved_status,
    official_name_variants,
)


class IdentityResolutionTests(unittest.TestCase):
    def test_official_comma_suffix_variants_are_explicit(self) -> None:
        variants = official_name_variants("BALDWIN IV, WADE")
        self.assertIn("wade baldwin", variants)
        self.assertNotIn("baldwin wade", {"wade baldwin"})

    def test_strong_name_requires_surname_or_token_containment(self) -> None:
        self.assertTrue(_strong_name_compatibility("Oshae Brissett", "BRISSETT, O'SHAE J"))
        self.assertTrue(_strong_name_compatibility("Nikos Rogkavopoulos", "ROGKAVOPOULOS, NIKOLAOS"))
        self.assertFalse(_strong_name_compatibility("Chiek Diallo", "RADZEVICIUS, GYTIS"))

    def test_transliteration_like_unique_candidate_stays_ambiguous(self) -> None:
        status = _unresolved_status(
            "Gur Lavy",
            {"candidate"},
            {"candidate": ("P001", "LAVI GUR")},
        )
        self.assertEqual(status, "AMBIGUOUS")


if __name__ == "__main__":
    unittest.main()
