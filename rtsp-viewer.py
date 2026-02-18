import tkinter as tk
from tkinter import messagebox
import cv2
import json
from PIL import Image, ImageTk
import threading
import time
import os
import queue

os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|framedrop;1"
)


class rtspviewer:
    def __init__(self, root):
        # ---------------- WINDOW ----------------
        self.root = root
        self.root.title("rtsp-viewer")
        self.root.geometry("1200x800")

        # ---------------- STATE ----------------
        self.is_running = True
        self.request_queue = queue.Queue()
        self.feed_widgets = {}

        # scaling mode
        self.maintain_aspect = False
        self.fullscreen = False

        self.feeds = self.load_config()
        self.setup_ui()

        # start video thread
        self.worker_thread = threading.Thread(
            target=self.video_worker, daemon=True
        )
        self.worker_thread.start()

        # hotkeys
        self.root.bind("f", self.toggle_fullscreen)
        self.root.bind("F", self.toggle_fullscreen)
        self.root.bind("a", self.toggle_aspect_mode)
        self.root.bind("A", self.toggle_aspect_mode)
        self.root.bind("<Escape>", self.exit_fullscreen)

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ============================================================
    # UI
    # ============================================================
    def setup_ui(self):
        """Builds sidebar and video display."""

        # ----- SIDEBAR -----
        self.sidebar_container = tk.Frame(
            self.root, width=250, bg="#2c3e50"
        )
        self.sidebar_container.pack(side="left", fill="y")
        self.sidebar_container.pack_propagate(False)

        self.canvas = tk.Canvas(
            self.sidebar_container, bg="#2c3e50", highlightthickness=0
        )
        self.scrollbar = tk.Scrollbar(
            self.sidebar_container,
            orient="vertical",
            command=self.canvas.yview,
        )
        self.scrollable_frame = tk.Frame(
            self.canvas, bg="#2c3e50"
        )

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )

        self.canvas.create_window(
            (0, 0),
            window=self.scrollable_frame,
            anchor="nw",
            width=230,
        )
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # ----- VIDEO AREA -----
        self.video_container = tk.Frame(self.root, bg="black")
        self.video_container.pack(side="right", expand=True, fill="both")

        self.status_label = tk.Label(
            self.video_container,
            text="Select a feed",
            fg="#95a5a6",
            bg="black",
            font=("Arial", 10),
        )
        self.status_label.pack(side="top", fill="x")

        self.video_label = tk.Label(self.video_container, bg="black")
        self.video_label.pack(expand=True, fill="both")

        # ----- SIDEBAR CONTENT -----
        tk.Label(
            self.scrollable_frame,
            text="STREAMS",
            fg="white",
            bg="#2c3e50",
            font=("Arial", 12, "bold"),
        ).pack(pady=15)

        for feed in self.feeds:
            name = feed.get("name", "Unknown")
            url = feed.get("url", "")
            comment = feed.get("comment")
            hotkey = feed.get("hotkey")

            btn_frame = tk.Frame(
                self.scrollable_frame,
                bg="#34495e",
                cursor="hand2",
                padx=2,
                pady=2,
            )
            btn_frame.pack(fill="x", padx=10, pady=4)

            inner = tk.Frame(btn_frame, bg="#34495e")
            inner.pack(fill="x", padx=5, pady=5)

            tk.Label(
                inner,
                text=name,
                fg="white",
                bg="#34495e",
                font=("Arial", 10, "bold"),
                anchor="w",
            ).pack(fill="x")

            if comment:
                tk.Label(
                    inner,
                    text=comment,
                    fg="#bdc3c7",
                    bg="#34495e",
                    font=("Arial", 8),
                    anchor="w",
                ).pack(fill="x")

            self.feed_widgets[url] = btn_frame

            for w in (btn_frame, inner, *inner.winfo_children()):
                w.bind(
                    "<Button-1>",
                    lambda e, u=url, n=name: self.request_switch(u, n),
                )

            if hotkey:
                self.root.bind(
                    f"<{hotkey}>",
                    lambda e, u=url, n=name: self.request_switch(u, n),
                )

        self.root.bind_all("<MouseWheel>", self._on_mousewheel)

    # ============================================================
    # VIDEO THREAD
    # ============================================================
    def video_worker(self):
        """Main video loop with reconnect + frame drop."""

        cap = None
        active_url = None
        last_frame_time = 0
        target_fps = 30

        while self.is_running:
            # ----- handle stream switch -----
            try:
                new_url, new_name = self.request_queue.get(block=False)

                if cap:
                    cap.release()
                    time.sleep(0.25)

                active_url = new_url
                cap = cv2.VideoCapture(active_url)

                self.root.after(
                    0,
                    lambda n=new_name: self.status_label.config(
                        text=f"● LIVE: {n}"
                    ),
                )

            except queue.Empty:
                pass

            # ----- read frame -----
            if cap and cap.isOpened():
                now = time.time()

                # frame drop if running behind
                if now - last_frame_time < (1 / target_fps):
                    time.sleep(0.001)
                    continue

                ret, frame = cap.read()

                if not ret:
                    # reconnect watchdog
                    cap.release()
                    time.sleep(1)
                    cap = cv2.VideoCapture(active_url)
                    continue

                last_frame_time = now

                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)

                # ----- scaling -----
                label_w = max(self.video_label.winfo_width(), 1)
                label_h = max(self.video_label.winfo_height(), 1)

                if self.maintain_aspect:
                    img = self.resize_letterbox(img, label_w, label_h)
                else:
                    img = img.resize(
                        (label_w, label_h),
                        Image.Resampling.BILINEAR,
                    )

                imgtk = ImageTk.PhotoImage(img)

                self.root.after(0, self.update_ui, imgtk)

            else:
                time.sleep(0.1)

    # ============================================================
    # LETTERBOX RESIZE
    # ============================================================
    def resize_letterbox(self, img, target_w, target_h):
        """Resizes image keeping aspect ratio with black bars."""

        src_w, src_h = img.size
        ratio = min(target_w / src_w, target_h / src_h)

        new_size = (int(src_w * ratio), int(src_h * ratio))
        resized = img.resize(new_size, Image.Resampling.BILINEAR)

        background = Image.new("RGB", (target_w, target_h), (0, 0, 0))
        x = (target_w - new_size[0]) // 2
        y = (target_h - new_size[1]) // 2
        background.paste(resized, (x, y))
        return background

    # ============================================================
    # UI HELPERS
    # ============================================================
    def update_ui(self, imgtk):
        if self.is_running:
            self.video_label.config(image=imgtk)
            self.video_label.image = imgtk

    def request_switch(self, url, name):
        if url:
            self.highlight_active(url)
            self.request_queue.put((url, name))

    def highlight_active(self, active_url):
        for url, frame in self.feed_widgets.items():
            color = "#3498db" if url == active_url else "#34495e"
            frame.config(bg=color)
            for child in frame.winfo_children():
                child.config(bg=color)
                for grandchild in child.winfo_children():
                    grandchild.config(bg=color)

    def toggle_fullscreen(self, event=None):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

    def exit_fullscreen(self, event=None):
        self.fullscreen = False
        self.root.attributes("-fullscreen", False)

    def toggle_aspect_mode(self, event=None):
        self.maintain_aspect = not self.maintain_aspect
        mode = "Aspect" if self.maintain_aspect else "Stretch"
        self.status_label.config(text=f"Mode: {mode}")

    def _on_mousewheel(self, event):
        direction = -1 if (event.delta > 0 or event.num == 4) else 1
        self.canvas.yview_scroll(direction, "units")

    def load_config(self):
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(base_path, "config.json"), "r") as f:
                return json.load(f).get("feeds", [])
        except Exception as e:
            print(f"Config error: {e}")
            return []

    def on_closing(self):
        self.is_running = False
        self.root.destroy()


# ================================================================
# ENTRY POINT
# ================================================================
if __name__ == "__main__":
    root = tk.Tk()
    app = rtspviewer(root)
    root.mainloop()
