import unittest
from datetime import datetime, timezone
from pathlib import Path

from ashare_lab.sources.external_snapshot import (
    ExternalSnapshotMetadata,
    validate_rule_fields,
)


class ExternalSnapshotTests(unittest.TestCase):
    def test_requires_timezone_and_absolute_directory(self):
        common = dict(
            source_name="demo",
            connector_version="1",
            raw_directory=Path("C:/imports/demo"),
            manifest_sha256="a" * 64,
        )
        ExternalSnapshotMetadata(
            queried_at=datetime(2026, 9, 24, tzinfo=timezone.utc), **common
        )
        with self.assertRaises(ValueError):
            ExternalSnapshotMetadata(
                queried_at=datetime(2026, 9, 24), **common
            )

    def test_missing_rule_fields_are_not_inferred(self):
        missing = validate_rule_fields({"trade_date", "code", "tradestatus"})
        self.assertEqual(
            missing,
            (
                "buyable",
                "lower_limit",
                "sellable",
                "source",
                "upper_limit",
            ),
        )


if __name__ == "__main__":
    unittest.main()
