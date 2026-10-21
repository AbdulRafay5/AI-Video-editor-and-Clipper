"""Face detection and a calm 'follow the people' camera path for vertical crops."""
import logging
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

import config
from . import video

log = logging.getLogger("clipper.tracking")

MODEL_URL = ("https://media.githubusercontent.com/media/opencv/opencv_zoo/"
             "47534e27c9851bb1128ccc0102f1145e27f23f98/models/face_detection_yunet/"
             "face_detection_yunet_2023mar.onnx")
MODEL_PATH = config.MODEL_DIR / "face_detection_yunet_2023mar.onnx"

SAMPLE_FPS = 4              # frames analysed per second
SAMPLE_WIDTH = 640          # analysis frames are scaled to this width
SCORE_THRESHOLD = 0.7       # minimum face confidence
MIN_FACE_FRACTION = 0.035   # ignore faces narrower than 3.5% of the frame width
GROUP_FIT = 0.92            # group is "fitting" if it uses under 92% of the crop window
DEAD_ZONE = 0.15            # ignore target moves smaller than 15% of the crop width
MIN_HOLD = 1.2              # seconds the camera must stay put between moves
MOVE_SECONDS = 0.7          # how long a camera move takes
MIN_FACE_COVERAGE = 0.10    # need faces in at least 10% of samples to trust the tracking


def _cv2():
    try:
        import cv2
    except ImportError:
        raise video.VideoError("OpenCV isn't installed. Run: pip install -r requirements.txt")
    return cv2


def ensure_model():
    """Download the small YuNet face model the first time it's needed."""
    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 50_000:
        return MODEL_PATH
    tmp = MODEL_PATH.with_suffix(".part")
    log.info("Downloading face detection model...")
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=60) as resp, open(tmp, "wb") as f:
            shutil.copyfileobj(resp, f)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        tmp.unlink(missing_ok=True)
        raise video.VideoError(
            f"Could not download the face detection model ({e}). Download it manually from "
            f"{MODEL_URL} and save it as {MODEL_PATH}")
    if tmp.stat().st_size < 50_000:
        tmp.unlink(missing_ok=True)
        raise video.VideoError("The downloaded face model looks wrong (too small). Try again.")
    tmp.replace(MODEL_PATH)
    return MODEL_PATH


def _even(n):
    return n - (n % 2)


def _dims(src_w, src_h):
    return SAMPLE_WIDTH, max(2, int(round(SAMPLE_WIDTH * src_h / src_w / 2)) * 2)


def _sample_frames(src, start, duration, src_w, src_h, cancel):
    """Yield (time, frame) pairs: low-resolution frames piped out of FFmpeg, so any format
    FFmpeg can read works and rotation is handled for us."""
    import numpy as np
    width, height = _dims(src_w, src_h)
    cmd = [video._tool("ffmpeg"), "-hide_banner", "-loglevel", "error",
           "-ss", f"{start:.3f}", "-i", str(Path(src).resolve()), "-t", f"{duration:.3f}",
           "-an", "-vf", f"fps={SAMPLE_FPS},scale={width}:{height}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            creationflags=video.NO_WINDOW)
    size = width * height * 3
    i = 0
    try:
        while True:
            if cancel.is_set():
                raise video.Cancelled()
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield i / SAMPLE_FPS, np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            i += 1
    finally:
        proc.kill()
        proc.stdout.close()
        proc.wait()


def _frame_target(faces, width, cwf):
    """Where (0-1 across the frame) the crop window should be centred for one frame."""
    if faces is None or len(faces) == 0:
        return None
    boxes = []
    for f in faces:
        x, w, h = float(f[0]), float(f[2]), float(f[3])
        if w < MIN_FACE_FRACTION * width:
            continue
        boxes.append((x / width, (x + w) / width, w * h))
    if not boxes:
        return None
    left, right = min(b[0] for b in boxes), max(b[1] for b in boxes)
    if right - left <= cwf * GROUP_FIT:          # everyone fits: centre on the group
        return (left + right) / 2
    best = max(boxes, key=lambda b: b[2])        # too spread out: follow the largest face
    return (best[0] + best[1]) / 2


def _smooth_path(times, centers, duration, cwf):
    """Turn noisy per-frame targets into a few slow, deliberate camera moves.
    Returns ([(seconds, centre), ...], number_of_moves)."""
    lo, hi = cwf / 2, 1 - cwf / 2
    last = next(c for c in centers if c is not None)
    filled = []
    for c in centers:                            # hold the last position when no face is seen
        if c is not None:
            last = c
        filled.append(last)
    smooth = []
    for i in range(len(filled)):                 # median filter removes one-frame glitches
        window = sorted(filled[max(0, i - 2): i + 3])
        smooth.append(min(max(window[len(window) // 2], lo), hi))

    cur = smooth[0]
    points = [(0.0, cur)]
    last_end, moves = -MIN_HOLD, 0
    for t, c in zip(times, smooth):
        if abs(c - cur) > DEAD_ZONE * cwf and t >= last_end + MIN_HOLD:
            begin = max(t, points[-1][0] + 0.01)
            points.append((begin, cur))
            points.append((begin + MOVE_SECONDS, c))
            cur, last_end, moves = c, begin + MOVE_SECONDS, moves + 1
    if duration > points[-1][0]:
        points.append((duration, cur))
    return points, moves


def plan_follow(src, start, end, src_w, src_h, cancel):
    """Work out a camera path for [start, end]. Returns {'points', 'crop_w', 'summary'},
    or None when too few faces were found to trust. Raises VideoError for setup problems."""
    target = config.VERTICAL_WIDTH / config.VERTICAL_HEIGHT
    if src_w / src_h <= target:
        raise video.VideoError("This video is already as narrow as a vertical clip, so there is nothing to follow")
    cv2 = _cv2()
    model = ensure_model()
    width, height = _dims(src_w, src_h)
    detector = cv2.FaceDetectorYN.create(str(model), "", (width, height), SCORE_THRESHOLD, 0.3, 200)

    cw = _even(int(src_h * target))
    cwf = cw / src_w
    duration = max(end - start, 0.1)

    times, centers, seen = [], [], 0
    for t, frame in _sample_frames(src, start, duration, src_w, src_h, cancel):
        _, faces = detector.detect(frame)
        c = _frame_target(faces, width, cwf)
        times.append(t)
        centers.append(c)
        seen += c is not None
    if not times or seen / len(times) < MIN_FACE_COVERAGE:
        return None

    path, moves = _smooth_path(times, centers, duration, cwf)
    points = []
    for t, c in path:
        x = int(round((c - cwf / 2) * src_w))
        x = _even(min(max(x, 0), src_w - cw))
        points.append((round(t, 3), x))
    summary = f"faces seen in {round(100 * seen / len(times))}% of samples, {moves} camera move(s)"
    log.info("Follow plan: %s", summary)
    return {"points": points, "crop_w": cw, "summary": summary}