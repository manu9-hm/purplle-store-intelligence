import argparse
import json
from pathlib import Path

import cv2


WINDOW_NAME = "Zone Calibrator"


class ZoneCalibrator:
    def __init__(
        self,
        *,
        frame,
        output_path: Path,
        camera_id: str,
        zone_id: str,
        zone_type: str,
    ) -> None:
        self.frame = frame
        self.output_path = output_path
        self.camera_id = camera_id
        self.zone_id = zone_id
        self.zone_type = zone_type
        self.points: list[tuple[int, int]] = []
        self.mouse_pos: tuple[int, int] = (0, 0)

    def mouse_callback(self, event, x, y, _flags, _param) -> None:
        self.mouse_pos = (x, y)
        if event == cv2.EVENT_LBUTTONDOWN:
            self.points.append((x, y))

    def draw(self):
        canvas = self.frame.copy()

        for index, point in enumerate(self.points):
            cv2.circle(canvas, point, 5, (0, 255, 255), -1)
            cv2.putText(
                canvas,
                str(index + 1),
                (point[0] + 8, point[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 255, 255),
                2,
                cv2.LINE_AA,
            )

        if len(self.points) >= 2:
            for start, end in zip(self.points, self.points[1:]):
                cv2.line(canvas, start, end, (0, 255, 255), 2)

        if len(self.points) >= 3:
            cv2.line(canvas, self.points[-1], self.points[0], (0, 180, 255), 2)

        x, y = self.mouse_pos
        help_lines = [
            f"camera={self.camera_id} zone={self.zone_id} type={self.zone_type}",
            f"mouse=({x}, {y}) points={len(self.points)}",
            "left click: add point | u: undo | r: reset | s: save | q/esc: quit",
        ]

        y_offset = 24
        for line in help_lines:
            cv2.putText(
                canvas,
                line,
                (12, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (20, 20, 20),
                4,
                cv2.LINE_AA,
            )
            cv2.putText(
                canvas,
                line,
                (12, y_offset),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
                cv2.LINE_AA,
            )
            y_offset += 26

        return canvas

    def save(self) -> None:
        if len(self.points) < 3:
            raise ValueError("A zone polygon needs at least 3 points.")

        payload = {"camera_id": self.camera_id, "zones": []}
        if self.output_path.exists():
            with self.output_path.open("r", encoding="utf-8") as handle:
                existing_payload = json.load(handle)
            if isinstance(existing_payload, dict):
                payload.update(existing_payload)

        zones = payload.get("zones", [])
        zones = [
            zone
            for zone in zones
            if zone.get("zone_id") != self.zone_id
        ]
        zones.append(
            {
                "zone_id": self.zone_id,
                "zone_type": self.zone_type,
                "polygon": [[x, y] for x, y in self.points],
            }
        )

        payload["camera_id"] = self.camera_id
        payload["zones"] = zones

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)
            handle.write("\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Click polygon points on a video frame and save a zone JSON config."
    )
    parser.add_argument("--video", required=True, help="Path to the camera video.")
    parser.add_argument("--output", required=True, help="Zone JSON output path.")
    parser.add_argument("--camera-id", required=True, help="Camera ID, for example CAM3.")
    parser.add_argument("--zone-id", required=True, help="Zone ID, for example entrance_zone.")
    parser.add_argument(
        "--zone-type",
        default="browsing",
        help="Zone type used by generate_events.py, for example entrance, browsing, billing, or storage.",
    )
    parser.add_argument(
        "--frame",
        type=int,
        default=0,
        help="Zero-based frame number to calibrate from.",
    )
    return parser.parse_args()


def read_frame(video_path: str, frame_number: int):
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    if frame_number > 0:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_number)

    ok, frame = cap.read()
    cap.release()

    if not ok:
        raise RuntimeError(f"Could not read frame {frame_number} from video: {video_path}")

    return frame


def main() -> None:
    args = parse_args()
    frame = read_frame(args.video, args.frame)
    calibrator = ZoneCalibrator(
        frame=frame,
        output_path=Path(args.output),
        camera_id=args.camera_id,
        zone_id=args.zone_id,
        zone_type=args.zone_type,
    )

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(WINDOW_NAME, calibrator.mouse_callback)

    while True:
        cv2.imshow(WINDOW_NAME, calibrator.draw())
        key = cv2.waitKey(20) & 0xFF

        if key in (ord("q"), 27):
            break
        if key == ord("u") and calibrator.points:
            calibrator.points.pop()
        if key == ord("r"):
            calibrator.points.clear()
        if key == ord("s"):
            try:
                calibrator.save()
                print(f"Saved zone config: {calibrator.output_path}")
                break
            except ValueError as error:
                print(error)

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
