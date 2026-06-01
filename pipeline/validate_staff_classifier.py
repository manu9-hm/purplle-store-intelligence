import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Set


def parse_args():
    parser = argparse.ArgumentParser(
        description="Sample a video and print lightweight staff/customer classifications."
    )
    parser.add_argument("--video")
    parser.add_argument(
        "--events",
        help="Optional event JSONL path for validating stable is_staff labels by visitor_id.",
    )
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--conf", type=float, default=0.25)
    parser.add_argument("--sample-every-sec", type=float, default=5.0)
    parser.add_argument("--max-samples", type=int, default=20)
    parser.add_argument("--staff-dark-value-threshold", type=int, default=80)
    parser.add_argument("--staff-dark-ratio-threshold", type=float, default=0.55)
    parser.add_argument("--staff-confidence-threshold", type=float, default=0.6)
    return parser.parse_args()


def event_summary_rows(events_path: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = defaultdict(
        lambda: {
            "event_count": 0,
            "labels": set(),
            "staff_confidences": [],
        }
    )

    with Path(events_path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            visitor_id = event["visitor_id"]
            metadata = event.get("metadata", {})
            grouped[visitor_id]["event_count"] += 1
            grouped[visitor_id]["labels"].add(bool(event.get("is_staff", False)))
            if "staff_confidence" in metadata:
                grouped[visitor_id]["staff_confidences"].append(
                    float(metadata["staff_confidence"])
                )

    rows: List[Dict[str, Any]] = []
    for visitor_id, values in sorted(grouped.items()):
        labels: Set[bool] = values["labels"]
        confidences = values["staff_confidences"]
        average_confidence = (
            round(sum(confidences) / len(confidences), 4)
            if confidences
            else None
        )
        rows.append(
            {
                "visitor_id": visitor_id,
                "event_count": values["event_count"],
                "is_staff": next(iter(labels)) if len(labels) == 1 else "mixed",
                "average_staff_confidence": average_confidence,
            }
        )

    return rows


def detection_rows(args) -> List[Dict[str, Any]]:
    import cv2
    from generate_events import PERSON_CLASS_ID, classify_staff, foot_point
    from ultralytics import YOLO

    video_path = Path(args.video)
    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    sample_step = max(1, int(round(fps * args.sample_every_sec)))
    model = YOLO(args.model)
    rows: List[Dict[str, Any]] = []

    for sample_index, frame_index in enumerate(range(0, frame_count, sample_step)):
        if sample_index >= args.max_samples:
            break

        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame = cap.read()
        if not ok:
            continue

        result = model.predict(
            frame,
            classes=[PERSON_CLASS_ID],
            conf=args.conf,
            verbose=False,
        )[0]
        boxes = result.boxes
        if boxes is None:
            continue

        for detection_index, box in enumerate(boxes.xyxy.cpu().tolist()):
            staff = classify_staff(
                frame,
                box,
                dark_value_threshold=args.staff_dark_value_threshold,
                dark_ratio_threshold=args.staff_dark_ratio_threshold,
                confidence_threshold=args.staff_confidence_threshold,
            )
            rows.append(
                {
                    "time_sec": round(frame_index / fps, 2),
                    "detection_index": detection_index,
                    "classification": "staff" if staff["is_staff"] else "customer",
                    "staff_confidence": staff["staff_confidence"],
                    "staff_reason": staff["staff_reason"],
                    "upper_dark_ratio": staff["staff_upper_dark_ratio"],
                    "lower_dark_ratio": staff["staff_lower_dark_ratio"],
                    "foot_point": foot_point(box),
                    "bbox_xyxy": [round(value, 2) for value in box],
                }
            )

    cap.release()
    return rows


def main() -> None:
    args = parse_args()
    if args.events:
        rows = event_summary_rows(args.events)
        mixed_rows = [row for row in rows if row["is_staff"] == "mixed"]
        print(
            json.dumps(
                {
                    "events": args.events,
                    "visitor_ids": len(rows),
                    "mixed_label_visitor_ids": [
                        row["visitor_id"] for row in mixed_rows
                    ],
                    "mixed_label_visitor_count": len(mixed_rows),
                },
                indent=2,
            )
        )
        for row in rows:
            print(json.dumps(row))
        return

    if not args.video:
        raise RuntimeError("Provide --video for sampling or --events for event validation.")

    rows = detection_rows(args)
    staff_count = sum(1 for row in rows if row["classification"] == "staff")
    customer_count = len(rows) - staff_count

    print(
        json.dumps(
            {
                "video": args.video,
                "detections": len(rows),
                "staff_candidates": staff_count,
                "customer_candidates": customer_count,
            },
            indent=2,
        )
    )
    for row in rows:
        print(json.dumps(row))


if __name__ == "__main__":
    main()
