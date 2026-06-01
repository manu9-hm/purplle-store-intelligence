import argparse
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import cv2
import numpy as np
from ultralytics import YOLO


PERSON_CLASS_ID = 0
DEFAULT_DWELL_THRESHOLD_MS = 3000
DEFAULT_ZONE_TRANSITION_CONFIRM_MS = 500
DEFAULT_TRACK_STALE_MS = 1500
DEFAULT_ENTRANCE_CROSSING_COOLDOWN_MS = 1000
DEFAULT_STAFF_DARK_VALUE_THRESHOLD = 80
DEFAULT_STAFF_DARK_RATIO_THRESHOLD = 0.55
DEFAULT_STAFF_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_STAFF_STABLE_OBSERVATIONS = 3


@dataclass(frozen=True)
class Zone:
    zone_id: str
    polygon: List[Tuple[int, int]]
    zone_type: str = "browsing"


@dataclass
class VisitorState:
    visitor_id: str
    is_staff: bool = False
    staff_confidence: float = 0.0
    staff_reason: str = "not_classified"
    staff_confidence_values: List[float] = field(default_factory=list)
    staff_vote_count: int = 0
    staff_observations: int = 0
    staff_classification_locked: bool = False
    active_zone_id: Optional[str] = None
    zone_entered_at_ms: Optional[int] = None
    pending_zone_id: Optional[str] = None
    pending_zone_started_at_ms: Optional[int] = None
    dwell_emitted_zone_ids: Set[str] = field(default_factory=set)
    exited_at_ms: Optional[int] = None
    store_presence: Optional[bool] = None
    last_foot_point: Optional[Tuple[int, int]] = None
    last_footfall_event_at_ms: Optional[int] = None
    last_seen_at_ms: int = 0


@dataclass
class Event:
    event_id: str
    store_id: str
    camera_id: str
    visitor_id: str
    event_type: str
    timestamp: str
    zone_id: Optional[str]
    dwell_ms: Optional[int]
    is_staff: bool
    confidence: float
    metadata: Dict[str, Any]


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate structured visitor events from YOLOv8 + ByteTrack output."
    )
    parser.add_argument("--video", default="data/videos/cam3.mp4")
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--tracker", default="bytetrack.yaml")
    parser.add_argument("--store-id", default="purplle_store_001")
    parser.add_argument("--camera-id", default="CAM3")
    parser.add_argument(
        "--zones",
        help="Optional JSON file with zone definitions.",
    )
    parser.add_argument(
        "--output",
        default="data/events/events.jsonl",
        help="JSONL output path.",
    )
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument(
        "--dwell-threshold-ms",
        type=int,
        default=DEFAULT_DWELL_THRESHOLD_MS,
    )
    parser.add_argument(
        "--zone-transition-confirm-ms",
        type=int,
        default=DEFAULT_ZONE_TRANSITION_CONFIRM_MS,
        help="Minimum time a zone change must persist before emitting enter/exit.",
    )
    parser.add_argument(
        "--track-stale-ms",
        type=int,
        default=DEFAULT_TRACK_STALE_MS,
        help="Time after last observation before a track is considered stale.",
    )
    parser.add_argument(
        "--emit-track-loss-exits",
        action="store_true",
        help="Emit EXIT events for stale/lost tracks. Disabled by default to reduce noise.",
    )
    parser.add_argument(
        "--entry-direction",
        choices=["down", "up"],
        default="down",
        help="Direction through an entrance zone that counts as ENTRY in image coordinates.",
    )
    parser.add_argument(
        "--entrance-crossing-cooldown-ms",
        type=int,
        default=DEFAULT_ENTRANCE_CROSSING_COOLDOWN_MS,
        help="Minimum time between ENTRY/EXIT/REENTRY events for one track.",
    )
    parser.add_argument(
        "--staff-dark-value-threshold",
        type=int,
        default=DEFAULT_STAFF_DARK_VALUE_THRESHOLD,
        help="HSV value threshold for dark clothing detection.",
    )
    parser.add_argument(
        "--staff-dark-ratio-threshold",
        type=float,
        default=DEFAULT_STAFF_DARK_RATIO_THRESHOLD,
        help="Minimum dark-pixel ratio in both upper and lower clothing regions.",
    )
    parser.add_argument(
        "--staff-confidence-threshold",
        type=float,
        default=DEFAULT_STAFF_CONFIDENCE_THRESHOLD,
        help="Minimum staff confidence for is_staff=True.",
    )
    parser.add_argument(
        "--staff-stable-observations",
        type=int,
        default=DEFAULT_STAFF_STABLE_OBSERVATIONS,
        help="Observations required before locking a track's staff/customer label.",
    )
    parser.add_argument(
        "--exclude-staff-events",
        action="store_true",
        help="Skip emitting events for tracks classified as staff.",
    )
    return parser.parse_args()


def load_zones(path: Optional[str]) -> List[Zone]:
    if not path:
        return []

    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)

    zones = payload.get("zones", payload)
    return [
        Zone(
            zone_id=item["zone_id"],
            polygon=[tuple(point) for point in item["polygon"]],
            zone_type=item.get("zone_type", "browsing"),
        )
        for item in zones
    ]


def timestamp_from_video(start_epoch_s: float, frame_ms: int) -> str:
    event_time = datetime.fromtimestamp(
        start_epoch_s + frame_ms / 1000,
        tz=timezone.utc,
    )
    return event_time.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def foot_point(box_xyxy: List[float]) -> Tuple[int, int]:
    x1, _, x2, y2 = box_xyxy
    return int((x1 + x2) / 2), int(y2)


def clamp_box(
    box_xyxy: List[float],
    frame_shape: Tuple[int, int, int],
) -> Tuple[int, int, int, int]:
    height, width = frame_shape[:2]
    x1, y1, x2, y2 = box_xyxy
    return (
        max(0, min(width - 1, int(x1))),
        max(0, min(height - 1, int(y1))),
        max(0, min(width, int(x2))),
        max(0, min(height, int(y2))),
    )


def body_region(
    frame,
    box_xyxy: List[float],
    *,
    y_start_ratio: float,
    y_end_ratio: float,
) -> Optional[Any]:
    x1, y1, x2, y2 = clamp_box(box_xyxy, frame.shape)
    box_width = x2 - x1
    box_height = y2 - y1
    if box_width <= 2 or box_height <= 2:
        return None

    inner_x1 = x1 + int(box_width * 0.18)
    inner_x2 = x2 - int(box_width * 0.18)
    region_y1 = y1 + int(box_height * y_start_ratio)
    region_y2 = y1 + int(box_height * y_end_ratio)
    if inner_x2 <= inner_x1 or region_y2 <= region_y1:
        return None

    return frame[region_y1:region_y2, inner_x1:inner_x2]


def color_stats(region) -> Dict[str, Any]:
    if region is None or region.size == 0:
        return {
            "dark_ratio": 0.0,
            "median_bgr": [0, 0, 0],
            "median_value": 255,
        }

    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    value = hsv[:, :, 2]
    median_bgr = np.median(region.reshape(-1, 3), axis=0)
    return {
        "dark_ratio": 0.0,
        "median_bgr": [int(channel) for channel in median_bgr],
        "median_value": int(np.median(value)),
    }


def classify_staff(
    frame,
    box_xyxy: List[float],
    *,
    dark_value_threshold: int,
    dark_ratio_threshold: float,
    confidence_threshold: float,
) -> Dict[str, Any]:
    upper = body_region(
        frame,
        box_xyxy,
        y_start_ratio=0.18,
        y_end_ratio=0.48,
    )
    lower = body_region(
        frame,
        box_xyxy,
        y_start_ratio=0.52,
        y_end_ratio=0.92,
    )

    upper_stats = color_stats(upper)
    lower_stats = color_stats(lower)

    if upper is not None and upper.size:
        upper_value = cv2.cvtColor(upper, cv2.COLOR_BGR2HSV)[:, :, 2]
        upper_stats["dark_ratio"] = float(
            np.mean(upper_value <= dark_value_threshold)
        )
    if lower is not None and lower.size:
        lower_value = cv2.cvtColor(lower, cv2.COLOR_BGR2HSV)[:, :, 2]
        lower_stats["dark_ratio"] = float(
            np.mean(lower_value <= dark_value_threshold)
        )

    upper_dark = upper_stats["dark_ratio"] >= dark_ratio_threshold
    lower_dark = lower_stats["dark_ratio"] >= dark_ratio_threshold
    confidence = (upper_stats["dark_ratio"] + lower_stats["dark_ratio"]) / 2
    is_staff = upper_dark and lower_dark and confidence >= confidence_threshold

    if is_staff:
        reason = "upper_and_lower_clothing_predominantly_dark"
    elif upper_dark and lower_dark:
        reason = "staff_confidence_below_threshold"
    elif not upper_dark and not lower_dark:
        reason = "upper_and_lower_not_dark_enough"
    elif not upper_dark:
        reason = "upper_not_dark_enough"
    else:
        reason = "lower_not_dark_enough"

    return {
        "is_staff": bool(is_staff),
        "staff_confidence": round(float(confidence), 4),
        "staff_reason": reason,
        "staff_upper_dark_ratio": round(float(upper_stats["dark_ratio"]), 4),
        "staff_lower_dark_ratio": round(float(lower_stats["dark_ratio"]), 4),
        "staff_upper_median_bgr": upper_stats["median_bgr"],
        "staff_lower_median_bgr": lower_stats["median_bgr"],
    }


def update_staff_classification(
    *,
    state: VisitorState,
    staff_metadata: Dict[str, Any],
    stable_observations: int,
) -> None:
    state.staff_confidence_values.append(staff_metadata["staff_confidence"])
    state.staff_observations += 1
    if staff_metadata["is_staff"]:
        state.staff_vote_count += 1

    if state.staff_classification_locked:
        state.staff_confidence = round(
            sum(state.staff_confidence_values) / state.staff_observations,
            4,
        )
        return

    required_observations = max(1, stable_observations)
    if state.staff_observations < required_observations:
        state.staff_confidence = round(
            sum(state.staff_confidence_values) / state.staff_observations,
            4,
        )
        state.staff_reason = "collecting_staff_observations"
        return

    state.is_staff = state.staff_vote_count > (state.staff_observations / 2)
    state.staff_confidence = round(
        sum(state.staff_confidence_values) / state.staff_observations,
        4,
    )
    state.staff_reason = (
        "stable_staff_majority_vote"
        if state.is_staff
        else "stable_customer_majority_vote"
    )
    state.staff_classification_locked = True


def stable_staff_metadata(
    *,
    state: VisitorState,
    raw_staff_metadata: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        **raw_staff_metadata,
        "is_staff": state.is_staff,
        "staff_confidence": state.staff_confidence,
        "staff_reason": state.staff_reason,
        "staff_observations": state.staff_observations,
    }


def zone_for_point(point: Tuple[int, int], zones: List[Zone]) -> Optional[Zone]:
    for zone in zones:
        polygon = np.array(zone.polygon, dtype=np.int32)
        if cv2.pointPolygonTest(polygon, point, False) >= 0:
            return zone
    return None


def point_in_zone(point: Tuple[int, int], zone: Zone) -> bool:
    polygon = np.array(zone.polygon, dtype=np.int32)
    return cv2.pointPolygonTest(polygon, point, False) >= 0


def entrance_threshold_y(zone: Zone) -> float:
    return sum(point[1] for point in zone.polygon) / len(zone.polygon)


def crossing_event_type(
    *,
    previous_point: Tuple[int, int],
    current_point: Tuple[int, int],
    zone: Zone,
    entry_direction: str,
) -> Optional[str]:
    threshold_y = entrance_threshold_y(zone)
    previous_y = previous_point[1]
    current_y = current_point[1]

    if previous_y < threshold_y <= current_y:
        movement = "down"
    elif previous_y > threshold_y >= current_y:
        movement = "up"
    else:
        return None

    if movement == entry_direction:
        return "ENTRY"
    return "EXIT"


def queue_depth_for_zone(
    states: Dict[int, VisitorState],
    zone_id: str,
) -> int:
    return queue_composition_for_zone(states, zone_id)["customer_count"]


def queue_composition_for_zone(
    states: Dict[int, VisitorState],
    zone_id: str,
) -> Dict[str, int]:
    customer_count = 0
    staff_count = 0
    for state in states.values():
        if state.active_zone_id != zone_id:
            continue
        if state.is_staff:
            staff_count += 1
        else:
            customer_count += 1

    return {
        "queue_depth": customer_count,
        "customer_count": customer_count,
        "staff_count": staff_count,
    }


def build_event(
    *,
    event_type: str,
    store_id: str,
    camera_id: str,
    visitor_id: str,
    timestamp: str,
    zone_id: Optional[str],
    dwell_ms: Optional[int],
    confidence: float,
    metadata: Dict[str, Any],
    is_staff: Optional[bool] = None,
) -> Event:
    event_is_staff = bool(metadata.get("is_staff", False) if is_staff is None else is_staff)
    return Event(
        event_id=str(uuid.uuid4()),
        store_id=store_id,
        camera_id=camera_id,
        visitor_id=visitor_id,
        event_type=event_type,
        timestamp=timestamp,
        zone_id=zone_id,
        dwell_ms=dwell_ms,
        is_staff=event_is_staff,
        confidence=round(confidence, 4),
        metadata=metadata,
    )


def emit_zone_transition_events(
    *,
    state: VisitorState,
    current_zone: Optional[Zone],
    frame_ms: int,
    timestamp: str,
    store_id: str,
    camera_id: str,
    confidence: float,
    metadata: Dict[str, Any],
    confirm_ms: int,
) -> List[Event]:
    events: List[Event] = []
    current_zone_id = current_zone.zone_id if current_zone else None

    if current_zone_id == state.active_zone_id:
        state.pending_zone_id = None
        state.pending_zone_started_at_ms = None
        if (
            current_zone_id
            and state.zone_entered_at_ms is not None
            and current_zone_id not in state.dwell_emitted_zone_ids
        ):
            dwell_ms = frame_ms - state.zone_entered_at_ms
            if dwell_ms >= metadata["dwell_threshold_ms"]:
                events.append(
                    build_event(
                        event_type="ZONE_DWELL",
                        store_id=store_id,
                        camera_id=camera_id,
                        visitor_id=state.visitor_id,
                        timestamp=timestamp,
                        zone_id=current_zone_id,
                        dwell_ms=dwell_ms,
                        confidence=confidence,
                        metadata={**metadata, "zone_transition_confirm_ms": confirm_ms},
                    )
                )
                state.dwell_emitted_zone_ids.add(current_zone_id)
        return events

    if current_zone_id != state.pending_zone_id:
        state.pending_zone_id = current_zone_id
        state.pending_zone_started_at_ms = frame_ms
        return events

    if state.pending_zone_started_at_ms is None:
        state.pending_zone_started_at_ms = frame_ms
        return events

    if frame_ms - state.pending_zone_started_at_ms < confirm_ms:
        return events

    if state.active_zone_id is not None:
        dwell_ms = None
        if state.zone_entered_at_ms is not None:
            dwell_ms = frame_ms - state.zone_entered_at_ms
        events.append(
            build_event(
                event_type="ZONE_EXIT",
                store_id=store_id,
                camera_id=camera_id,
                visitor_id=state.visitor_id,
                timestamp=timestamp,
                zone_id=state.active_zone_id,
                dwell_ms=dwell_ms,
                confidence=confidence,
                metadata={**metadata, "zone_transition_confirm_ms": confirm_ms},
            )
        )

    state.active_zone_id = current_zone_id
    state.zone_entered_at_ms = (
        state.pending_zone_started_at_ms if current_zone_id else None
    )
    state.pending_zone_id = None
    state.pending_zone_started_at_ms = None

    if current_zone_id is not None:
        state.dwell_emitted_zone_ids.discard(current_zone_id)
        events.append(
            build_event(
                event_type="ZONE_ENTER",
                store_id=store_id,
                camera_id=camera_id,
                visitor_id=state.visitor_id,
                timestamp=timestamp,
                zone_id=current_zone_id,
                dwell_ms=None,
                confidence=confidence,
                metadata={**metadata, "zone_transition_confirm_ms": confirm_ms},
            )
        )

    return events


def emit_entry_exit_events(
    *,
    state: VisitorState,
    entrance_zones: List[Zone],
    previous_point: Optional[Tuple[int, int]],
    current_point: Tuple[int, int],
    frame_ms: int,
    timestamp: str,
    store_id: str,
    camera_id: str,
    confidence: float,
    metadata: Dict[str, Any],
    entry_direction: str,
    cooldown_ms: int,
) -> List[Event]:
    if previous_point is None:
        return []

    if (
        state.last_footfall_event_at_ms is not None
        and frame_ms - state.last_footfall_event_at_ms < cooldown_ms
    ):
        return []

    for zone in entrance_zones:
        if not point_in_zone(previous_point, zone) and not point_in_zone(current_point, zone):
            continue

        event_type = crossing_event_type(
            previous_point=previous_point,
            current_point=current_point,
            zone=zone,
            entry_direction=entry_direction,
        )
        if event_type is None:
            continue

        if event_type == "ENTRY":
            if state.store_presence is True:
                return []
            event_type = "REENTRY" if state.exited_at_ms is not None else "ENTRY"
            state.exited_at_ms = None
            state.store_presence = True
        else:
            if state.store_presence is False:
                return []
            state.exited_at_ms = frame_ms
            state.store_presence = False

        state.last_footfall_event_at_ms = frame_ms
        event_metadata = {
            **metadata,
            "entry_direction": entry_direction,
            "entrance_threshold_y": round(entrance_threshold_y(zone), 2),
            "previous_foot_point": previous_point,
        }

        return [
            build_event(
                event_type=event_type,
                store_id=store_id,
                camera_id=camera_id,
                visitor_id=state.visitor_id,
                timestamp=timestamp,
                zone_id=zone.zone_id,
                dwell_ms=None,
                confidence=confidence,
                metadata=event_metadata,
            )
        ]

    return []


def extract_tracks(result) -> List[Dict[str, Any]]:
    boxes = result.boxes
    if boxes is None or boxes.id is None:
        return []

    xyxy = boxes.xyxy.cpu().tolist()
    track_ids = boxes.id.cpu().tolist()
    confidences = boxes.conf.cpu().tolist()

    return [
        {
            "track_id": int(track_id),
            "box": box,
            "confidence": float(confidence),
            "foot_point": foot_point(box),
        }
        for track_id, box, confidence in zip(track_ids, xyxy, confidences)
    ]


def write_events(output_path: Path, events: List[Event]) -> None:
    if not events:
        return

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")


def emit_billing_queue_join_events(
    *,
    zone_events: List[Event],
    states: Dict[int, VisitorState],
    billing_zone_ids: Set[str],
) -> List[Event]:
    events: List[Event] = []
    for event in zone_events:
        if event.event_type != "ZONE_ENTER" or event.zone_id not in billing_zone_ids:
            continue

        if event.is_staff:
            continue

        queue_diagnostics = queue_composition_for_zone(states, event.zone_id)
        if queue_diagnostics["queue_depth"] <= 1:
            continue

        events.append(
            build_event(
                event_type="BILLING_QUEUE_JOIN",
                store_id=event.store_id,
                camera_id=event.camera_id,
                visitor_id=event.visitor_id,
                timestamp=event.timestamp,
                zone_id=event.zone_id,
                dwell_ms=None,
                confidence=event.confidence,
                metadata={**event.metadata, **queue_diagnostics},
            )
        )
    return events


def mark_missing_tracks_as_exit(
    *,
    states: Dict[int, VisitorState],
    visible_track_ids: Set[int],
    frame_ms: int,
    timestamp: str,
    store_id: str,
    camera_id: str,
    confidence: float,
    metadata: Dict[str, Any],
    stale_after_ms: int,
    emit_track_loss_exits: bool,
) -> List[Event]:
    events: List[Event] = []
    for track_id, state in states.items():
        if track_id in visible_track_ids or state.exited_at_ms is not None:
            continue

        if frame_ms - state.last_seen_at_ms < stale_after_ms:
            continue

        state.exited_at_ms = frame_ms
        state.store_presence = False
        if not emit_track_loss_exits:
            continue
        if not state.staff_classification_locked:
            continue

        events.append(
            build_event(
                event_type="EXIT",
                store_id=store_id,
                camera_id=camera_id,
                visitor_id=state.visitor_id,
                timestamp=timestamp,
                zone_id=state.active_zone_id,
                dwell_ms=None,
                confidence=confidence,
                metadata={
                    **metadata,
                    "reason": "track_lost",
                    "is_staff": state.is_staff,
                    "staff_confidence": state.staff_confidence,
                    "staff_reason": state.staff_reason,
                    "staff_observations": state.staff_observations,
                },
            )
        )
    return events


def generate_events(args) -> None:
    zones = load_zones(args.zones)
    entrance_zones = [zone for zone in zones if zone.zone_type == "entrance"]
    billing_zone_ids = {zone.zone_id for zone in zones if zone.zone_type == "billing"}
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("", encoding="utf-8")

    model = YOLO(args.model)
    cap = cv2.VideoCapture(args.video)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {args.video}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    start_epoch_s = time.time()
    states: Dict[int, VisitorState] = {}

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        frame_index = int(cap.get(cv2.CAP_PROP_POS_FRAMES))
        frame_ms = int(frame_index * 1000 / fps)
        timestamp = timestamp_from_video(start_epoch_s, frame_ms)

        results = model.track(
            frame,
            persist=True,
            classes=[PERSON_CLASS_ID],
            conf=args.conf,
            tracker=args.tracker,
            verbose=False,
        )

        frame_events: List[Event] = []
        tracks = extract_tracks(results[0])
        visible_track_ids: Set[int] = set()

        for track in tracks:
            track_id = track["track_id"]
            visible_track_ids.add(track_id)
            state = states.setdefault(
                track_id,
                VisitorState(visitor_id=f"{args.camera_id}-{track_id}"),
            )

            current_zone = zone_for_point(track["foot_point"], zones)
            staff_metadata = classify_staff(
                frame,
                track["box"],
                dark_value_threshold=args.staff_dark_value_threshold,
                dark_ratio_threshold=args.staff_dark_ratio_threshold,
                confidence_threshold=args.staff_confidence_threshold,
            )
            update_staff_classification(
                state=state,
                staff_metadata=staff_metadata,
                stable_observations=args.staff_stable_observations,
            )
            metadata_staff = stable_staff_metadata(
                state=state,
                raw_staff_metadata=staff_metadata,
            )
            metadata = {
                "track_id": track_id,
                "bbox_xyxy": [round(value, 2) for value in track["box"]],
                "foot_point": track["foot_point"],
                "zone_type": current_zone.zone_type if current_zone else None,
                "dwell_threshold_ms": args.dwell_threshold_ms,
                "staff_confidence": metadata_staff["staff_confidence"],
                "staff_reason": metadata_staff["staff_reason"],
                "staff_observations": metadata_staff["staff_observations"],
                "staff_upper_dark_ratio": metadata_staff["staff_upper_dark_ratio"],
                "staff_lower_dark_ratio": metadata_staff["staff_lower_dark_ratio"],
                "staff_upper_median_bgr": metadata_staff["staff_upper_median_bgr"],
                "staff_lower_median_bgr": metadata_staff["staff_lower_median_bgr"],
                "is_staff": metadata_staff["is_staff"],
            }

            if not state.staff_classification_locked:
                state.last_foot_point = track["foot_point"]
                state.last_seen_at_ms = frame_ms
                continue

            if args.exclude_staff_events and state.is_staff:
                state.last_foot_point = track["foot_point"]
                state.last_seen_at_ms = frame_ms
                continue

            frame_events.extend(
                emit_entry_exit_events(
                    state=state,
                    entrance_zones=entrance_zones,
                    previous_point=state.last_foot_point,
                    current_point=track["foot_point"],
                    frame_ms=frame_ms,
                    timestamp=timestamp,
                    store_id=args.store_id,
                    camera_id=args.camera_id,
                    confidence=track["confidence"],
                    metadata=metadata,
                    entry_direction=args.entry_direction,
                    cooldown_ms=args.entrance_crossing_cooldown_ms,
                )
            )
            zone_events = emit_zone_transition_events(
                state=state,
                current_zone=current_zone,
                frame_ms=frame_ms,
                timestamp=timestamp,
                store_id=args.store_id,
                camera_id=args.camera_id,
                confidence=track["confidence"],
                metadata=metadata,
                confirm_ms=args.zone_transition_confirm_ms,
            )
            frame_events.extend(zone_events)
            frame_events.extend(
                emit_billing_queue_join_events(
                    zone_events=zone_events,
                    states=states,
                    billing_zone_ids=billing_zone_ids,
                )
            )
            state.last_foot_point = track["foot_point"]
            state.last_seen_at_ms = frame_ms

        frame_events.extend(
            mark_missing_tracks_as_exit(
                states=states,
                visible_track_ids=visible_track_ids,
                frame_ms=frame_ms,
                timestamp=timestamp,
                store_id=args.store_id,
                camera_id=args.camera_id,
                confidence=0.0,
                metadata={"source": "tracker_state"},
                stale_after_ms=args.track_stale_ms,
                emit_track_loss_exits=args.emit_track_loss_exits,
            )
        )
        write_events(output_path, frame_events)

    cap.release()


def main():
    generate_events(parse_args())


if __name__ == "__main__":
    main()
