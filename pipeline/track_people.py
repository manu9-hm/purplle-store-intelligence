import argparse

import cv2
from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(
        description="Visualize YOLOv8 person detection with ByteTrack IDs."
    )
    parser.add_argument(
        "--video",
        default="data/videos/cam3.mp4",
        help="Path to the input video.",
    )
    parser.add_argument(
        "--model",
        default="yolov8n.pt",
        help="Path/name of the YOLOv8 model.",
    )
    parser.add_argument(
        "--tracker",
        default="bytetrack.yaml",
        help="Ultralytics tracker config.",
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.25,
        help="Minimum detection confidence.",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    model = YOLO(args.model)
    cap = cv2.VideoCapture(args.video)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {args.video}")

    while True:
        ok, frame = cap.read()
        if not ok:
            break

        results = model.track(
            frame,
            persist=True,
            classes=[0],
            conf=args.conf,
            tracker=args.tracker,
            verbose=False,
        )

        annotated = results[0].plot()
        cv2.imshow("Person Tracking", annotated)

        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
