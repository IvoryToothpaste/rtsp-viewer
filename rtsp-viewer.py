import tkinter as tk
from tkinter import messagebox
import cv2
import json
from PIL import Image, ImageTk
import threading
import time
import os
import queue

# ---------------------------------------------------------
# RTSP / FFMPEG SETTINGS
# ---------------------------------------------------------
# Force TCP transport and low latency behavior
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = (
    "rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|framedrop;1"
)


class RTSPViewer:
    # ---------------------------------------------------------
    # INITIALIZATION
    # ---------------------------------------------------------
    def __init__(self, root):
        self.root = root
        self.root.title("rtsp-viewer")
        self.root.geometry("1200x800")

        # ---------------- Application State ----------------
        self.is_running = True
        self.request_queue = queue.Queue()

        self.grid_mode = 1
        self.selected_slot = 0
        self.slot_map = {}
        self.slot_labels = []
        self.hotkey_map = {}

        self.maintain_aspect = False
        self.fullscreen = False
        self.sidebar_visible = True

        # Fullscreen banner text (loaded from config)
        self.fullscreen_text = "rtsp-viewer"

        self.feeds = self.load_config()
        self.setup_ui()

        # ---------------- Background Worker ----------------
        self.worker_thread = threading.Thread(
            target=self.video_worker,
            daemon=True
        )
        self.worker_thread.start()

        # ---------------- Global Key Bindings ----------------
        self.root.bind_all("<Key>", self.universal_key_handler)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    # ---------------------------------------------------------
    # UI SETUP
    # ---------------------------------------------------------
    def setup_ui(self):

        # ---------- Sidebar ----------
        self.sidebar_container = tk.Frame(self.root, width=230, bg="#1f1f1f")
        self.sidebar_container.pack(side="left", fill="y")
        self.sidebar_container.pack_propagate(False)

        # Minimal toggle tab
        self.toggle_btn = tk.Label(
            self.sidebar_container,
            text="◀",
            bg="#1f1f1f",
            fg="#888",
            cursor="hand2"
        )
        self.toggle_btn.pack(anchor="ne", padx=5, pady=5)
        self.toggle_btn.bind("<Button-1>", lambda e: self.toggle_sidebar())

        # Scrollable stream list
        self.canvas = tk.Canvas(self.sidebar_container, bg="#1f1f1f", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.sidebar_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#1f1f1f")

        # ---------- Mousewheel scrolling ----------
        self.canvas.bind_all("<MouseWheel>", self._on_mousewheel)        # Windows / Pi
        self.canvas.bind_all("<Button-4>", self._on_mousewheel_linux)    # Linux scroll up
        self.canvas.bind_all("<Button-5>", self._on_mousewheel_linux)    # Linux scroll down

        self.canvas.bind(
            "<Configure>",
            lambda e: self.canvas.itemconfig(
                self.canvas_window,
                width=e.width
            )
        )


        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        )

        self.canvas_window = self.canvas.create_window(
            (0, 0),
            window=self.scrollable_frame,
            anchor="nw"
)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # ---------- Main Video Area ----------
        self.main_content = tk.Frame(self.root, bg="black")
        self.main_content.pack(side="right", expand=True, fill="both")

        # ---------- Fullscreen Top Banner ----------
        self.fullscreen_bar = tk.Frame(
            self.main_content,
            bg="#000000",
            height=32
        )

        self.fullscreen_label = tk.Label(
            self.fullscreen_bar,
            text=self.fullscreen_text,
            bg="#000000",
            fg="white",
            font=("Arial", 12, "bold"),
            anchor="center"
        )

        # Center the text properly
        self.fullscreen_label.pack(expand=True, fill="both")

        # ---------- Layout selector ----------
        self.control_bar = tk.Frame(self.main_content, bg="#151515", height=35)
        self.control_bar.pack(side="bottom", fill="x")

        # RPi-friendly layouts only
        layouts = [("1x1", 1), ("2x2", 4)]
        for text, val in layouts:
            tk.Button(
                self.control_bar,
                text=text,
                command=lambda v=val: self.set_grid_mode(v),
                bg="#2a2a2a",
                fg="white",
                relief="flat",
                padx=12
            ).pack(side="left", padx=4, pady=4)

        self.video_area = tk.Frame(self.main_content, bg="black")
        self.video_area.pack(expand=True, fill="both")

        # ---------- Stream List ----------
        for feed in self.feeds:
            url = feed.get("url", "")
            name = feed.get("name", "Unknown")
            hk = feed.get("hotkey")

            if hk:
                self.hotkey_map[str(hk)] = url

            row = tk.Label(
                self.scrollable_frame,
                text=name,
                bg="#2a2a2a",
                fg="white",
                anchor="w",
                padx=8,
                pady=6,
                cursor="hand2"
            )
            row.pack(fill="x", padx=6, pady=2)
            row.bind("<Button-1>", lambda e, u=url: self.assign_stream_to_slot(u))

        self.set_grid_mode(1)

    # ---------------------------------------------------------
    # SIDEBAR CONTROL
    # ---------------------------------------------------------
    def toggle_sidebar(self):
        if self.sidebar_visible:
            self.sidebar_container.config(width=18)
            self.canvas.pack_forget()
            self.scrollbar.pack_forget()
            self.toggle_btn.config(text="▶")
            self.sidebar_visible = False
        else:
            self.sidebar_container.config(width=230)
            self.canvas.pack(side="left", fill="both", expand=True)
            self.scrollbar.pack(side="right", fill="y")
            self.toggle_btn.config(text="◀")
            self.sidebar_visible = True

    # ---------------------------------------------------------
    # SIDEBAR SCROLL HANDLERS
    # ---------------------------------------------------------
    def _on_mousewheel(self, event):
        if self.sidebar_visible:
            self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def _on_mousewheel_linux(self, event):
        if self.sidebar_visible:
            if event.num == 4:
                self.canvas.yview_scroll(-1, "units")
            elif event.num == 5:
                self.canvas.yview_scroll(1, "units")


    # ---------------------------------------------------------
    # GRID MANAGEMENT
    # ---------------------------------------------------------
    def set_grid_mode(self, mode):

        self.grid_mode = mode
        self.selected_slot = 0
        self.slot_map = {}
        self.slot_labels = []

        for w in self.video_area.winfo_children():
            w.destroy()

        self.request_queue.put(("CLEAR", None))

        rows = int(mode ** 0.5)
        cols = rows if rows * rows == mode else rows + 1

        for r in range(rows):
            self.video_area.grid_rowconfigure(r, weight=1, uniform="video")

        for c in range(cols):
            self.video_area.grid_columnconfigure(c, weight=1, uniform="video")

        for i in range(mode):
            frame = tk.Frame(
                self.video_area,
                bg="#101010",
                highlightthickness=1,
                highlightbackground="#222"
            )
            frame.grid(
                row=i // cols,
                column=i % cols,
                sticky="nsew",
                padx=1,
                pady=1
            )

            label = tk.Label(frame, bg="black")
            label.pack(expand=True, fill="both")

            self.slot_labels.append(label)

            frame.bind("<Button-1>", lambda e, idx=i: self.select_slot(idx))
            label.bind("<Button-1>", lambda e, idx=i: self.select_slot(idx))

        self.update_highlight()

    def select_slot(self, index):
        self.selected_slot = index
        self.update_highlight()

    def update_highlight(self):
        for i, lbl in enumerate(self.slot_labels):
            lbl.master.config(
                highlightbackground="#3498db" if i == self.selected_slot else "#222",
                highlightthickness=2 if i == self.selected_slot else 1
            )

    def assign_stream_to_slot(self, url):
        self.slot_map[self.selected_slot] = url
        self.request_queue.put(("UPDATE", dict(self.slot_map)))
        self.selected_slot = (self.selected_slot + 1) % self.grid_mode
        self.update_highlight()

    # ---------------------------------------------------------
    # VIDEO WORKER THREAD (OPTIMIZED FOR RPI)
    # ---------------------------------------------------------
    def video_worker(self):

        caps = {}
        active_map = {}
        last_frame_time = {}
        target_interval = 1 / 30

        while self.is_running:

            # ---------- Handle Commands ----------
            try:
                cmd, data = self.request_queue.get_nowait()

                if cmd == "CLEAR":
                    for cap in caps.values():
                        cap.release()
                    caps.clear()
                    active_map.clear()

                elif cmd == "UPDATE":
                    active_map = data

                    for url in active_map.values():
                        if url and url not in caps:
                            cap = cv2.VideoCapture(url)
                            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                            caps[url] = cap

                    for url in list(caps.keys()):
                        if url not in active_map.values():
                            caps[url].release()
                            del caps[url]

            except queue.Empty:
                pass

            # ---------- Read Frames ----------
            now = time.time()

            for idx, url in list(active_map.items()):

                cap = caps.get(url)
                if not cap or not cap.isOpened():
                    continue

                if now - last_frame_time.get(url, 0) < target_interval:
                    continue

                ret, frame = cap.read()
                if not ret:
                    cap.release()
                    caps[url] = cv2.VideoCapture(url)
                    continue

                last_frame_time[url] = now

                lbl = self.slot_labels[idx]
                w = lbl.winfo_width()
                h = lbl.winfo_height()

                if w < 10 or h < 10:
                    continue

                # Convert color ONCE (performance critical)
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(frame)

                if self.maintain_aspect:
                    img = self.letterbox(img, w, h)
                else:
                    img = img.resize((w, h), Image.Resampling.BILINEAR)

                tk_img = ImageTk.PhotoImage(img)
                self.root.after(0, self.safe_update, idx, tk_img)

            time.sleep(0.01)

    # ---------------------------------------------------------
    # SAFE UI UPDATE
    # ---------------------------------------------------------
    def safe_update(self, idx, img):
        if self.is_running and idx < len(self.slot_labels):
            lbl = self.slot_labels[idx]
            lbl.config(image=img)
            lbl.image = img

    # ---------------------------------------------------------
    # LETTERBOX RESIZE (PRESERVE ASPECT RATIO)
    # ---------------------------------------------------------
    def letterbox(self, img, tw, th):
        sw, sh = img.size

        ratio = min(tw / sw, th / sh)
        nw = int(sw * ratio)
        nh = int(sh * ratio)

        img = img.resize((nw, nh), Image.Resampling.BILINEAR)

        background = Image.new("RGB", (tw, th), (0, 0, 0))
        background.paste(img, ((tw - nw) // 2, (th - nh) // 2))

        return background

    def toggle_aspect_mode(self, e=None):
        self.maintain_aspect = not self.maintain_aspect

    # ---------------------------------------------------------
    # KEY HANDLING
    # ---------------------------------------------------------
    def universal_key_handler(self, event):
        key = event.keysym

        if key in ["f", "F"]:
            self.toggle_fullscreen()
        elif key in ["a", "A"]:
            self.toggle_aspect_mode()
        elif key == "Escape":
            self.exit_fullscreen()
        elif key in self.hotkey_map:
            self.assign_stream_to_slot(self.hotkey_map[key])

    # ---------------------------------------------------------
    # WINDOW CONTROL
    # ---------------------------------------------------------
    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.root.attributes("-fullscreen", self.fullscreen)

        # Show/hide top banner ABOVE video area
        if self.fullscreen:
            self.fullscreen_bar.pack(
                side="top",
                fill="x",
                before=self.video_area
            )
        else:
            self.fullscreen_bar.pack_forget()

    def exit_fullscreen(self):
        if self.fullscreen:
            self.toggle_fullscreen()

    # ---------------------------------------------------------
    # CONFIG LOADING
    # ---------------------------------------------------------
    def load_config(self):
        try:
            with open("config.json", "r") as f:
                data = json.load(f)
                self.fullscreen_text = data.get("fullscreen_text", "rtsp-viewer")
                return data.get("feeds", [])
        except Exception:
            self.fullscreen_text = "rtsp-viewer"
            return []

    # ---------------------------------------------------------
    # CLEAN SHUTDOWN
    # ---------------------------------------------------------
    def on_closing(self):
        self.is_running = False
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = RTSPViewer(root)
    root.mainloop()
