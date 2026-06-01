import cv2

cap = cv2.VideoCapture("data/videos/cam3.mp4")

print("Width =", cap.get(cv2.CAP_PROP_FRAME_WIDTH))
print("Height =", cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

cap.release()