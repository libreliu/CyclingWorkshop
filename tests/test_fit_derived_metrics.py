"""距离/坡度派生字段重建测试。"""

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models.fit_data import FitData, FitRecord, FitSession
from services.fit_parser import FitParserService, FitSanitize


def _build_sample_fit_data() -> FitData:
    start = datetime(2026, 4, 4, 8, 0, 0, tzinfo=timezone.utc)
    records = [
        FitRecord(timestamp=start, latitude=0.0, longitude=0.0, altitude=0.0, speed=5.0),
        FitRecord(timestamp=start + timedelta(seconds=1), latitude=0.0, longitude=0.001, altitude=1.0, speed=5.0),
        FitRecord(timestamp=start + timedelta(seconds=2), latitude=50.0, longitude=50.0, altitude=100.0, speed=5.0),
        FitRecord(timestamp=start + timedelta(seconds=3), latitude=0.0, longitude=0.002, altitude=2.0, speed=5.0),
    ]
    fit_data = FitData(
        sessions=[FitSession(start_time=start, total_distance=250.0, records=records)],
        available_fields=["timestamp", "latitude", "longitude", "altitude", "speed"],
    )
    fit_data._distance_is_fallback = True
    return fit_data


class TestFitDerivedMetrics(unittest.TestCase):
    def test_rebuild_distance_skips_glitch_points(self):
        fit_data = _build_sample_fit_data()
        fit_data._glitch_cache = {
            "ms55.0": {
                "glitch_indices": [2],
                "glitch_details": [],
                "total_records": len(fit_data.primary_session.records),
                "glitch_count": 1,
            }
        }

        result = FitParserService.rebuild_derived_metrics(
            fit_data,
            rebuild_distance=True,
            filter_glitches_for_distance=True,
            recompute_gradient=True,
        )

        records = fit_data.primary_session.records
        self.assertTrue(result["distance_rebuilt"])
        self.assertTrue(result["gradient_recomputed"])
        self.assertLess(records[-1].distance, 300.0)
        self.assertLess(fit_data.haversine_total_distance, 300.0)
        self.assertAlmostEqual(records[2].distance, records[1].distance, places=3)
        self.assertTrue(any(record.gradient is not None for record in records))

    def test_sanitize_rebuilds_distance_and_gradient(self):
        fit_data = _build_sample_fit_data()
        for idx, record in enumerate(fit_data.primary_session.records):
            record.distance = 1_000_000.0 + idx * 1000.0
            record.gradient = 88.8
        fit_data.available_fields.extend(["distance", "gradient"])
        fit_data.haversine_total_distance = 1_234_567.0

        report = FitSanitize.sanitize(fit_data)

        records = fit_data.primary_session.records
        self.assertGreater(report["removed_count"], 0)
        self.assertLess(len(records), 4)
        self.assertLess(records[-1].distance, 300.0)
        self.assertLess(fit_data.haversine_total_distance, 300.0)
        self.assertTrue(all(record.distance is not None for record in records))


if __name__ == "__main__":
    unittest.main()
