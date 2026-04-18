"""FIT 文件解析服务"""
import copy
import contextlib
import math
import os
import threading
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Optional

from models.fit_data import (
    FitData, FitSession, FitRecord,
    SanitizeConfig, SmoothingConfig,
)


# sport 编码映射
SPORT_MAP = {
    0: "generic", 1: "running", 2: "cycling", 3: "transition",
    4: "fitness_equipment", 5: "swimming", 6: "basketball",
    7: "soccer", 8: "tennis", 9: "american_football",
    10: "training", 11: "walking", 12: "cross_country_skiing",
    13: "alpine_skiing", 14: "snowboarding", 15: "rowing",
    16: "mountaineering", 17: "hiking", 18: "multisport",
}


_FIT_TOOL_PATCH_LOCK = threading.RLock()
_FIT_TOOL_OPTIMIZED_FACTORY = None
_FIT_TOOL_ORIGINAL_FACTORY = None


def _ensure_optimized_fit_tool_record_factory():
    """安装 fit_tool 的轻量 RecordMessage 工厂。

    `fit_tool` 默认会为每条 record 构造 70+ 个字段对象，而 CyclingWorkshop
    实际只读取十来个常用字段。这里将 RecordMessage 缩减为项目真正需要的
    字段集合，避免在 47k+ 条记录上重复做无用对象创建。
    """
    global _FIT_TOOL_OPTIMIZED_FACTORY, _FIT_TOOL_ORIGINAL_FACTORY

    with _FIT_TOOL_PATCH_LOCK:
        if _FIT_TOOL_OPTIMIZED_FACTORY is not None:
            return _FIT_TOOL_OPTIMIZED_FACTORY

        from fit_tool.data_message import DataMessage
        from fit_tool.profile.messages.message_factory import MessageFactory
        from fit_tool.profile.messages.record_message import (
            RecordMessage as FitToolRecordMessage,
            TimestampField,
            RecordPositionLatField,
            RecordPositionLongField,
            RecordAltitudeField,
            RecordHeartRateField,
            RecordCadenceField,
            RecordDistanceField,
            RecordSpeedField,
            RecordPowerField,
            RecordTemperatureField,
            RecordGradeField,
            RecordEnhancedSpeedField,
            RecordEnhancedAltitudeField,
        )

        _FIT_TOOL_ORIGINAL_FACTORY = MessageFactory.from_definition

        class SlimRecordMessage(DataMessage):
            NAME = FitToolRecordMessage.NAME
            ID = FitToolRecordMessage.ID

            def __init__(self, definition_message=None, developer_fields=None, local_id: int = 0, endian=None):
                size_by_id = {}
                if definition_message is not None:
                    size_by_id = {
                        field_definition.field_id: field_definition.size
                        for field_definition in definition_message.field_definitions
                    }

                def field_size(field_id: int) -> int:
                    return size_by_id.get(field_id, 0)

                super().__init__(
                    name=SlimRecordMessage.NAME,
                    global_id=SlimRecordMessage.ID,
                    local_id=definition_message.local_id if definition_message else local_id,
                    endian=definition_message.endian if definition_message else endian,
                    definition_message=definition_message,
                    developer_fields=developer_fields,
                    fields=[
                        TimestampField(size=field_size(TimestampField.ID), growable=definition_message is None),
                        RecordPositionLatField(size=field_size(RecordPositionLatField.ID), growable=definition_message is None),
                        RecordPositionLongField(size=field_size(RecordPositionLongField.ID), growable=definition_message is None),
                        RecordAltitudeField(size=field_size(RecordAltitudeField.ID), growable=definition_message is None),
                        RecordHeartRateField(size=field_size(RecordHeartRateField.ID), growable=definition_message is None),
                        RecordCadenceField(size=field_size(RecordCadenceField.ID), growable=definition_message is None),
                        RecordDistanceField(size=field_size(RecordDistanceField.ID), growable=definition_message is None),
                        RecordSpeedField(size=field_size(RecordSpeedField.ID), growable=definition_message is None),
                        RecordPowerField(size=field_size(RecordPowerField.ID), growable=definition_message is None),
                        RecordTemperatureField(size=field_size(RecordTemperatureField.ID), growable=definition_message is None),
                        RecordGradeField(size=field_size(RecordGradeField.ID), growable=definition_message is None),
                        RecordEnhancedSpeedField(size=field_size(RecordEnhancedSpeedField.ID), growable=definition_message is None),
                        RecordEnhancedAltitudeField(size=field_size(RecordEnhancedAltitudeField.ID), growable=definition_message is None),
                    ],
                )
                self._field_map = {field.field_id: field for field in self.fields}

            def get_field(self, field_id: int):
                return self._field_map.get(field_id)

            def _value(self, field_id: int):
                field = self._field_map.get(field_id)
                if field and field.is_valid():
                    sub_field = field.get_valid_sub_field(self.fields)
                    return field.get_value(sub_field=sub_field)
                return None

            @property
            def timestamp(self):
                return self._value(TimestampField.ID)

            @property
            def position_lat(self):
                return self._value(RecordPositionLatField.ID)

            @property
            def position_long(self):
                return self._value(RecordPositionLongField.ID)

            @property
            def altitude(self):
                return self._value(RecordAltitudeField.ID)

            @property
            def enhanced_altitude(self):
                return self._value(RecordEnhancedAltitudeField.ID)

            @property
            def heart_rate(self):
                return self._value(RecordHeartRateField.ID)

            @property
            def cadence(self):
                return self._value(RecordCadenceField.ID)

            @property
            def distance(self):
                return self._value(RecordDistanceField.ID)

            @property
            def speed(self):
                return self._value(RecordSpeedField.ID)

            @property
            def enhanced_speed(self):
                return self._value(RecordEnhancedSpeedField.ID)

            @property
            def power(self):
                return self._value(RecordPowerField.ID)

            @property
            def temperature(self):
                return self._value(RecordTemperatureField.ID)

            @property
            def grade(self):
                return self._value(RecordGradeField.ID)

        def optimized_from_definition(definition_message, developer_fields):
            if definition_message.global_id == FitToolRecordMessage.ID:
                return SlimRecordMessage(
                    definition_message=definition_message,
                    developer_fields=developer_fields,
                )
            return _FIT_TOOL_ORIGINAL_FACTORY(definition_message, developer_fields)

        _FIT_TOOL_OPTIMIZED_FACTORY = optimized_from_definition
        MessageFactory.from_definition = staticmethod(optimized_from_definition)
        return _FIT_TOOL_OPTIMIZED_FACTORY


@contextlib.contextmanager
def fit_tool_record_factory_optimization(enabled: bool = True):
    """测试/基准用：临时切换 fit_tool RecordMessage 优化开关。"""
    with _FIT_TOOL_PATCH_LOCK:
        _ensure_optimized_fit_tool_record_factory()

        from fit_tool.profile.messages.message_factory import MessageFactory

        previous_factory = MessageFactory.from_definition
        target_factory = _FIT_TOOL_OPTIMIZED_FACTORY if enabled else _FIT_TOOL_ORIGINAL_FACTORY
        MessageFactory.from_definition = staticmethod(target_factory)
        try:
            yield
        finally:
            MessageFactory.from_definition = staticmethod(previous_factory)


class FitParserService:
    """FIT 文件解析"""

    _GRADIENT_WINDOW_M = 12.0
    _GRADIENT_MIN_DISTANCE_M = 4.0
    _GRADIENT_STOP_SPEED_MS = 0.8
    _GRADIENT_SMOOTH_RADIUS = 2

    @staticmethod
    def parse(file_path: str) -> Optional[FitData]:
        """解析 FIT 文件，返回 FitData"""
        if not os.path.isfile(file_path):
            return None

        try:
            from fit_tool.fit_file import FitFile
            from fit_tool.profile.messages.record_message import RecordMessage as FitToolRecordMessage
            from fit_tool.profile.messages.session_message import SessionMessage as FitToolSessionMessage
            from fit_tool.profile.messages.lap_message import LapMessage as FitToolLapMessage

            _ensure_optimized_fit_tool_record_factory()
            print(f"[FitParser] 开始解析文件: {file_path}")
            fit_file = FitFile.from_file(file_path)
            print(f"[FitParser] 解析完成: {file_path}")
        except Exception as e:
            print(f"[FitParser] 解析失败: {e}")
            return None

        fit_data = FitData(file_path=file_path)
        available_fields = set()

        # 单次遍历：同时解析 sessions / records / lap markers
        sessions = []
        records = []
        lap_markers = []

        for r in fit_file.records:
            msg = r.message
            msg_type = type(msg).__name__
            if msg_type == "DefinitionMessage":
                continue

            msg_global_id = getattr(msg, "global_id", None)

            if msg_global_id == FitToolSessionMessage.ID or msg_type == "SessionMessage":
                session = FitSession()
                session.sport = SPORT_MAP.get(getattr(msg, 'sport', None), "unknown")
                raw_start = getattr(msg, 'start_time', None)
                session.start_time = FitParserService._ms_to_datetime(raw_start)
                session.total_distance = float(getattr(msg, 'total_distance', 0) or 0)
                session.total_elapsed_time = float(getattr(msg, 'total_elapsed_time', 0) or 0)
                session.total_timer_time = float(getattr(msg, 'total_timer_time', 0) or 0)
                session.total_ascent = float(getattr(msg, 'total_ascent', 0) or 0)
                session.total_descent = float(getattr(msg, 'total_descent', 0) or 0)
                session.avg_heart_rate = getattr(msg, 'avg_heart_rate', None)
                session.max_heart_rate = getattr(msg, 'max_heart_rate', None)
                raw_avg_speed = getattr(msg, 'enhanced_avg_speed', None)
                if raw_avg_speed is None:
                    raw_avg_speed = getattr(msg, 'avg_speed', None)
                raw_max_speed = getattr(msg, 'enhanced_max_speed', None)
                if raw_max_speed is None:
                    raw_max_speed = getattr(msg, 'max_speed', None)
                session.avg_speed = float(raw_avg_speed) if raw_avg_speed is not None else None
                session.max_speed = float(raw_max_speed) if raw_max_speed is not None else None
                session.avg_cadence = float(getattr(msg, 'avg_cadence', 0) or 0)
                session.max_cadence = getattr(msg, 'max_cadence', None)
                sessions.append(session)

            elif msg_global_id == FitToolRecordMessage.ID or msg_type == "RecordMessage":
                rec = FitRecord()
                raw_ts = getattr(msg, 'timestamp', None)
                rec.timestamp = FitParserService._ms_to_datetime(raw_ts)
                if rec.timestamp:
                    available_fields.add("timestamp")

                lat = getattr(msg, 'position_lat', None)
                lon = getattr(msg, 'position_long', None)
                if lat is not None:
                    rec.latitude = float(lat)
                    available_fields.add("latitude")
                if lon is not None:
                    rec.longitude = float(lon)
                    available_fields.add("longitude")

                alt = getattr(msg, 'enhanced_altitude', None)
                if alt is None:
                    alt = getattr(msg, 'altitude', None)
                if alt is not None:
                    rec.altitude = float(alt)
                    available_fields.add("altitude")

                hr = getattr(msg, 'heart_rate', None)
                if hr is not None:
                    rec.heart_rate = int(hr)
                    available_fields.add("heart_rate")

                cad = getattr(msg, 'cadence', None)
                if cad is not None:
                    rec.cadence = int(cad)
                    available_fields.add("cadence")

                spd = getattr(msg, 'enhanced_speed', None)
                if spd is None:
                    spd = getattr(msg, 'speed', None)
                if spd is not None:
                    rec.speed = float(spd)
                    available_fields.add("speed")

                dist = getattr(msg, 'distance', None)
                if dist is not None:
                    rec.distance = float(dist)
                    available_fields.add("distance")

                pwr = getattr(msg, 'power', None)
                if pwr is not None:
                    rec.power = int(pwr)
                    available_fields.add("power")

                temp = getattr(msg, 'temperature', None)
                if temp is not None:
                    rec.temperature = float(temp)
                    available_fields.add("temperature")

                grad = getattr(msg, 'gradient', None)
                if grad is None:
                    grad = getattr(msg, 'grade', None)
                if grad is not None:
                    rec.gradient = float(grad)
                    available_fields.add("gradient")

                if rec.timestamp is not None:
                    records.append(rec)

            elif msg_global_id == FitToolLapMessage.ID or msg_type == "LapMessage":
                raw_ts = getattr(msg, 'timestamp', None)
                dt = FitParserService._ms_to_datetime(raw_ts)
                if dt:
                    lap_markers.append(dt)

        # 关联 records 到 session
        if sessions:
            sessions[0].records = records
        elif records:
            session = FitSession(
                sport="unknown",
                start_time=records[0].timestamp if records else None,
                total_elapsed_time=(
                    (records[-1].timestamp - records[0].timestamp).total_seconds()
                    if len(records) > 1 and records[0].timestamp and records[-1].timestamp
                    else 0
                ),
                records=records,
            )
            sessions.append(session)

        fit_data.sessions = sessions
        fit_data.available_fields = sorted(available_fields)
        fit_data.lap_markers = lap_markers

        # ── haversine 全路径积分：计算总距离（辅助信息）+ Distance 回退 ──
        if records:
            cum_dist = 0.0
            prev_lat, prev_lon = None, None
            has_any_coord = any(r.latitude is not None and r.longitude is not None for r in records)
            need_distance_fallback = "distance" not in available_fields
            if has_any_coord:
                for r in records:
                    if r.latitude is not None and r.longitude is not None:
                        if prev_lat is not None and prev_lon is not None:
                            cum_dist += FitSanitize.haversine(prev_lat, prev_lon, r.latitude, r.longitude)
                        prev_lat, prev_lon = r.latitude, r.longitude
                    # 回退：写入每条 record.distance
                    if need_distance_fallback:
                        r.distance = cum_dist
                fit_data.haversine_total_distance = cum_dist
                if need_distance_fallback:
                    available_fields.add("distance")
                    fit_data.available_fields = sorted(available_fields)

            if FitParserService._compute_gradients(records):
                available_fields.add("gradient")
                fit_data.available_fields = sorted(available_fields)

        return fit_data

    @staticmethod
    def _compute_gradients(records: list[FitRecord]) -> bool:
        """为记录预计算平滑坡度，减少渲染期重复计算与静止抖动。"""
        if not records:
            return False

        raw_values = []
        has_any = False
        for i, rec in enumerate(records):
            raw = rec.gradient
            if raw is None:
                raw = FitParserService._estimate_gradient(records, i)
            if raw is not None:
                has_any = True
            raw_values.append(raw)

        if not has_any:
            return False

        stable_values = []
        last_stable = 0.0
        for rec, raw in zip(records, raw_values):
            is_stopped = (
                rec.speed is not None and
                rec.speed <= FitParserService._GRADIENT_STOP_SPEED_MS
            )
            if raw is None or is_stopped:
                stable = last_stable
            else:
                stable = raw
                last_stable = raw
            stable_values.append(stable)

        smoothed = stable_values[:]
        radius = FitParserService._GRADIENT_SMOOTH_RADIUS
        for i, rec in enumerate(records):
            if rec.speed is not None and rec.speed <= FitParserService._GRADIENT_STOP_SPEED_MS:
                continue
            start = max(0, i - radius)
            end = min(len(stable_values), i + radius + 1)
            window = [v for v in stable_values[start:end] if v is not None]
            if window:
                window.sort()
                smoothed[i] = window[len(window) // 2]

        last_stable = 0.0
        for i, rec in enumerate(records):
            value = smoothed[i]
            if rec.speed is not None and rec.speed <= FitParserService._GRADIENT_STOP_SPEED_MS:
                rec.gradient = last_stable
            else:
                rec.gradient = value
                last_stable = value

        return True

    @staticmethod
    def _estimate_gradient(records: list[FitRecord], center_idx: int) -> Optional[float]:
        """用 altitude / distance 差估算单点坡度。"""
        center = records[center_idx]
        if center.altitude is None or center.distance is None:
            return None

        half_window = FitParserService._GRADIENT_WINDOW_M / 2.0

        left = center_idx
        while left > 0:
            if records[left].distance is not None and center.distance - records[left].distance >= half_window:
                break
            left -= 1

        right = center_idx
        while right < len(records) - 1:
            if records[right].distance is not None and records[right].distance - center.distance >= half_window:
                break
            right += 1

        r1 = records[left]
        r2 = records[right]
        if r1.altitude is None or r2.altitude is None:
            return None
        if r1.distance is None or r2.distance is None:
            return None

        dist_diff = r2.distance - r1.distance
        if dist_diff < FitParserService._GRADIENT_MIN_DISTANCE_M:
            return None

        gradient = ((r2.altitude - r1.altitude) / dist_diff) * 100.0
        return max(-99.9, min(99.9, gradient))

    @staticmethod
    def get_record_at(fit_data: FitData, target_time: datetime) -> Optional[FitRecord]:
        """按时间查询单条记录（线性插值）"""
        session = fit_data.primary_session
        if not session or not session.records:
            return None

        records = session.records

        if target_time <= records[0].timestamp:
            return records[0]
        if target_time >= records[-1].timestamp:
            return records[-1]

        lo, hi = 0, len(records) - 1
        while lo < hi - 1:
            mid = (lo + hi) // 2
            if records[mid].timestamp <= target_time:
                lo = mid
            else:
                hi = mid

        r1, r2 = records[lo], records[hi]
        if r1.timestamp == r2.timestamp:
            return r1

        ratio = (target_time - r1.timestamp).total_seconds() / (r2.timestamp - r1.timestamp).total_seconds()
        result = FitRecord(timestamp=target_time)
        for field_name in ("latitude", "longitude", "altitude", "speed",
                           "distance", "heart_rate", "cadence", "power",
                           "temperature", "gradient"):
            v1, v2 = getattr(r1, field_name), getattr(r2, field_name)
            if v1 is not None and v2 is not None:
                setattr(result, field_name, v1 + (v2 - v1) * ratio)
            elif v1 is not None:
                setattr(result, field_name, v1)
            elif v2 is not None:
                setattr(result, field_name, v2)

        return result

    @staticmethod
    def get_records_range(fit_data: FitData, start: datetime, end: datetime) -> list:
        session = fit_data.primary_session
        if not session:
            return []
        return [r for r in session.records
                if r.timestamp and start <= r.timestamp <= end]

    @staticmethod
    def get_track_coords(fit_data: FitData, filter_glitches: bool = True) -> list:
        """获取轨迹坐标

        Args:
            fit_data: FIT 数据
            filter_glitches: 是否过滤 GPS glitch 点（默认 True）

        优化：结果缓存在 fit_data._track_coords_cache 中，
        避免每次 render_frame 重复遍历 47k 记录构建坐标列表。
        """
        session = fit_data.primary_session
        if not session:
            return []

        # 检查缓存（以 records 数量 + filter_glitches 作为缓存键）
        cache_key = (len(session.records), filter_glitches)
        cached = getattr(fit_data, '_track_coords_cache', None)
        if cached is not None and cached[0] == cache_key:
            return cached[1]

        # 获取 glitch 索引集合（使用缓存）
        glitch_indices = set()
        if filter_glitches:
            glitch_result = fit_data.get_glitch_cache()
            glitch_indices = set(glitch_result["glitch_indices"])

        result = [(r.latitude, r.longitude) for i, r in enumerate(session.records)
                  if r.latitude is not None and r.longitude is not None
                  and i not in glitch_indices]

        # 缓存结果
        fit_data._track_coords_cache = (cache_key, result)
        return result

    @staticmethod
    def _ms_to_datetime(ms) -> Optional[datetime]:
        """将 FIT 毫秒时间戳转换为 datetime（UTC）"""
        if ms is None:
            return None
        try:
            return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc)
        except (ValueError, TypeError, OSError):
            return None

    # ── GPX 导出 ───────────────────────────

    @staticmethod
    def export_gpx(fit_data: FitData, output_path: str) -> str:
        """将 FitData 导出为 GPX 文件"""
        session = fit_data.primary_session
        if not session or not session.records:
            raise ValueError("没有可导出的数据")

        gpx = ET.Element("gpx",
                         version="1.1",
                         creator="CyclingWorkshop",
                         xmlns="http://www.topografix.com/GPX/1/1")

        metadata = ET.SubElement(gpx, "metadata")
        name_el = ET.SubElement(metadata, "name")
        sport = session.sport or "activity"
        start_str = session.start_time.strftime("%Y%m%d_%H%M%S") if session.start_time else "unknown"
        name_el.text = f"{sport}_{start_str}"

        trk = ET.SubElement(gpx, "trk")
        trk_name = ET.SubElement(trk, "name")
        trk_name.text = name_el.text

        trkseg = ET.SubElement(trk, "trkseg")

        for r in session.records:
            trkpt = ET.SubElement(trkseg, "trkpt")
            if r.latitude is not None:
                trkpt.set("lat", f"{r.latitude:.8f}")
            else:
                trkpt.set("lat", "0")
            if r.longitude is not None:
                trkpt.set("lon", f"{r.longitude:.8f}")
            else:
                trkpt.set("lon", "0")

            if r.timestamp:
                time_el = ET.SubElement(trkpt, "time")
                time_el.text = r.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ")

            if r.altitude is not None:
                ele = ET.SubElement(trkpt, "ele")
                ele.text = f"{r.altitude:.1f}"

            ext_fields = []
            if r.heart_rate is not None:
                ext_fields.append(f"<hr>{r.heart_rate}</hr>")
            if r.cadence is not None:
                ext_fields.append(f"<cad>{r.cadence}</cad>")
            if r.speed is not None:
                ext_fields.append(f"<spd>{r.speed:.2f}</spd>")
            if r.power is not None:
                ext_fields.append(f"<pwr>{r.power}</pwr>")
            if r.temperature is not None:
                ext_fields.append(f"<tmp>{r.temperature:.1f}</tmp>")

            if ext_fields:
                exts = ET.SubElement(trkpt, "extensions")
                exts.text = "".join(ext_fields)

        tree = ET.ElementTree(gpx)
        ET.indent(tree, space="  ")
        tree.write(output_path, encoding="utf-8", xml_declaration=True)

        return output_path


# ══════════════════════════════════════════════════════════
#  FitSanitize — 数据清洗（丢弃不合理记录）
# ══════════════════════════════════════════════════════════

class FitSanitize:
    """数据清洗：根据规则丢弃不合理的 FitRecord

    清洗是第一步原地变换操作，结果是移除不满足条件的记录行。
    """

    @staticmethod
    def sanitize(fit_data: FitData, config: SanitizeConfig = None) -> dict:
        """执行数据清洗，返回清洗报告

        修改 fit_data 原地（移除不合格记录），同时返回详细报告。

        Returns:
            {
                "removed_count": int,
                "original_count": int,
                "remaining_count": int,
                "details": {
                    "gps_glitch": int,
                    "gps_out_of_range": int,
                    "hr_range": int,
                    "hr_rate": int,
                    "speed_range": int,
                    "speed_accel": int,
                    "altitude_range": int,
                    "cadence_range": int,
                    "power_range": int,
                    "temperature_range": int,
                },
                "removed_indices": [int],  # 被移除的原始索引
            }
        """
        if config is None:
            config = SanitizeConfig()

        session = fit_data.primary_session
        if not session or not session.records:
            return {
                "removed_count": 0, "original_count": 0, "remaining_count": 0,
                "details": {}, "removed_indices": [],
            }

        records = session.records
        original_count = len(records)
        remove_set = set()
        details = {
            "gps_glitch": 0,
            "gps_out_of_range": 0,
            "hr_range": 0,
            "hr_rate": 0,
            "speed_range": 0,
            "speed_accel": 0,
            "altitude_range": 0,
            "cadence_range": 0,
            "power_range": 0,
            "temperature_range": 0,
        }

        # ── 1. GPS 清洗 ──
        if config.gps_filter_glitches or config.gps_out_of_range:
            for i, r in enumerate(records):
                if r.latitude is None or r.longitude is None:
                    continue

                # GPS 坐标范围外
                if config.gps_out_of_range:
                    if (r.latitude < -90 or r.latitude > 90 or
                            r.longitude < -180 or r.longitude > 180 or
                            (r.longitude == 180.0 and r.latitude == 180.0)):
                        remove_set.add(i)
                        details["gps_out_of_range"] += 1

            # GPS glitch 检测（速度跳跃 + bounce）
            if config.gps_filter_glitches:
                glitch_result = FitSanitize.detect_gps_glitches(
                    fit_data, max_speed_ms=config.gps_max_speed_ms)
                for idx in glitch_result["glitch_indices"]:
                    if idx not in remove_set:
                        remove_set.add(idx)
                        details["gps_glitch"] += 1

        # ── 2. 心率范围检查 ──
        lo, hi = config.hr_range
        for i, r in enumerate(records):
            if r.heart_rate is not None:
                if r.heart_rate < lo or r.heart_rate > hi:
                    remove_set.add(i)
                    details["hr_range"] += 1

        # ── 3. 心率变化率检查 ──
        if config.hr_enable_rate_check:
            for i in range(1, len(records)):
                r_prev = records[i - 1]
                r_curr = records[i]
                if (r_prev.heart_rate is not None and r_curr.heart_rate is not None
                        and r_prev.timestamp and r_curr.timestamp):
                    dt = (r_curr.timestamp - r_prev.timestamp).total_seconds()
                    if dt > 0:
                        rate = abs(r_curr.heart_rate - r_prev.heart_rate) / dt
                        if rate > config.hr_max_rate:
                            # 移除变化率异常的那个点（偏离更远的）
                            remove_set.add(i)
                            details["hr_rate"] += 1

        # ── 4. 速度范围检查 ──
        lo, hi = config.speed_range
        for i, r in enumerate(records):
            if r.speed is not None:
                if r.speed < lo or r.speed > hi:
                    remove_set.add(i)
                    details["speed_range"] += 1

        # ── 5. 速度加速度检查 ──
        if config.speed_enable_accel_check:
            for i in range(1, len(records)):
                r_prev = records[i - 1]
                r_curr = records[i]
                if (r_prev.speed is not None and r_curr.speed is not None
                        and r_prev.timestamp and r_curr.timestamp):
                    dt = (r_curr.timestamp - r_prev.timestamp).total_seconds()
                    if dt > 0:
                        accel = abs(r_curr.speed - r_prev.speed) / dt
                        if accel > config.speed_max_accel:
                            remove_set.add(i)
                            details["speed_accel"] += 1

        # ── 6. 海拔范围检查 ──
        lo, hi = config.altitude_range
        for i, r in enumerate(records):
            if r.altitude is not None:
                if r.altitude < lo or r.altitude > hi:
                    remove_set.add(i)
                    details["altitude_range"] += 1

        # ── 7. 踏频范围检查 ──
        lo, hi = config.cadence_range
        for i, r in enumerate(records):
            if r.cadence is not None:
                if r.cadence < lo or r.cadence > hi:
                    remove_set.add(i)
                    details["cadence_range"] += 1

        # ── 8. 功率范围检查 ──
        lo, hi = config.power_range
        for i, r in enumerate(records):
            if r.power is not None:
                if r.power < lo or r.power > hi:
                    remove_set.add(i)
                    details["power_range"] += 1

        # ── 9. 温度范围检查 ──
        lo, hi = config.temperature_range
        for i, r in enumerate(records):
            if r.temperature is not None:
                if r.temperature < lo or r.temperature > hi:
                    remove_set.add(i)
                    details["temperature_range"] += 1

        # 执行移除
        removed_indices = sorted(remove_set)
        session.records = [r for i, r in enumerate(records) if i not in remove_set]

        return {
            "removed_count": len(removed_indices),
            "original_count": original_count,
            "remaining_count": len(session.records),
            "details": details,
            "removed_indices": removed_indices,
        }

    @staticmethod
    def detect_gps_glitches(fit_data: FitData, max_speed_ms: float = None) -> dict:
        """基于距离+时间检测 GPS glitch 点（保留原接口兼容）"""
        if max_speed_ms is None:
            max_speed_ms = 55.0

        session = fit_data.primary_session
        if not session or not session.records:
            return {"glitch_indices": [], "glitch_details": [], "total_records": 0, "glitch_count": 0}

        records = session.records
        n = len(records)
        glitch_set = set()
        details = []

        # 阶段1：范围检查
        for i, r in enumerate(records):
            if r.latitude is not None and r.longitude is not None:
                out_of_range = False
                if r.latitude < -90 or r.latitude > 90:
                    out_of_range = True
                elif r.longitude < -180 or r.longitude > 180:
                    out_of_range = True
                elif r.longitude == 180.0 and r.latitude == 180.0:
                    out_of_range = True
                if out_of_range:
                    glitch_set.add(i)
                    details.append({
                        "index": i, "type": "out_of_range",
                        "lat": r.latitude, "lon": r.longitude,
                        "speed_ms": None, "distance_m": None, "time_diff_s": None,
                    })

        # 阶段2：速度跳跃检测
        for i in range(1, n):
            r_prev = records[i - 1]
            r_curr = records[i]
            if (r_prev.latitude is None or r_prev.longitude is None or
                    r_curr.latitude is None or r_curr.longitude is None):
                continue
            if i - 1 in glitch_set or i in glitch_set:
                continue
            dist = FitSanitize.haversine(
                r_prev.latitude, r_prev.longitude, r_curr.latitude, r_curr.longitude)
            if r_prev.timestamp and r_curr.timestamp:
                time_diff = (r_curr.timestamp - r_prev.timestamp).total_seconds()
            else:
                time_diff = 1.0
            if time_diff <= 0:
                time_diff = 0.001
            speed = dist / time_diff
            if speed > max_speed_ms:
                glitch_idx = FitSanitize._identify_glitch_point(
                    records, i - 1, i, glitch_set, max_speed_ms)
                if glitch_idx is not None and glitch_idx not in glitch_set:
                    glitch_set.add(glitch_idx)
                    r = records[glitch_idx]
                    details.append({
                        "index": glitch_idx, "type": "speed_jump",
                        "lat": r.latitude, "lon": r.longitude,
                        "speed_ms": round(speed, 1), "distance_m": round(dist, 1),
                        "time_diff_s": round(time_diff, 2),
                    })

        # 阶段3：bounce 检测
        for i in range(1, n - 1):
            if i in glitch_set:
                continue
            r_prev = records[i - 1]
            r_curr = records[i]
            r_next = records[i + 1]
            if (r_prev.latitude is None or r_prev.longitude is None or
                    r_curr.latitude is None or r_curr.longitude is None or
                    r_next.latitude is None or r_next.longitude is None):
                continue
            if (i - 1) in glitch_set or (i + 1) in glitch_set:
                continue
            dist_aa = FitSanitize.haversine(
                r_prev.latitude, r_prev.longitude, r_next.latitude, r_next.longitude)
            dist_ab = FitSanitize.haversine(
                r_prev.latitude, r_prev.longitude, r_curr.latitude, r_curr.longitude)
            dist_ba = FitSanitize.haversine(
                r_curr.latitude, r_curr.longitude, r_next.latitude, r_next.longitude)
            if dist_aa > 0 and dist_ab > dist_aa * 10 and dist_ba > dist_aa * 10:
                if r_prev.timestamp and r_curr.timestamp:
                    time_diff = (r_curr.timestamp - r_prev.timestamp).total_seconds()
                    if time_diff > 0:
                        speed = dist_ab / time_diff
                        if speed > max_speed_ms * 0.5:
                            glitch_set.add(i)
                            details.append({
                                "index": i, "type": "bounce",
                                "lat": r_curr.latitude, "lon": r_curr.longitude,
                                "speed_ms": round(speed, 1), "distance_m": round(dist_ab, 1),
                                "time_diff_s": round(time_diff, 2),
                            })

        glitch_indices = sorted(glitch_set)
        return {
            "glitch_indices": glitch_indices,
            "glitch_details": sorted(details, key=lambda d: d["index"]),
            "total_records": n,
            "glitch_count": len(glitch_indices),
        }

    @staticmethod
    def _identify_glitch_point(records, idx_a, idx_b, existing_glitches, max_speed_ms):
        """在两个相邻点之间判断哪个是 glitch 点"""
        window = 5
        neighbors_b = []
        for offset in range(1, window + 1):
            ni = idx_b + offset
            if ni < len(records) and ni not in existing_glitches:
                r = records[ni]
                if r.latitude is not None and r.longitude is not None:
                    neighbors_b.append(r)

        if neighbors_b:
            r_b = records[idx_b]
            avg_dist_b = sum(
                FitSanitize.haversine(r_b.latitude, r_b.longitude, nb.latitude, nb.longitude)
                for nb in neighbors_b[:2]
            ) / min(len(neighbors_b), 2)
            r_a = records[idx_a]
            avg_dist_a_from_b = sum(
                FitSanitize.haversine(r_a.latitude, r_a.longitude, nb.latitude, nb.longitude)
                for nb in neighbors_b[:2]
            ) / min(len(neighbors_b), 2)
            if avg_dist_a_from_b > avg_dist_b * 3:
                return idx_a
            else:
                return idx_b
        return idx_b

    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        """计算两点之间的 Haversine 距离（米）"""
        R = 6371000
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlam = math.radians(lon2 - lon1)
        a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))


# ══════════════════════════════════════════════════════════
#  FitFilter — 窗函数平滑滤波
# ══════════════════════════════════════════════════════════

class FitFilter:
    """窗函数平滑滤波：第二步原地变换操作（可选）

    只做平滑，不做记录移除。支持：
    - 中值滤波 (median)
    - 移动平均 (moving_avg)
    - 高斯加权 (gaussian)
    """

    @staticmethod
    def smooth(fit_data: FitData, config: SmoothingConfig) -> dict:
        """对 FitData 应用平滑滤波，原地修改

        Returns:
            {
                "smoothed_fields": [str],
                "method": str,
                "window_size": int,
            }
        """
        if not config.enabled or not config.fields:
            return {"smoothed_fields": [], "method": config.method, "window_size": config.window_size}

        session = fit_data.primary_session
        if not session or not session.records:
            return {"smoothed_fields": [], "method": config.method, "window_size": config.window_size}

        records = session.records
        smoothed = []

        for field_name in config.fields:
            values = [getattr(r, field_name) for r in records]

            if config.method == "median":
                values = FitFilter._median_filter(values, config.window_size)
            elif config.method == "moving_avg":
                values = FitFilter._moving_average(values, config.window_size)
            elif config.method == "gaussian":
                values = FitFilter._gaussian_filter(values, config.window_size)

            # 写回
            for i, r in enumerate(records):
                setattr(r, field_name, values[i])
            smoothed.append(field_name)

        return {
            "smoothed_fields": smoothed,
            "method": config.method,
            "window_size": config.window_size,
        }

    # ── 兼容旧接口 ──

    @staticmethod
    def apply_filter(fit_data: FitData, filter_config: dict) -> FitData:
        """兼容旧 API：对 FitData 应用滤波，返回新的 FitData

        filter_config:
        {
            "fields": ["heart_rate", "speed", ...],
            "method": "median" | "moving_avg" | "remove_outliers",
            "window_size": 5,
            "sigma": 3.0,
            "fill": "interpolate" | "remove",
        }
        """
        new_data = copy.deepcopy(fit_data)
        session = new_data.primary_session
        if not session or not session.records:
            return new_data

        records = session.records
        fields = filter_config.get("fields", [])
        method = filter_config.get("method", "median")
        window = filter_config.get("window_size", 5)
        sigma = filter_config.get("sigma", 3.0)
        fill = filter_config.get("fill", "interpolate")

        # GPS glitch 预处理
        if any(f in fields for f in ("latitude", "longitude")):
            glitch_result = new_data.get_glitch_cache()
            glitch_indices = set(glitch_result["glitch_indices"])
            for idx in glitch_indices:
                if "latitude" in fields:
                    records[idx].latitude = None
                if "longitude" in fields:
                    records[idx].longitude = None

        for field in fields:
            values = [getattr(r, field) for r in records]

            if method == "remove_outliers":
                non_none = [(i, v) for i, v in enumerate(values) if v is not None]
                if len(non_none) < 3:
                    continue
                nums = [v for _, v in non_none]
                mean = sum(nums) / len(nums)
                std = math.sqrt(sum((v - mean) ** 2 for v in nums) / len(nums))
                if std < 1e-9:
                    continue
                for i, v in non_none:
                    if abs(v - mean) > sigma * std:
                        values[i] = None
                # 范围检测
                FIELD_RANGES = {
                    "latitude": (-90, 90), "longitude": (-180, 180),
                    "altitude": (-500, 9000), "heart_rate": (30, 250),
                    "cadence": (0, 250), "speed": (0, 55),
                    "distance": (0, 1e7), "power": (0, 2500), "temperature": (-40, 60),
                }
                lo, hi = FIELD_RANGES.get(field, (None, None))
                if lo is not None:
                    for i, v in enumerate(values):
                        if v is not None and (v < lo or v > hi):
                            values[i] = None
                if fill == "interpolate":
                    values = FitFilter._interpolate_gaps(values)
                elif fill == "remove":
                    for i, v in enumerate(values):
                        if v is None:
                            setattr(records[i], field, None)
                    continue

            elif method == "median":
                values = FitFilter._median_filter(values, window)
            elif method == "moving_avg":
                values = FitFilter._moving_average(values, window)

            for i, r in enumerate(records):
                setattr(r, field, values[i])

        if method == "remove_outliers" and fill == "remove":
            session.records = [r for r in records
                               if all(getattr(r, f, None) is not None for f in fields)
                               or not any(getattr(r, f, None) is None for f in fields)]

        return new_data

    # ── 滤波算法 ──

    @staticmethod
    def _interpolate_gaps(values: list) -> list:
        """对 None 值做线性插值填充"""
        n = len(values)
        result = list(values)
        i = 0
        while i < n:
            if result[i] is None:
                left = i - 1
                while left >= 0 and result[left] is None:
                    left -= 1
                right = i + 1
                while right < n and result[right] is None:
                    right += 1
                if left >= 0 and right < n:
                    lv, rv = result[left], result[right]
                    span = right - left
                    for j in range(left + 1, right):
                        ratio = (j - left) / span
                        result[j] = lv + (rv - lv) * ratio
                elif left >= 0:
                    for j in range(i, right if right < n else n):
                        if result[j] is None:
                            result[j] = result[left]
                elif right < n:
                    for j in range(max(0, left + 1), right):
                        if result[j] is None:
                            result[j] = result[right]
                i = right
            else:
                i += 1
        return result

    @staticmethod
    def _median_filter(values: list, window: int = 5) -> list:
        """中值滤波，保留 None"""
        n = len(values)
        half = window // 2
        result = list(values)
        for i in range(n):
            if values[i] is None:
                continue
            lo = max(0, i - half)
            hi = min(n, i + half + 1)
            window_vals = sorted(v for v in values[lo:hi] if v is not None)
            if window_vals:
                result[i] = window_vals[len(window_vals) // 2]
        return result

    @staticmethod
    def _moving_average(values: list, window: int = 5) -> list:
        """移动平均，保留 None"""
        n = len(values)
        half = window // 2
        result = list(values)
        for i in range(n):
            if values[i] is None:
                continue
            lo = max(0, i - half)
            hi = min(n, i + half + 1)
            window_vals = [v for v in values[lo:hi] if v is not None]
            if window_vals:
                result[i] = sum(window_vals) / len(window_vals)
        return result

    @staticmethod
    def _gaussian_filter(values: list, window: int = 5) -> list:
        """高斯加权平滑，保留 None

        使用标准高斯核，sigma = window / 4
        """
        n = len(values)
        half = window // 2
        sigma = max(window / 4.0, 1.0)
        result = list(values)

        # 预计算高斯权重
        weights = []
        for k in range(-half, half + 1):
            w = math.exp(-0.5 * (k / sigma) ** 2)
            weights.append(w)

        for i in range(n):
            if values[i] is None:
                continue
            weighted_sum = 0.0
            weight_total = 0.0
            for k, w in enumerate(weights):
                j = i + k - half
                if 0 <= j < n and values[j] is not None:
                    weighted_sum += values[j] * w
                    weight_total += w
            if weight_total > 0:
                result[i] = weighted_sum / weight_total

        return result


# ══════════════════════════════════════════════════════════
#  兼容旧 API：detect_outliers 保留
# ══════════════════════════════════════════════════════════

# 各字段的物理合理范围（兼容旧 API）
FIELD_RANGES = {
    "latitude": (-90, 90),
    "longitude": (-180, 180),
    "altitude": (-500, 9000),
    "heart_rate": (30, 250),
    "cadence": (0, 250),
    "speed": (0, 55),
    "distance": (0, 1e7),
    "power": (0, 2500),
    "temperature": (-40, 60),
}


def detect_outliers_compat(fit_data: FitData, fields: list = None,
                            sigma: float = 3.0) -> dict:
    """兼容旧 API 的异常值检测"""
    session = fit_data.primary_session
    if not session or not session.records:
        return {"total_records": 0, "outliers": {}, "any_outlier_indices": []}

    records = session.records
    if fields is None:
        fields = [f for f in ("latitude", "longitude", "altitude",
                               "heart_rate", "cadence", "speed",
                               "distance", "power", "temperature")
                  if f in fit_data.available_fields]

    result = {"total_records": len(records), "outliers": {}}
    any_bad = set()

    for field in fields:
        values = []
        indices = []
        for i, r in enumerate(records):
            v = getattr(r, field, None)
            if v is not None:
                values.append(v)
                indices.append(i)

        if len(values) < 3:
            continue

        mean = sum(values) / len(values)
        std = math.sqrt(sum((v - mean) ** 2 for v in values) / len(values))
        lo, hi = FIELD_RANGES.get(field, (None, None))

        range_bad = set()
        zscore_bad = set()

        for j, (v, idx) in enumerate(zip(values, indices)):
            is_bad = False
            if lo is not None and (v < lo or v > hi):
                range_bad.add(idx)
                is_bad = True
            if std > 1e-9 and abs(v - mean) > sigma * std:
                zscore_bad.add(idx)
                is_bad = True
            if is_bad:
                any_bad.add(idx)

        all_bad = sorted(range_bad | zscore_bad)
        result["outliers"][field] = {
            "count": len(all_bad),
            "indices": all_bad,
            "range_outliers": len(range_bad),
            "zscore_outliers": len(zscore_bad),
            "mean": round(mean, 4),
            "std": round(std, 4),
        }

    # GPS Glitch 检测（使用缓存）
    gps_glitch = fit_data.get_glitch_cache()
    result["gps_glitch"] = gps_glitch
    any_bad.update(gps_glitch["glitch_indices"])
    result["any_outlier_indices"] = sorted(any_bad)
    return result
