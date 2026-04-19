"""DistanceCounter 总距离选择规则测试。"""

import os
import sys
import unittest
from datetime import datetime, timezone


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from models.fit_data import FitData, FitRecord, FitSession
from services.frame_renderer import FrameRenderer


class TestDistanceCounterTotal(unittest.TestCase):
    def test_total_distance_uses_max_record_when_greater_than_session_total(self):
        start = datetime(2026, 4, 4, 8, 0, 0, tzinfo=timezone.utc)
        fit_data = FitData(
            sessions=[
                FitSession(
                    start_time=start,
                    total_distance=153_780.0,
                    records=[
                        FitRecord(timestamp=start, distance=0.0),
                        FitRecord(timestamp=start, distance=159_231.4894232361),
                    ],
                )
            ],
            haversine_total_distance=159_231.4894232361,
        )

        total_km = FrameRenderer._resolve_total_distance_km(fit_data)

        self.assertAlmostEqual(total_km, 159.2314894232361, places=6)

    def test_total_distance_prefers_session_total_when_it_is_greater(self):
        start = datetime(2026, 4, 4, 8, 0, 0, tzinfo=timezone.utc)
        fit_data = FitData(
            sessions=[
                FitSession(
                    start_time=start,
                    total_distance=200_000.0,
                    records=[
                        FitRecord(timestamp=start, distance=0.0),
                        FitRecord(timestamp=start, distance=159_231.4894232361),
                    ],
                )
            ],
            haversine_total_distance=159_231.4894232361,
        )

        total_km = FrameRenderer._resolve_total_distance_km(fit_data)

        self.assertAlmostEqual(total_km, 200.0, places=6)


if __name__ == "__main__":
    unittest.main()
