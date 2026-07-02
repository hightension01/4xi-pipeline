"""
4XI Studios — Pipeline Launcher UI
Lightweight click-and-go interface with live terminal output.
"""

import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import subprocess
import threading
import queue
import sys
import os
from pathlib import Path
from datetime import datetime

# ── Paths ─────────────────────────────────────────────────────────────────────

REPO     = Path(__file__).parent
PYTHON   = r"C:\Users\garre\AppData\Local\Programs\Python\Python312\python.exe"
NODE_DIR = REPO / "nifty-uploader"

# ── Theme ─────────────────────────────────────────────────────────────────────

BG       = "#111318"   # deep navy-black
BG2      = "#1c1f26"   # card background
BG3      = "#252931"   # input / terminal background
BORDER   = "#2e3340"
ACCENT   = "#6c8ef5"   # blue-indigo
ACCENT2  = "#4ade80"   # green (success)
RED      = "#f87171"
YELLOW   = "#fbbf24"
TEXT     = "#e2e8f0"
MUTED    = "#64748b"
MONO     = ("Consolas", 10)
SANS     = ("Segoe UI", 10)
SANS_B   = ("Segoe UI", 10, "bold")
TITLE_F  = ("Segoe UI", 14, "bold")

# ── Status colours ────────────────────────────────────────────────────────────

STATUS_IDLE    = (MUTED,   "● IDLE")
STATUS_RUNNING = (YELLOW,  "● RUNNING")
STATUS_OK      = (ACCENT2, "● DONE")
STATUS_ERROR   = (RED,     "● ERROR")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("4XI Studios — Pipeline")
        self.configure(bg=BG)
        self.resizable(True, True)
        self.minsize(700, 560)

        self._queue  = queue.Queue()
        self._proc   = None
        self._running = False

        self._build_ui()
        self._poll_queue()

        # Center on screen
        self.update_idletasks()
        w, h = 820, 640
        x = (self.winfo_screenwidth()  - w) // 2
        y = (self.winfo_screenheight() - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(self, bg=BG2, pady=14)
        hdr.pack(fill="x")

        tk.Label(hdr, text="4XI", font=("Segoe UI", 18, "bold"),
                 fg=ACCENT, bg=BG2).pack(side="left", padx=(20, 4))
        tk.Label(hdr, text="STUDIOS", font=("Segoe UI", 18),
                 fg=TEXT, bg=BG2).pack(side="left")
        tk.Label(hdr, text="Pipeline Launcher", font=("Segoe UI", 10),
                 fg=MUTED, bg=BG2).pack(side="left", padx=(10, 0), pady=(4, 0))

        self._status_lbl = tk.Label(hdr, text=STATUS_IDLE[1],
                                    fg=STATUS_IDLE[0], bg=BG2,
                                    font=SANS_B)
        self._status_lbl.pack(side="right", padx=20)

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Controls card ─────────────────────────────────────────────────────
        card = tk.Frame(self, bg=BG2, padx=20, pady=18)
        card.pack(fill="x", padx=0)

        # Count row
        row1 = tk.Frame(card, bg=BG2)
        row1.pack(fill="x", pady=(0, 14))

        tk.Label(row1, text="Items to process:", font=SANS,
                 fg=MUTED, bg=BG2).pack(side="left")

        self._count_var = tk.IntVar(value=5)
        count_frame = tk.Frame(row1, bg=BG3, bd=0, highlightthickness=1,
                               highlightbackground=BORDER)
        count_frame.pack(side="left", padx=(10, 0))
        self._count_spin = tk.Spinbox(
            count_frame, from_=1, to=99, textvariable=self._count_var,
            width=4, font=SANS_B, bg=BG3, fg=TEXT,
            buttonbackground=BG3, relief="flat",
            highlightthickness=0, insertbackground=TEXT,
        )
        self._count_spin.pack(padx=6, pady=4)

        # Button row
        row2 = tk.Frame(card, bg=BG2)
        row2.pack(fill="x")

        btn_cfg = dict(font=SANS_B, relief="flat", cursor="hand2",
                       padx=16, pady=10, bd=0)

        self._btn_full = tk.Button(
            row2, text="▶  Full Pipeline",
            bg=ACCENT, fg="white", activebackground="#7c9cf7",
            command=self._run_full, **btn_cfg)
        self._btn_full.pack(side="left", padx=(0, 8))

        self._btn_upload = tk.Button(
            row2, text="↑  Upload + List",
            bg=BG3, fg=TEXT, activebackground=BORDER,
            command=self._run_upload, **btn_cfg)
        self._btn_upload.pack(side="left", padx=(0, 8))

        self._btn_list = tk.Button(
            row2, text="☰  List Only",
            bg=BG3, fg=TEXT, activebackground=BORDER,
            command=self._run_list, **btn_cfg)
        self._btn_list.pack(side="left")

        self._btn_stop = tk.Button(
            row2, text="■  Stop",
            bg=RED, fg="white", activebackground="#f87171",
            command=self._stop, state="disabled", **btn_cfg)
        self._btn_stop.pack(side="right")

        self._all_run_btns = [self._btn_full, self._btn_upload, self._btn_list]

        # ── Divider ───────────────────────────────────────────────────────────
        tk.Frame(self, bg=BORDER, height=1).pack(fill="x")

        # ── Terminal panel ────────────────────────────────────────────────────
        term_hdr = tk.Frame(self, bg=BG, padx=16, pady=8)
        term_hdr.pack(fill="x")

        tk.Label(term_hdr, text="Terminal Output", font=SANS_B,
                 fg=MUTED, bg=BG).pack(side="left")

        tk.Button(term_hdr, text="Clear", font=("Segoe UI", 9),
                  bg=BG3, fg=MUTED, activebackground=BORDER,
                  relief="flat", cursor="hand2", padx=10, pady=3,
                  command=self._clear_terminal).pack(side="right")

        term_wrap = tk.Frame(self, bg=BG, padx=12, pady=12)
        term_wrap.pack(fill="both", expand=True)

        self._terminal = scrolledtext.ScrolledText(
            term_wrap, bg=BG3, fg=TEXT, font=MONO,
            relief="flat", bd=0, wrap="word",
            insertbackground=TEXT,
            highlightthickness=1, highlightbackground=BORDER,
            state="disabled",
        )
        self._terminal.pack(fill="both", expand=True)

        # Tag colours for log lines
        self._terminal.tag_config("ts",      foreground=MUTED)
        self._terminal.tag_config("error",   foreground=RED)
        self._terminal.tag_config("warn",    foreground=YELLOW)
        self._terminal.tag_config("ok",      foreground=ACCENT2)
        self._terminal.tag_config("accent",  foreground=ACCENT)
        self._terminal.tag_config("divider", foreground=BORDER)
        self._terminal.tag_config("normal",  foreground=TEXT)

        self._log_info(f"4XI Pipeline UI ready — {datetime.now().strftime('%B %d, %Y')}")

    # ── Logging ───────────────────────────────────────────────────────────────

    def _write(self, text, tag="normal"):
        self._terminal.config(state="normal")
        self._terminal.insert("end", text, tag)
        self._terminal.see("end")
        self._terminal.config(state="disabled")

    def _log_info(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self._write(f"[{ts}] ", "ts")
        self._write(msg + "\n", "normal")

    def _log_ok(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self._write(f"[{ts}] ", "ts")
        self._write(msg + "\n", "ok")

    def _log_error(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self._write(f"[{ts}] ", "ts")
        self._write(msg + "\n", "error")

    def _log_divider(self, label=""):
        line = f"{'─' * 20} {label} {'─' * 20}" if label else "─" * 52
        self._write(line + "\n", "divider")

    def _clear_terminal(self):
        self._terminal.config(state="normal")
        self._terminal.delete("1.0", "end")
        self._terminal.config(state="disabled")

    # ── ComfyUI reminder ──────────────────────────────────────────────────────

    def _comfyui_reminder(self) -> bool:
        """Show reminder and return True if user confirms ComfyUI is running."""
        return messagebox.askyesno(
            "ComfyUI Check",
            "Is ComfyUI running on http://127.0.0.1:8000?\n\n"
            "Start it before continuing — the watcher will stall\n"
            "waiting for jobs if ComfyUI isn't up.\n\n"
            "Click YES to proceed, NO to cancel.",
            icon="warning",
        )

    # ── Button handlers ───────────────────────────────────────────────────────

    def _run_full(self):
        if not self._comfyui_reminder():
            return
        count = self._count_var.get()
        self._log_divider("FULL PIPELINE")
        self._log_info(f"Starting: Watcher → Upload → List  ({count} item(s))")
        self._start_sequence([
            ("Watcher",     [PYTHON, str(REPO / "watcher.py"), "--single-pass", "--count", str(count)], None),
            ("Upload",      ["node", "upload.auto.js"], str(NODE_DIR)),
            ("List",        ["node", "list.auto.js"],   str(NODE_DIR)),
        ], wait_before_list=True)

    def _run_upload(self):
        count = self._count_var.get()
        self._log_divider("UPLOAD + LIST")
        self._log_info(f"Starting: Upload → List  ({count} item(s))")
        self._start_sequence([
            ("Upload", ["node", "upload.auto.js", "--count", str(count)], str(NODE_DIR)),
            ("List",   ["node", "list.auto.js",   "--count", str(count)], str(NODE_DIR)),
        ])

    def _run_list(self):
        count = self._count_var.get()
        self._log_divider("LIST ONLY")
        self._log_info(f"Starting: List Only  ({count} draft(s))")
        self._start_sequence([
            ("List", ["node", "list.auto.js", "--count", str(count)], str(NODE_DIR)),
        ])

    def _stop(self):
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._log_error("Stopped by user.")
        self._set_running(False)

    # ── Process runner ────────────────────────────────────────────────────────

    def _start_sequence(self, steps, wait_before_list=False):
        """Run a list of (label, cmd, cwd) steps sequentially in a thread."""
        self._set_running(True)
        threading.Thread(
            target=self._run_sequence,
            args=(steps, wait_before_list),
            daemon=True,
        ).start()

    def _run_sequence(self, steps, wait_before_list=False):
        import time
        try:
            for i, (label, cmd, cwd) in enumerate(steps):

                # Wait between upload and list so nifty.ai can generate drafts
                if wait_before_list and label == "List":
                    self._queue.put(("info", "Waiting 2 min for nifty.ai to generate drafts…"))
                    for remaining in range(120, 0, -5):
                        if not self._running:
                            return
                        self._queue.put(("info", f"  {remaining}s remaining…"))
                        time.sleep(5)

                self._queue.put(("divider", label))
                self._queue.put(("info", f"Step {i+1}/{len(steps)}: {label}"))

                env = os.environ.copy()
                proc = subprocess.Popen(
                    cmd,
                    cwd=cwd or str(REPO),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env,
                    creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
                )
                self._proc = proc

                for line in proc.stdout:
                    if not self._running:
                        proc.terminate()
                        return
                    self._queue.put(("line", line.rstrip()))

                proc.wait()

                if proc.returncode != 0:
                    self._queue.put(("error", f"{label} failed (exit {proc.returncode})"))
                    self._queue.put(("status", "error"))
                    return
                else:
                    self._queue.put(("ok", f"{label} complete ✓"))

            self._queue.put(("ok", "All steps finished successfully."))
            self._queue.put(("status", "ok"))

        except Exception as e:
            self._queue.put(("error", f"Unexpected error: {e}"))
            self._queue.put(("status", "error"))
        finally:
            self._queue.put(("done", None))

    # ── Queue polling (main thread) ───────────────────────────────────────────

    def _poll_queue(self):
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "line":
                    # Colour-code based on content
                    low = payload.lower()
                    if any(k in low for k in ["error", "failed", "timeout"]):
                        tag = "error"
                    elif any(k in low for k in ["warning", "warn"]):
                        tag = "warn"
                    elif any(k in low for k in ["done", "complete", "saved", "✓"]):
                        tag = "ok"
                    else:
                        tag = "normal"
                    self._write(payload + "\n", tag)
                elif kind == "info":
                    self._log_info(payload)
                elif kind == "ok":
                    self._log_ok(payload)
                elif kind == "error":
                    self._log_error(payload)
                elif kind == "divider":
                    self._log_divider(payload)
                elif kind == "status":
                    if payload == "ok":
                        self._set_status(*STATUS_OK)
                    elif payload == "error":
                        self._set_status(*STATUS_ERROR)
                elif kind == "done":
                    self._set_running(False)
        except queue.Empty:
            pass
        self.after(80, self._poll_queue)

    # ── State helpers ─────────────────────────────────────────────────────────

    def _set_running(self, running: bool):
        self._running = running
        state_run  = "disabled" if running else "normal"
        state_stop = "normal"   if running else "disabled"
        for btn in self._all_run_btns:
            btn.config(state=state_run)
        self._btn_stop.config(state=state_stop)
        self._count_spin.config(state=state_run)
        if running:
            self._set_status(*STATUS_RUNNING)

    def _set_status(self, colour, text):
        self._status_lbl.config(fg=colour, text=text)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
