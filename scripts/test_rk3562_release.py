#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0+
"""Check dated release names and tag/manual version consistency."""

import unittest

from rk3562_release import release_identity


class ReleaseTests(unittest.TestCase):
    def test_manual_release_has_date_and_version(self):
        self.assertEqual(release_identity("v1.2.3", "2026-10-19"),
                         dict(release_version="1.2.3", release_date="2026-10-19",
                              release_tag="rk3562-v1.2.3-2026-10-19"))

    def test_dated_tag_uses_its_own_version_and_date(self):
        self.assertEqual(release_identity(ref="rk3562-v2.0.0-2026-09-01", ref_type="tag"),
                         dict(release_version="2.0.0", release_date="2026-09-01",
                              release_tag="rk3562-v2.0.0-2026-09-01"))
        self.assertEqual(release_identity(release_date="2026-10-19", ref="rk3562-v1.0.0", ref_type="tag")["release_tag"],
                         "rk3562-v1.0.0")

    def test_invalid_versions_dates_and_conflicting_tags_are_rejected(self):
        for version, day in (("1.0", "2026-10-19"), ("../escape", "2026-10-19"),
                             ("01.0.0", "2026-10-19"), ("1.0.0", "2026-02-30"),
                             ("1.0.0", "20261019")):
            with self.assertRaises(ValueError):
                release_identity(version, day)
        with self.assertRaisesRegex(ValueError, "version differs"):
            release_identity("1.0.1", ref="rk3562-v1.0.0-2026-10-19", ref_type="tag")
        with self.assertRaisesRegex(ValueError, "date differs"):
            release_identity(release_date="2026-10-20", ref="rk3562-v1.0.0-2026-10-19", ref_type="tag")


if __name__ == "__main__":
    unittest.main()
