import atexit
import hashlib
import json
import logging
import math
import os
import subprocess
import webbrowser
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic

import cv2
import joblib
import mediapipe as mp
import numpy as np
import sklearn
from flask import Flask, Response, jsonify, render_template, request
from werkzeug.serving import make_server

from browser_features import ClipError, has_sign_motion, letter_features, rgb_image, sign_features
from launch_fsl import parse_args, venv_python

ROOT = Path(__file__).resolve().parent
RECORD_SECONDS = 4
PREPARE_SECONDS = 2
LETTER_HOLD_SECONDS = 0.45
LETTER_RELEASE_SECONDS = 0.25
SIGN_MIN_MARGIN = 0.5
LOG = logging.getLogger(__name__)


def signs_bundle(vocabulary=None):
    conversational = ROOT / "models" / "conversational"
    if vocabulary == "full":
        return ROOT / "export"
    if vocabulary == "conversational" or (vocabulary is None and conversational.is_dir()):
        return conversational
    if vocabulary is not None:
        raise ValueError("FSL_SIGNS_VOCAB must be 'conversational' or 'full'.")
    return ROOT / "export"


class LetterWorker:
    def __init__(self, python, bundle):
        self.process = subprocess.Popen(
            [str(python), "-u", str(ROOT / "letter_worker.py"), str(bundle)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding="utf-8",
        )
        self.responses = Queue()
        Thread(target=self.read, daemon=True).start()
        try:
            self.labels = self.reply(20)["labels"]
        except Exception:
            self.close()
            raise

    def read(self):
        try:
            for line in self.process.stdout:
                self.responses.put(json.loads(line))
        finally:
            self.responses.put({"error": "Fingerspelling worker exited. Restart the app."})

    def reply(self, seconds=5):
        try:
            reply = self.responses.get(timeout=seconds)
        except Empty as error:
            self.close()
            raise RuntimeError("Fingerspelling worker did not respond. Restart the app.") from error
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply

    def probabilities(self, features):
        self.process.stdin.write(json.dumps(features.tolist()) + "\n")
        self.process.stdin.flush()
        return self.reply()["probabilities"]

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


class Recognizers:
    def __init__(self, signs, letters, letter_python):
        if sklearn.__version__ != "1.7.2":
            raise RuntimeError("Signs requires scikit-learn 1.7.2. Start with Launch-FSL.ps1.")
        self.settings = json.loads((signs / "preprocessing.json").read_text(encoding="utf-8"))
        expected = {
            "feature_version": 1, "feature_size": 2128, "frame_width": 133,
            "ordered_frames": 12, "sample_fps": 15, "hand_order": ["left", "right"],
            "summary_order": ["mean", "std", "min", "max"],
        }
        if any(self.settings.get(key) != value for key, value in expected.items()):
            raise ValueError("Signs preprocessing settings do not match the exported SVM.")
        self.holistic = signs / "holistic_landmarker.task"
        with self.holistic.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != self.settings["model_sha256"]:
                raise ValueError("The Holistic Landmarker does not match the Signs export.")
        raw_labels = json.loads((signs / "labels.json").read_text(encoding="utf-8"))
        self.sign_labels = {int(key): value["label"] for key, value in raw_labels.items()}
        label_count = len(self.sign_labels)
        if label_count == 20:
            if {item["category"] for item in raw_labels.values()} != {"GREETING", "SURVIVAL"}:
                raise ValueError("The conversational label map must contain Greetings and Survival signs.")
            model_filename = "fsl_conversational_svm.joblib"
        elif label_count == 105:
            model_filename = "fsl_signs_svm.joblib"
        else:
            raise ValueError("Expected a 20-sign or 105-sign label map.")
        self.sign_model = joblib.load(signs / model_filename)
        class_ids = list(range(label_count))
        if self.sign_model.n_features_in_ != 2128 or self.sign_model.classes_.tolist() != class_ids or sorted(self.sign_labels) != class_ids:
            raise ValueError("Signs model and label map do not match.")
        if "TODAY" in self.sign_labels.values():
            svc = self.sign_model.named_steps["model"]
            self.sign_support = svc.support_vectors_
            self.sign_support_labels = np.repeat(svc.classes_, svc.n_support_)
        hand_asset = letters / "hand_landmarker.task"
        self.hand_options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(hand_asset)),
            running_mode=mp.tasks.vision.RunningMode.VIDEO, num_hands=1,
        )
        self.hand_detector = mp.tasks.vision.HandLandmarker.create_from_options(self.hand_options)
        self.last_timestamp = -1
        try:
            self.letters = LetterWorker(letter_python, letters)
        except Exception:
            self.hand_detector.close()
            raise

    def letter(self, elapsed, frame, mirror):
        self.last_timestamp = max(self.last_timestamp + 1, round(elapsed * 1000))
        result = self.hand_detector.detect_for_video(rgb_image(frame), self.last_timestamp)
        if not result.hand_landmarks:
            return None
        height, width = frame.shape[:2]
        features = letter_features(result.hand_landmarks[0], width, height, mirror)
        return self.letters.probabilities(features)

    def sign(self, frames):
        features = sign_features(frames, self.holistic, self.settings)
        if len(self.sign_labels) == 20 and not has_sign_motion(features, self.settings):
            raise ClipError("No clear signing movement in that recording.")
        return self.sign_label(features, include_margin=True)

    def sign_label(self, features, *, include_margin=False):
        batch = features[None, :]
        label_id = int(self.sign_model.predict(batch)[0])
        label = self.sign_labels[label_id]
        if label == "TODAY":
            scaled = self.sign_model.named_steps["scale"].transform(batch)[0]
            distances = np.sum((self.sign_support - scaled) ** 2, axis=1)
            nearest = self.sign_support_labels[np.argsort(distances, kind="stable")[:5]]
            if label_id not in nearest:
                raise ClipError("The sign wasn’t clear. Keep your hands and shoulders in view.")
        if include_margin:
            scores = self.sign_model.decision_function(batch)[0]
            runner_up = np.max(np.delete(scores, label_id))
            return label, float(scores[label_id] - runner_up)
        return label

    def close(self):
        self.hand_detector.close()
        self.letters.close()


class CameraSession:
    def __init__(self, models, camera_index=0):
        self.models = models
        self.camera_index = camera_index
        self.lock = Lock()
        self.worker = ThreadPoolExecutor(max_workers=1)
        self.camera = "off"
        self.mode = "letters"
        self.mirror = False
        self.generation = 0
        self.job = None
        self.history = deque(maxlen=12)
        self.events = deque(maxlen=128)
        self.event_id = 0
        self.reset()

    def reset(self):
        self.generation += 1
        self.detecting = False
        self.phase = "ready"
        self.label = self.confidence = None
        self.message = ""
        self.frames = []
        self.history.clear()
        self.next_letter = 0
        self.letter_candidate = None
        self.hand_missing_since = None
        self.last_added = None

    def append_prediction(self):
        self.last_added = self.label
        self.event_id += 1
        self.events.append(dict(id=self.event_id, mode=self.mode, label=self.label))

    def state(self):
        with self.lock:
            return dict(
                camera=self.camera, mode=self.mode, mirror=self.mirror,
                sign_count=len(self.models.sign_labels),
                detecting=self.detecting, event_id=self.event_id, events=list(self.events),
                phase=self.phase, label=self.label, confidence=self.confidence,
                generation=self.generation, worker_busy=self.job is not None, message=self.message,
                remaining=max(0, self.deadline - monotonic()) if self.phase in ("countdown", "recording") else None,
            )

    def action(self, action, payload):
        with self.lock:
            if action == "mode" and payload.get("mode") in ("letters", "signs"):
                self.mode = payload["mode"]
                self.reset()
            elif action == "mirror" and self.mode == "letters":
                self.mirror = not self.mirror
                self.reset()
            elif action == "stop":
                if self.camera in ("opening", "live"):
                    self.camera = "stopping"
                self.reset()
            elif action == "detection" and self.mode == "letters" and isinstance(payload.get("enabled"), bool):
                enabled = payload["enabled"]
                if enabled and self.camera != "live":
                    raise ValueError("Start the camera first.")
                if enabled != self.detecting:
                    self.reset()
                    self.detecting = enabled
            elif action == "record" and self.mode == "signs":
                if self.camera != "live" or self.job is not None or self.phase in ("countdown", "recording"):
                    raise ValueError("Wait for the camera or current recording.")
                self.reset()
                self.phase = "countdown"
                self.deadline = monotonic() + PREPARE_SECONDS
                self.message = "Get ready to sign."
            elif action == "cancel" and self.mode == "signs":
                self.reset()
            elif action == "accept" and self.mode == "signs":
                if (self.camera != "live" or self.phase != "result"
                        or payload.get("generation") != self.generation):
                    raise ValueError("That prediction is no longer available.")
                self.append_prediction()
                self.reset()
            else:
                raise ValueError("Action is unavailable in this mode.")
        return self.state()

    def submit(self, kind, fn, *args):
        self.job = self.worker.submit(fn, *args)
        self.job_generation = self.generation
        self.job_kind = kind

    def collect(self, now):
        if self.job is None or not self.job.done():
            return
        job, self.job = self.job, None
        if self.job_generation != self.generation:
            return
        try:
            value = job.result()
        except ClipError as error:
            self.reset()
            self.phase, self.message = "retry", str(error)
            return
        except Exception:
            LOG.exception("Recognition failed")
            self.reset()
            self.phase, self.message = "error", "Recognition failed. Restart the app if it persists."
            return
        if self.job_kind == "sign":
            self.label, margin = value
            self.confidence = round(margin, 2)
            self.phase = "result"
            self.message = "Close match. Review before adding." if margin < SIGN_MIN_MARGIN else "Review the predicted sign."
        elif value is None:
            self.history.clear()
            self.label = self.confidence = None
            self.phase = "no_hand"
            self.letter_candidate = None
            if self.hand_missing_since is None:
                self.hand_missing_since = now
            if now - self.hand_missing_since >= LETTER_RELEASE_SECONDS:
                self.last_added = None
        else:
            self.hand_missing_since = None
            self.history.append(value)
            average = np.mean(self.history, axis=0)
            top = int(np.argmax(average))
            confidence = float(average[top])
            stable = len(self.history) >= 5 and confidence >= 0.60
            self.label = self.models.letters.labels[top] if stable else None
            self.confidence = round(confidence * 100) if stable else None
            self.phase = "result" if stable else "tracking"
            if self.label != self.letter_candidate:
                self.letter_candidate = self.label
                self.letter_since = now
            if self.label and self.label != self.last_added and now - self.letter_since >= LETTER_HOLD_SECONDS:
                self.append_prediction()

    def on_frame(self, frame, now):
        self.collect(now)
        if self.mode == "letters":
            if self.detecting and self.job is None and now >= self.next_letter:
                self.next_letter = now + 1 / 12
                self.submit("letter", self.models.letter, now, frame.copy(), self.mirror)
            return
        if self.phase == "countdown" and now >= self.deadline:
            self.phase = "recording"
            self.started = now
            self.deadline = now + RECORD_SECONDS
            self.next_sample = 0
            self.message = ""
        if self.phase == "recording":
            if now >= self.deadline:
                frames, self.frames = self.frames, []
                if len(frames) < self.models.settings["ordered_frames"]:
                    self.phase, self.message = "retry", "Too few camera frames. Try recording again."
                else:
                    self.phase = "processing"
                    self.submit("sign", self.models.sign, frames)
            else:
                elapsed = now - self.started
                if elapsed >= self.next_sample:
                    rate = self.models.settings["sample_fps"]
                    self.next_sample = (math.floor(elapsed * rate) + 1) / rate
                    self.frames.append((elapsed, frame.copy()))

    def stream(self):
        cap = None
        failed = False
        try:
            cap = cv2.VideoCapture(self.camera_index)
            if not cap.isOpened():
                raise ClipError("Camera unavailable. Close other apps using it and try again.")
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            cap.set(cv2.CAP_PROP_FPS, 30)
            while True:
                with self.lock:
                    if self.camera == "stopping":
                        break
                ok, frame = cap.read()
                if not ok:
                    raise ClipError("Camera stopped responding. Try starting it again.")
                with self.lock:
                    if self.camera == "stopping":
                        break
                    self.camera = "live"
                    self.on_frame(frame, monotonic())
                ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
                if not ok:
                    raise ClipError("The camera preview could not be decoded.")
                yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg.tobytes() + b"\r\n"
        except Exception as error:
            failed = True
            LOG.warning("Camera stream failed: %s", error)
            with self.lock:
                self.reset()
                self.message = str(error) if isinstance(error, ClipError) else "Camera error. Try starting it again."
        finally:
            if cap is not None:
                cap.release()
            with self.lock:
                if not failed:
                    self.reset()
                self.camera = "error" if failed else "off"

    def close(self):
        self.worker.shutdown(wait=True, cancel_futures=True)
        self.models.close()


def create_app(models, camera_index=0):
    app = Flask(__name__)
    session = CameraSession(models, camera_index)
    app.extensions["camera_session"] = session
    atexit.register(session.close)

    @app.get("/")
    def index():
        return render_template("combined.html")

    @app.get("/video_feed")
    def video_feed():
        with session.lock:
            if session.camera not in ("off", "error"):
                return "Camera already active.", 409
            session.reset()
            session.camera = "opening"
        return Response(session.stream(), mimetype="multipart/x-mixed-replace; boundary=frame",
                        headers={"Cache-Control": "no-store"})

    @app.get("/api/state")
    def state():
        return jsonify(session.state()), 200, {"Cache-Control": "no-store"}

    @app.post("/api/<action>")
    def control(action):
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify(error="Expected a JSON object."), 400
        try:
            return jsonify(session.action(action, payload))
        except ValueError as error:
            return jsonify(error=str(error)), 409

    return app


if __name__ == "__main__":
    args = parse_args()
    logging.basicConfig(level=logging.INFO)
    models = Recognizers(
        signs_bundle(os.environ.get("FSL_SIGNS_VOCAB")), ROOT / "models" / "fingerspelling",
        venv_python(ROOT / ".venv-letters"),
    )
    app = create_app(models, int(os.environ.get("FSL_CAMERA_INDEX", "0")))
    session = app.extensions["camera_session"]
    atexit.unregister(session.close)
    browser_host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(args.host, args.host)
    if ":" in browser_host:
        browser_host = f"[{browser_host}]"
    url = f"http://{browser_host}:{args.port}"
    try:
        with make_server(args.host, args.port, app, threaded=True) as server:
            print(f"FSL browser app: {url}", flush=True)
            if args.host in ("0.0.0.0", "::"):
                print(f"LAN access: use the host computer's network IP and port {args.port}.", flush=True)
            if not args.no_browser:
                Thread(target=webbrowser.open, args=(url,), daemon=True).start()
            server.serve_forever()
    finally:
        session.close()
