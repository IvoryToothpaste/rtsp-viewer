import tkinter as tk
from tkinter import messagebox
import cv2
import json
from PIL import Image, ImageTk
import threading
import time
import os
import queue

#Forces TCP, strike this out if you are using UDP.
os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp"

class rtspviewer:
    def __init__(self, root):
        self.root = root
        self.root.title("rtsp-viewer")
        self.root.geometry("1200x800")
        
        self.request_queue = queue.Queue()
        self.is_running = True
        self.feed_widgets = {} # Stores frames to allow highlighting
        self.feeds = self.load_config()

        # --- SCROLLABLE SIDEBAR ---
        self.sidebar_container = tk.Frame(root, width=250, bg="#2c3e50")
        self.sidebar_container.pack(side="left", fill="y")
        self.sidebar_container.pack_propagate(False)

        self.canvas = tk.Canvas(self.sidebar_container, bg="#2c3e50", highlightthickness=0)
        self.scrollbar = tk.Scrollbar(self.sidebar_container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = tk.Frame(self.canvas, bg="#2c3e50")

        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw", width=230)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scrollbar.pack(side="right", fill="y")

        # --- MAIN VIDEO AREA ---
        self.video_container = tk.Frame(root, bg="black")
        self.video_container.pack(side="right", expand=True, fill="both")
        self.status_label = tk.Label(self.video_container, text="Select a feed", fg="#95a5a6", bg="black", font=("Arial", 10))
        self.status_label.pack(pady=5)
        self.video_label = tk.Label(self.video_container, bg="black")
        self.video_label.pack(expand=True, fill="both")

        # --- POPULATE SIDEBAR ---
        tk.Label(self.scrollable_frame, text="STREAMS", fg="white", bg="#2c3e50", font=("Arial", 12, "bold")).pack(pady=15)

        for feed in self.feeds:
            name = feed.get('name', 'Unknown')
            url = feed.get('url', '')
            comment = feed.get('comment', None) # Optional
            hotkey = feed.get('hotkey', None)   # Optional

            # Create entry frame
            btn_frame = tk.Frame(self.scrollable_frame, bg="#34495e", cursor="hand2", padx=2, pady=2)
            btn_frame.pack(fill="x", padx=10, pady=4)
            
            # Inner container for the text
            inner_cont = tk.Frame(btn_frame, bg="#34495e")
            inner_cont.pack(fill="x", padx=5, pady=5)

            tk.Label(inner_cont, text=name, fg="white", bg="#34495e", font=("Arial", 10, "bold"), anchor="w").pack(fill="x")
            
            if comment:
                tk.Label(inner_cont, text=comment, fg="#bdc3c7", bg="#34495e", font=("Arial", 8), anchor="w").pack(fill="x")

            # Store widget reference for highlighting later
            self.feed_widgets[url] = btn_frame

            # Bind clicks
            for w in (btn_frame, inner_cont, *inner_cont.winfo_children()):
                w.bind("<Button-1>", lambda e, u=url, n=name: self.request_switch(u, n))

            # Bind Hotkey if present
            if hotkey:
                self.root.bind(f"<{hotkey}>", lambda e, u=url, n=name: self.request_switch(u, n))

        self.root.bind_all("<MouseWheel>", self._on_mousewheel)
        
        # Worker Thread
        self.worker_thread = threading.Thread(target=self.video_worker, daemon=True)
        self.worker_thread.start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

    def _on_mousewheel(self, event):
        direction = -1 if (event.delta > 0 or event.num == 4) else 1
        self.canvas.yview_scroll(direction, "units")

    def load_config(self):
        try:
            base_path = os.path.dirname(os.path.abspath(__file__))
            with open(os.path.join(base_path, 'config.json'), 'r') as f:
                return json.load(f).get('feeds', [])
        except Exception as e:
            print(f"Config error: {e}")
            return []

    def highlight_active(self, active_url):
        """Changes the background of the selected feed to show it's active."""
        for url, frame in self.feed_widgets.items():
            if url == active_url:
                frame.config(bg="#3498db") # Active Blue
                for child in frame.winfo_children():
                    child.config(bg="#3498db")
                    for grandchild in child.winfo_children():
                        grandchild.config(bg="#3498db")
            else:
                frame.config(bg="#34495e") # Default Gray
                for child in frame.winfo_children():
                    child.config(bg="#34495e")
                    for grandchild in child.winfo_children():
                        grandchild.config(bg="#34495e")

    def request_switch(self, url, name):
        if url:
            self.highlight_active(url)
            self.request_queue.put((url, name))

    def video_worker(self):
        cap = None
        active_url = None
        while self.is_running:
            try:
                new_url, new_name = self.request_queue.get(block=False)
                if cap: cap.release(); time.sleep(0.4)
                active_url = new_url
                cap = cv2.VideoCapture(active_url)
                self.root.after(0, lambda n=new_name: self.status_label.config(text=f"● LIVE: {n}"))
            except queue.Empty: pass

            if cap and cap.isOpened():
                ret, frame = cap.read()
                if ret:
                    win_w = max(self.video_container.winfo_width(), 100)
                    win_h = max(self.video_container.winfo_height() - 40, 100)
                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    img = Image.fromarray(frame)
                    img.thumbnail((win_w, win_h), Image.Resampling.LANCZOS)
                    imgtk = ImageTk.PhotoImage(image=img)
                    self.root.after(0, self.update_ui, imgtk)
                else:
                    cap.release(); time.sleep(2); cap = cv2.VideoCapture(active_url)
            else: time.sleep(0.1)

    def update_ui(self, imgtk):
        if self.is_running:
            self.video_label.config(image=imgtk)
            self.video_label.image = imgtk

    def on_closing(self):
        self.is_running = False
        self.root.destroy()

if __name__ == "__main__":
    root = tk.Tk()
    app = rtspviewer(root)
    root.mainloop()