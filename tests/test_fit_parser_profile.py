"""FIT 解析正确性与性能评估测试。

默认只运行轻量的正确性快照测试。
若要执行耗时较长的基准对比，请设置环境变量：

    RUN_FIT_PARSER_BENCHMARK=1
"""

import os
import sys
import time
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKSPACE_ROOT = os.path.dirname(PROJECT_ROOT)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from services.fit_parser import (
    FitParserService,
    fit_tool_record_factory_optimization,
)


FIT_PATH = os.path.join(WORKSPACE_ROOT, "Zepp20260404075746.fit")


class TestFitParserProfile(unittest.TestCase):
    def test_parse_snapshot_for_speed_fields(self):
        fit_data = FitParserService.parse(FIT_PATH)
        self.assertIsNotNone(fit_data)

        session = fit_data.primary_session
        self.assertIsNotNone(session)
        self.assertEqual(len(fit_data.sessions), 1)
        self.assertEqual(len(fit_data.lap_markers), 1)
        self.assertEqual(len(session.records), 47564)
        self.assertEqual(
            sum(1 for record in session.records if record.speed is not None),
            47564,
        )
        self.assertEqual(
            fit_data.available_fields,
            [
                "altitude",
                "cadence",
                "distance",
                "gradient",
                "heart_rate",
                "latitude",
                "longitude",
                "speed",
                "timestamp",
            ],
        )

        first = session.records[0]
        mid = session.records[23782]
        last = session.records[-1]

        self.assertEqual(first.timestamp.isoformat(), "2026-04-03T23:57:46+00:00")
        self.assertAlmostEqual(first.latitude, 22.55317999050021, places=10)
        self.assertAlmostEqual(first.longitude, 113.85980763472617, places=10)
        self.assertEqual(first.speed, 0.0)
        self.assertEqual(first.distance, 0.0)

        self.assertEqual(mid.timestamp.isoformat(), "2026-04-04T08:26:35+00:00")
        self.assertAlmostEqual(mid.speed, 6.02, places=2)
        self.assertAlmostEqual(mid.altitude, 64.8, places=1)
        self.assertEqual(mid.heart_rate, 131)
        self.assertAlmostEqual(mid.gradient, -1.3026383603744993, places=9)

        self.assertEqual(last.timestamp.isoformat(), "2026-04-04T17:05:27+00:00")
        self.assertAlmostEqual(last.latitude, 22.592672249302268, places=10)
        self.assertAlmostEqual(last.longitude, 114.89987952634692, places=10)
        self.assertEqual(last.speed, 0.0)
        self.assertEqual(last.heart_rate, 255)

    @unittest.skipUnless(
        os.getenv("RUN_FIT_PARSER_BENCHMARK") == "1",
        "Set RUN_FIT_PARSER_BENCHMARK=1 to run the slow benchmark.",
    )
    def test_benchmark_optimized_vs_original_record_factory(self):
        def parse_once(enabled: bool):
            with fit_tool_record_factory_optimization(enabled):
                start = time.perf_counter()
                data = FitParserService.parse(FIT_PATH)
                elapsed = time.perf_counter() - start
            return data, elapsed

        # Warm up each code path once to reduce import/cache noise.
        parse_once(False)
        parse_once(True)

        original_data, original_time = parse_once(False)
        optimized_data, optimized_time = parse_once(True)

        self.assertLess(
            optimized_time,
            original_time * 0.75,
            msg=(
                f"optimized parse should be faster than original: "
                f"original={original_time:.3f}s optimized={optimized_time:.3f}s"
            ),
        )

        original_records = original_data.primary_session.records
        optimized_records = optimized_data.primary_session.records
        self.assertEqual(len(original_records), len(optimized_records))

        for index in (0, 1, 12345, 23782, len(original_records) - 1):
            with self.subTest(index=index):
                original = original_records[index]
                optimized = optimized_records[index]
                self.assertEqual(original.timestamp, optimized.timestamp)
                self.assertEqual(original.heart_rate, optimized.heart_rate)
                self.assertEqual(original.cadence, optimized.cadence)
                self.assertAlmostEqual(original.latitude, optimized.latitude, places=10)
                self.assertAlmostEqual(original.longitude, optimized.longitude, places=10)
                self.assertAlmostEqual(original.speed, optimized.speed, places=6)
                self.assertAlmostEqual(original.distance, optimized.distance, places=6)
                if original.altitude is None:
                    self.assertIsNone(optimized.altitude)
                else:
                    self.assertAlmostEqual(original.altitude, optimized.altitude, places=6)

        print(
            f"\nFIT parser benchmark: original={original_time:.3f}s "
            f"optimized={optimized_time:.3f}s "
            f"speedup={original_time / optimized_time:.2f}x"
        )


if __name__ == "__main__":
    unittest.main()
