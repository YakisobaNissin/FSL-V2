import math

import cv2
import mediapipe as mp
import numpy as np


class ClipError(ValueError):
    pass


def letter_features(landmarks, width, height, mirror=False):
    points = np.array([[p.x * width, p.y * height, p.z * width] for p in landmarks], dtype=np.float32)
    points -= points[0]
    if mirror:
        points[:, 0] *= -1
    scale = np.linalg.norm(points[9, :2])
    return (points / (scale if scale >= 1e-6 else 1.0)).flatten()


def frame_vector(result, width, height, settings):
    vector = np.zeros(settings["frame_width"], dtype=np.float32)
    center, span = None, 0.0
    if len(result.pose_landmarks) > 12:
        shoulders = [result.pose_landmarks[11], result.pose_landmarks[12]]
        points = np.array([[p.x * width, p.y * height] for p in shoulders], dtype=np.float32)
        if np.isfinite(points).all() and all((p.visibility or 0) >= settings["shoulder_visibility"] for p in shoulders):
            span = np.linalg.norm(points[1] - points[0])
            if span > 1e-6:
                center = points.mean(axis=0)
                vector[132] = 1
    for hand, offset in zip((result.left_hand_landmarks, result.right_hand_landmarks), (0, 66)):
        if len(hand) != 21:
            continue
        points = np.array([[p.x * width, p.y * height, p.z * width] for p in hand], dtype=np.float32)
        if not np.isfinite(points).all():
            continue
        relative = points - points[0]
        palm_size = np.linalg.norm(relative[9, :2])
        if palm_size <= 1e-6:
            continue
        vector[offset:offset + 63] = (relative / palm_size).ravel()
        if center is not None:
            vector[offset + 63:offset + 65] = (points[0, :2] - center) / span
        vector[offset + 65] = 1
    return vector


def clip_vector(frames, settings):
    frames = np.asarray(frames, dtype=np.float32)
    if frames.ndim != 2 or frames.shape[1] != settings["frame_width"] or not np.isfinite(frames).all():
        raise ClipError("No usable video frames.")
    active = np.flatnonzero(np.any(frames[:, [65, 131]], axis=1))
    if len(active) < settings["min_hand_frames"]:
        raise ClipError("Keep your hands and shoulders in view.")
    margin = settings["margin_frames"]
    window = frames[max(0, active[0] - margin):active[-1] + margin + 1]
    indices = np.linspace(0, len(window) - 1, settings["ordered_frames"]).round().astype(int)
    summary = np.stack([window.mean(axis=0), window.std(axis=0), window.min(axis=0), window.max(axis=0)])
    vector = np.concatenate([window[indices].ravel(), summary.ravel()])
    if vector.shape != (settings["feature_size"],) or not np.isfinite(vector).all():
        raise ClipError("Invalid clip features.")
    return vector


def has_sign_motion(features, settings):
    count = settings["ordered_frames"]
    width = settings["frame_width"]
    frames = features[:count * width].reshape(count, width)
    for offset in (0, 66):
        visible = frames[:, offset + 65] > 0.5
        hand = frames[visible, offset:offset + 63].reshape(-1, 21, 3)
        if len(hand) >= 3:
            low, high = np.percentile(hand[:, :, :2], [10, 90], axis=0)
            if np.sqrt(np.mean((high - low) ** 2)) >= 0.10:
                return True
        shoulders = frames[:, 132] > 0.5
        wrist = frames[visible & shoulders, offset + 63:offset + 65]
        if len(wrist) >= 3:
            low, high = np.percentile(wrist, [10, 90], axis=0)
            if np.linalg.norm(high - low) >= 0.10:
                return True
    return False


def rgb_image(frame):
    return mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))


def sign_features(sampled_frames, model_path, settings):
    options = mp.tasks.vision.HolisticLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
    )
    vectors, last_ms = [], -1
    with mp.tasks.vision.HolisticLandmarker.create_from_options(options) as detector:
        for elapsed, frame in sampled_frames:
            last_ms = max(last_ms + 1, round(elapsed * 1000))
            result = detector.detect_for_video(rgb_image(frame), last_ms)
            vectors.append(frame_vector(result, frame.shape[1], frame.shape[0], settings))
    return clip_vector(vectors, settings)


def sampled_video(path, sample_fps):
    cap = cv2.VideoCapture(str(path))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not cap.isOpened() or not math.isfinite(fps) or fps <= 0:
            raise ClipError("Video cannot be opened or has no valid frame rate.")
        index, next_sample = -1, 0.0
        period = 1 / sample_fps
        while cap.grab():
            index += 1
            elapsed = index / fps
            if elapsed + 1e-9 < next_sample:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                raise ClipError("A sampled video frame could not be decoded.")
            next_sample = (math.floor((elapsed + 1e-9) / period) + 1) * period
            yield elapsed, frame
    finally:
        cap.release()
