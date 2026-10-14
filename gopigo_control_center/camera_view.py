"""A live camera picture on a canvas, with faces found and boxed.

CameraView reads an MJPEG stream (the robot's camera stream, or the Autopilot navigator's) in a background
thread, decodes each frame with Pillow, looks for faces with OpenCV's YuNet detector (models/), draws a box
round each, and hands the newest frame to the Tk thread, which shows it when tick() is called.
"""

import io
import os
import threading
import time
import urllib.request

from dependencies import Image, ImageDraw, ImageTk, cv2, numpy
from theme import FONT_MONO_SMALL, MUTED

FACE_MODEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "face_detection_yunet_2023mar.onnx")
DETECT_WIDTH = 320       # frames are shrunk to this width to look for faces: plenty, and fast
FACE_SCORE = 0.8         # how sure the detector must be
FACE_HOLD_SECONDS = 1.0  # a face counts as still there this long after it was last seen, so it doesn't flicker
FACE_BOX = "#e0473b"


def face_detection_note():
    """Whether faces can be looked for, in a few words, without loading the model."""
    if cv2 is None:
        return "No face detection: install OpenCV (pip install opencv-python-headless) and restart the app"
    if not hasattr(cv2, "FaceDetectorYN"):
        return "No face detection: needs OpenCV 4.5.4 or newer (pip install -U opencv-python-headless)"
    if not os.path.exists(FACE_MODEL):
        return "No face detection: the face model is missing from models/"
    return "Face detection on"


class FaceDetector:
    """Finds faces with OpenCV's YuNet model. error says why it can't, if it can't."""

    def __init__(self):
        self.error = ""
        self._detector = None
        self._size = None
        if cv2 is None:
            self.error = "Face detection needs OpenCV: pip install opencv-python-headless"
        elif not hasattr(cv2, "FaceDetectorYN"):
            self.error = "Face detection needs OpenCV 4.5.4 or newer: pip install -U opencv-python-headless"
        else:
            try:
                self._detector = cv2.FaceDetectorYN.create(FACE_MODEL, "", (DETECT_WIDTH, 240), FACE_SCORE, 0.3, 5000)
            except Exception as e:
                self.error = f"Couldn't load the face model: {e}"

    def find(self, image):
        """Boxes (x, y, w, h) of the faces in a Pillow image, in that image's pixels."""
        if self._detector is None:
            return []
        scale = DETECT_WIDTH / image.width
        small = image.resize((DETECT_WIDTH, max(1, round(image.height * scale))))
        if small.size != self._size:
            self._size = small.size
            self._detector.setInputSize(small.size)
        bgr = numpy.ascontiguousarray(numpy.asarray(small.convert("RGB"))[:, :, ::-1])
        _, faces = self._detector.detect(bgr)
        if faces is None:
            return []
        return [tuple(int(v / scale) for v in face[:4]) for face in faces]


class CameraView:
    def __init__(self, parent, root, idle_text="Camera off", on_face_change=None, face_note_var=None):
        """on_face_change(present, snapshot) is called on the Tk thread when a face appears (with the frame,
        boxes drawn) or goes (snapshot None). face_note_var, a StringVar, is told whether faces are being
        looked for."""
        import tkinter as tk

        self.root = root
        self.idle_text = idle_text
        self.on_face_change = on_face_change
        self.canvas = tk.Canvas(parent, bg="#12161d", highlightthickness=0, width=1, height=1)
        self.canvas.bind("<Configure>", self._on_resize)
        self.size = (640, 480)
        self.reader = None       # identifies the running reader; None stops it
        self.frame = None        # the newest frame, waiting for tick()
        self.photo = None
        self.image_item = None
        self.placeholder_text = None
        self.face_present = False
        self.face_last_seen = 0.0
        self.face_note = ""      # why faces aren't being looked for, if they aren't
        self.face_note_var = face_note_var
        self.placeholder()

    # ---- running ----
    def start(self, url):
        """Show the MJPEG stream at url + "stream.mjpg"."""
        if Image is None:
            self.placeholder("Install Pillow to see the camera here: pip install Pillow")
            return
        token = object()
        self.reader = token
        threading.Thread(target=self._read, args=(url, token), daemon=True).start()

    def stop(self, text=None):
        self.reader = None
        self.frame = None
        self._set_face(False, None)
        self.placeholder(text)

    @property
    def running(self):
        return self.reader is not None

    def _read(self, url, token):
        detector = FaceDetector()
        self.face_note = detector.error
        if self.face_note_var is not None:
            note = detector.error or "Face detection on"
            self.root.after(0, lambda: self.face_note_var.set(note))
        try:
            with urllib.request.urlopen(url + "stream.mjpg", timeout=10) as stream:
                while self.reader is token:
                    length = None
                    while True:  # each frame: a few header lines with a Content-Length, a blank line, the JPEG
                        line = stream.readline()
                        if not line:
                            raise ConnectionError("the stream ended")
                        line = line.strip()
                        if line.lower().startswith(b"content-length:"):
                            length = int(line.split(b":", 1)[1])
                        elif not line and length:
                            break
                    frame = Image.open(io.BytesIO(stream.read(length))).convert("RGB")
                    faces = detector.find(frame)
                    if faces:
                        self.face_last_seen = time.time()
                        draw = ImageDraw.Draw(frame)
                        for x, y, w, h in faces:
                            draw.rectangle((x, y, x + w, y + h), outline=FACE_BOX, width=max(2, frame.width // 200))
                    present = time.time() - self.face_last_seen < FACE_HOLD_SECONDS
                    if present != self.face_present:
                        snapshot = frame.copy() if present else None
                        self.root.after(0, lambda p=present, s=snapshot: self._set_face(p, s))
                    frame.thumbnail(self.size, Image.Resampling.BILINEAR)
                    self.frame = frame
        except Exception as e:
            error = str(e)
            if self.reader is token:
                self.reader = None
                self.root.after(0, lambda: self.stop(f"Lost the camera picture: {error}"))

    def _set_face(self, present, snapshot):
        if present == self.face_present:
            return
        self.face_present = present
        if self.on_face_change is not None:
            self.on_face_change(present, snapshot)

    # ---- showing ----
    def tick(self):
        """Show the newest frame. Call often from the Tk thread."""
        frame, self.frame = self.frame, None
        if frame is None or self.reader is None:
            return
        self.photo = ImageTk.PhotoImage(frame)  # keep a reference, or Tk shows nothing
        w, h = self.size
        if self.image_item is None:
            self.canvas.delete("all")
            self.image_item = self.canvas.create_image(w / 2, h / 2, image=self.photo)
        else:
            self.canvas.coords(self.image_item, w / 2, h / 2)
            self.canvas.itemconfig(self.image_item, image=self.photo)

    def placeholder(self, text=None):
        self.placeholder_text = text
        self.canvas.delete("all")
        self.image_item = None
        w, h = self.size
        self.canvas.create_text(
            w / 2, h / 2, text=text or self.idle_text, fill=MUTED, font=FONT_MONO_SMALL, width=max(w - 40, 100),
            justify="center",
        )

    def _on_resize(self, event):
        self.size = (max(event.width, 1), max(event.height, 1))
        if self.image_item is None:
            self.placeholder(self.placeholder_text)
