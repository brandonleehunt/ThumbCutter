#!/usr/bin/env python3
"""
ThumbCutter — drop in a video + captions, get headline thumbnails, Premiere-ready
cutdown XMLs, and a YouTube description + tags.

Run with:  python app.py
Requires:  Python 3.10+, ffmpeg/ffprobe on PATH, pip install -r requirements.txt
"""
import os
import sys
import zipfile
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, colorchooser
from tkinter import font as tkfont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core import cuts, thumbs, ai, settings, channel

APP_VERSION = "1.0.0"

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except Exception:
    HAS_DND = False

APP_TITLE = "ThumbCutter"
N_HEADLINES = 6
DEFAULT_TAGS = ["", "", "", "", "", ""]
DEFAULT_HEADLINES = [
    "The Truth About [TOPIC]",
    "Don't [DO THIS] Until You Watch This",
    "I Tried [THING] for 30 Days",
    "I Finally [DID THING]",
    "Can I Really [GOAL]?",
    "[TOPIC] Verdict: My Honest Take",
]

THEMES = {
    "dark": dict(bg="#14161a", panel="#1c1f26", field="#262a33", fg="#e8eaed", muted="#9aa0aa",
                 border="#363b47", accent="#e5484d", accent_hover="#f05a5f", accent_fg="#ffffff",
                 btn="#2c313c", btn_hover="#384050", select="#46506a",
                 log_bg="#0e1013", log_fg="#cfd3da"),
    "light": dict(bg="#eef0f3", panel="#ffffff", field="#ffffff", fg="#1b1e24", muted="#6b7280",
                  border="#cdd2da", accent="#d93a3f", accent_hover="#c42f34", accent_fg="#ffffff",
                  btn="#e6e8ed", btn_hover="#d9dce3", select="#bcd3ff",
                  log_bg="#f7f8fa", log_fg="#222222"),
}


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(rgb)


class LogBox(ttk.Frame):
    def __init__(self, master):
        super().__init__(master)
        self.text = tk.Text(self, height=7, wrap="word", state="disabled")
        sb = ttk.Scrollbar(self, command=self.text.yview)
        self.text.configure(yscrollcommand=sb.set)
        self.text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def log(self, msg):
        self.text.configure(state="normal")
        self.text.insert("end", msg + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")


class FilePicker(ttk.Frame):
    """Label on top, path field + Browse below (fits narrow columns). Drag-and-drop
    onto the field when tkinterdnd2 is available."""
    def __init__(self, master, label, filetypes, is_dir=False, on_change=None):
        super().__init__(master)
        self.var = tk.StringVar()
        self.filetypes = filetypes
        self.is_dir = is_dir
        self.on_change = on_change
        ttk.Label(self, text=label, style="Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(self)
        row.pack(fill="x")
        self.entry = ttk.Entry(row, textvariable=self.var)
        self.entry.pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self._browse).pack(side="left", padx=(6, 0))
        if HAS_DND:
            self.entry.drop_target_register(DND_FILES)
            self.entry.dnd_bind("<<Drop>>", self._on_drop)

    def _browse(self):
        p = filedialog.askdirectory() if self.is_dir else filedialog.askopenfilename(filetypes=self.filetypes)
        if p:
            self.var.set(p)
            if self.on_change:
                self.on_change()

    def _on_drop(self, event):
        self.var.set(event.data.strip("{}"))
        if self.on_change:
            self.on_change()

    def get(self):
        return self.var.get().strip()


class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.title("Settings")
        self.configure(bg=app.colors["panel"])
        self.resizable(False, False)
        self.transient(app.root)
        pad = dict(padx=12, pady=(8, 2))
        self.columnconfigure(0, weight=1)

        r = 0
        ttk.Label(self, text="Anthropic API key", style="Bold.TLabel").grid(row=r, column=0, sticky="w", **pad); r += 1
        keyrow = ttk.Frame(self)
        keyrow.grid(row=r, column=0, sticky="ew", padx=12); r += 1
        self.key_var = tk.StringVar(value=ai.get_api_key())
        key_entry = ttk.Entry(keyrow, textvariable=self.key_var, width=52, show="•")
        key_entry.pack(side="left", fill="x", expand=True)
        self.show_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(keyrow, text="show", variable=self.show_var,
                        command=lambda: key_entry.configure(show="" if self.show_var.get() else "•")
                        ).pack(side="left", padx=6)
        ttk.Label(self, text="Your own pay-as-you-go key from console.anthropic.com/settings/keys (separate\n"
                             "from a claude.ai subscription). Stored in plain text at:\n"
                             f"{ai.CONFIG_PATH}\nand only ever sent to api.anthropic.com.",
                  style="Muted.TLabel", justify="left").grid(row=r, column=0, sticky="w", padx=12); r += 1

        ttk.Label(self, text="Model", style="Bold.TLabel").grid(row=r, column=0, sticky="w", **pad); r += 1
        self.model_var = tk.StringVar(value=ai.get_model())
        ttk.Entry(self, textvariable=self.model_var, width=34).grid(row=r, column=0, sticky="w", padx=12); r += 1
        ttk.Label(self, text="If suggestions fail with a 'model not found' error, update this to the\n"
                             "current name from docs.claude.com.",
                  style="Muted.TLabel", justify="left").grid(row=r, column=0, sticky="w", padx=12); r += 1

        ttk.Label(self, text="YouTube channel", style="Bold.TLabel").grid(row=r, column=0, sticky="w", **pad); r += 1
        self.channel_var = tk.StringVar(value=settings.get("channel", ""))
        ttk.Entry(self, textvariable=self.channel_var, width=52).grid(row=r, column=0, sticky="ew", padx=12); r += 1
        ttk.Label(self, text="A @handle or channel URL. Used to find your two most recent regular uploads\n"
                             "(Shorts are skipped) for the end of each description.",
                  style="Muted.TLabel", justify="left").grid(row=r, column=0, sticky="w", padx=12); r += 1

        ttk.Label(self, text="Social links (one per line, added to every description)",
                  style="Bold.TLabel").grid(row=r, column=0, sticky="w", **pad); r += 1
        self.socials = tk.Text(self, height=7, width=58, wrap="none")
        self.socials.grid(row=r, column=0, sticky="ew", padx=12); r += 1
        self.socials.insert("1.0", settings.get("socials", ""))
        app.style_text(self.socials)
        ttk.Label(self, text="Written exactly as typed, e.g.  Instagram: https://instagram.com/yourname",
                  style="Muted.TLabel").grid(row=r, column=0, sticky="w", padx=12); r += 1

        btns = ttk.Frame(self)
        btns.grid(row=r, column=0, pady=12)
        ttk.Button(btns, text="Save", style="Accent.TButton", command=self._save).pack(side="left", padx=4)
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="left", padx=4)

    def _save(self):
        ai.set_api_key(self.key_var.get())
        ai.set_model(self.model_var.get())
        new_channel = self.channel_var.get().strip()
        if new_channel != settings.get("channel", ""):
            settings.set_value("channel_id", "")
        settings.set_value("channel", new_channel)
        settings.set_value("socials", self.socials.get("1.0", "end").strip())
        self.destroy()


class ThumbCutterApp:
    MIN_W, MIN_H = 1180, 640

    def __init__(self, root):
        self.root = root
        root.title(APP_TITLE)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{min(1440, sw - 80)}x{min(900, sh - 120)}")
        root.minsize(self.MIN_W, self.MIN_H)

        self.style = ttk.Style()
        try:
            self.style.theme_use("clam")
        except Exception:
            pass
        base = tkfont.nametofont("TkDefaultFont")
        self.f_bold = base.copy()
        self.f_bold.configure(weight="bold")
        self.f_text = base.copy()
        self.f_title = base.copy()
        self.f_title.configure(size=base.cget("size") + 7, weight="bold")

        theme = settings.get("theme", "dark")
        self.theme_name = theme if theme in THEMES else "dark"
        self.colors = THEMES[self.theme_name]
        self._text_widgets = []
        self._combos = []

        self.headline_vars = []
        self.tag_vars = []
        self.thumb_cache = {}   # (headline_idx, fmt) -> PIL Image
        self._needs_scroll = False
        self._sb_job = None

        self._build_shell()
        self._build_header()
        self._build_columns()
        self.apply_theme(self.theme_name)
        self._bind_wheel()
        self.log.log(f"{APP_TITLE} ready. " + ("Drag & drop enabled." if HAS_DND else
                      "Drag & drop isn't available in this Python install — use the Browse buttons "
                      "(pip install tkinterdnd2 to enable it)."))

    # ------------------------------------------------------------------ theme
    def style_text(self, widget):
        """Register a tk.Text so it follows the active theme."""
        self._text_widgets.append(widget)
        self._style_text_widget(widget)

    def _style_text_widget(self, w):
        c = self.colors
        try:
            w.configure(bg=c["field"], fg=c["fg"], insertbackground=c["fg"],
                        selectbackground=c["select"], selectforeground=c["fg"],
                        highlightthickness=1, highlightbackground=c["border"],
                        highlightcolor=c["accent"], relief="flat", bd=0, padx=6, pady=4,
                        font=self.f_text)
        except tk.TclError:
            pass  # widget was destroyed (e.g. a closed Settings dialog)

    def apply_theme(self, name):
        self.theme_name = name
        c = self.colors = THEMES[name]
        s = self.style
        self.root.configure(bg=c["bg"])
        self.canvas.configure(bg=c["bg"])

        s.configure(".", background=c["panel"], foreground=c["fg"], fieldbackground=c["field"],
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"],
                    troughcolor=c["bg"], focuscolor=c["panel"], insertcolor=c["fg"],
                    selectbackground=c["select"], selectforeground=c["fg"])
        s.configure("TFrame", background=c["panel"])
        s.configure("Page.TFrame", background=c["bg"])
        s.configure("TLabel", background=c["panel"], foreground=c["fg"])
        s.configure("Bold.TLabel", background=c["panel"], foreground=c["fg"], font=self.f_bold)
        s.configure("Muted.TLabel", background=c["panel"], foreground=c["muted"])
        s.configure("Title.TLabel", background=c["bg"], foreground=c["fg"], font=self.f_title)
        s.configure("Sub.TLabel", background=c["bg"], foreground=c["muted"])
        s.configure("TLabelframe", background=c["panel"], bordercolor=c["border"],
                    lightcolor=c["border"], darkcolor=c["border"], relief="solid", borderwidth=1)
        s.configure("TLabelframe.Label", background=c["panel"], foreground=c["fg"], font=self.f_bold)

        s.configure("TButton", background=c["btn"], foreground=c["fg"], bordercolor=c["border"],
                    lightcolor=c["btn"], darkcolor=c["btn"], focuscolor=c["btn"], padding=(10, 5))
        s.map("TButton", background=[("pressed", c["btn_hover"]), ("active", c["btn_hover"])],
              lightcolor=[("active", c["btn_hover"])], darkcolor=[("active", c["btn_hover"])])
        s.configure("Accent.TButton", background=c["accent"], foreground=c["accent_fg"],
                    bordercolor=c["accent"], lightcolor=c["accent"], darkcolor=c["accent"],
                    focuscolor=c["accent"], padding=(10, 6))
        s.map("Accent.TButton", background=[("pressed", c["accent_hover"]), ("active", c["accent_hover"])],
              lightcolor=[("active", c["accent_hover"])], darkcolor=[("active", c["accent_hover"])])

        s.configure("TCheckbutton", background=c["panel"], foreground=c["fg"], focuscolor=c["panel"],
                    indicatorbackground=c["field"], indicatorforeground=c["fg"],
                    upperbordercolor=c["border"], lowerbordercolor=c["border"])
        s.map("TCheckbutton", background=[("active", c["panel"])],
              indicatorbackground=[("selected", c["accent"]), ("!selected", c["field"])],
              indicatorforeground=[("selected", "#ffffff")])

        s.configure("TEntry", fieldbackground=c["field"], foreground=c["fg"], insertcolor=c["fg"],
                    bordercolor=c["border"], lightcolor=c["border"], darkcolor=c["border"], padding=4)
        s.map("TEntry", bordercolor=[("focus", c["accent"])], lightcolor=[("focus", c["accent"])],
              darkcolor=[("focus", c["accent"])])
        s.configure("TCombobox", fieldbackground=c["field"], foreground=c["fg"], background=c["btn"],
                    arrowcolor=c["fg"], bordercolor=c["border"], lightcolor=c["border"],
                    darkcolor=c["border"], padding=3)
        s.map("TCombobox", fieldbackground=[("readonly", c["field"])], foreground=[("readonly", c["fg"])],
              selectbackground=[("readonly", c["field"])], selectforeground=[("readonly", c["fg"])],
              background=[("active", c["btn_hover"])])
        s.configure("TSpinbox", fieldbackground=c["field"], foreground=c["fg"], background=c["btn"],
                    arrowcolor=c["fg"], bordercolor=c["border"], lightcolor=c["border"],
                    darkcolor=c["border"], insertcolor=c["fg"])
        s.configure("Vertical.TScrollbar", background=c["btn"], troughcolor=c["bg"],
                    bordercolor=c["bg"], lightcolor=c["btn"], darkcolor=c["btn"], arrowcolor=c["fg"])
        s.map("Vertical.TScrollbar", background=[("active", c["btn_hover"])])

        self.root.option_add("*TCombobox*Listbox.background", c["field"])
        self.root.option_add("*TCombobox*Listbox.foreground", c["fg"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", c["accent"])
        self.root.option_add("*TCombobox*Listbox.selectForeground", "#ffffff")
        for combo in self._combos:
            try:  # an already-created dropdown list ignores option_add, so restyle it directly
                pd = combo.tk.eval(f"ttk::combobox::PopdownWindow {combo}")
                combo.tk.call(f"{pd}.f.l", "configure", "-background", c["field"],
                              "-foreground", c["fg"], "-selectbackground", c["accent"],
                              "-selectforeground", "#ffffff")
            except tk.TclError:
                pass

        for w in self._text_widgets:
            self._style_text_widget(w)
        self.log.text.configure(bg=c["log_bg"], fg=c["log_fg"], insertbackground=c["log_fg"],
                                highlightthickness=1, highlightbackground=c["border"],
                                relief="flat", bd=0, padx=6, pady=4)
        self.preview_label.configure(bg=c["field"], fg=c["muted"])
        self.theme_btn.configure(text="Light mode" if name == "dark" else "Dark mode")

    def on_toggle_theme(self):
        new = "light" if self.theme_name == "dark" else "dark"
        self.apply_theme(new)
        settings.set_value("theme", new)

    # ----------------------------------------------------- scrollable shell
    def _build_shell(self):
        outer = ttk.Frame(self.root, style="Page.TFrame")
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(0, weight=1)
        self.canvas = tk.Canvas(outer, highlightthickness=0, bd=0, yscrollincrement=24)
        self.vsb = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vsb.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        self.vsb.grid(row=0, column=1, sticky="ns")
        self.vsb.grid_remove()   # only shown when the page is taller than the window
        self.page = ttk.Frame(self.canvas, style="Page.TFrame", padding=(14, 10, 14, 14))
        self._page_id = self.canvas.create_window((0, 0), window=self.page, anchor="nw")
        self.page.bind("<Configure>", self._on_page_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_canvas_configure(self, e):
        self.canvas.itemconfigure(self._page_id, width=e.width)
        self._schedule_scrollbar_update()

    def _on_page_configure(self, _e):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._schedule_scrollbar_update()

    def _schedule_scrollbar_update(self):
        if self._sb_job is None:
            self._sb_job = self.root.after_idle(self._update_scrollbar)

    def _update_scrollbar(self):
        self._sb_job = None
        bbox = self.canvas.bbox("all")
        content_h = (bbox[3] - bbox[1]) if bbox else 0
        view_h = self.canvas.winfo_height()
        showing = bool(self.vsb.winfo_ismapped())
        # hysteresis so the bar can't flap on/off when the heights are nearly equal
        need = content_h > view_h + 1 if not showing else content_h > view_h - 24
        self._needs_scroll = need
        if need and not showing:
            self.vsb.grid()
        elif not need and showing:
            self.vsb.grid_remove()
            self.canvas.yview_moveto(0)

    def _bind_wheel(self):
        self.root.bind_all("<MouseWheel>", self._on_wheel)   # Windows + macOS
        self.root.bind_all("<Button-4>", self._on_wheel)     # Linux
        self.root.bind_all("<Button-5>", self._on_wheel)

    def _on_wheel(self, e):
        if not self._needs_scroll:
            return
        try:
            cls = e.widget.winfo_class()
        except Exception:
            cls = ""
        if cls in ("Text", "Listbox", "TCombobox", "TSpinbox"):
            return  # let those widgets handle their own scrolling
        if getattr(e, "num", None) == 4:
            step = -1
        elif getattr(e, "num", None) == 5:
            step = 1
        else:
            step = -1 if e.delta > 0 else 1
        self.canvas.yview_scroll(step * 3, "units")

    # ---------------------------------------------------------------- layout
    def _section(self, parent, title):
        lf = ttk.LabelFrame(parent, text=title, padding=10)
        lf.pack(fill="x", pady=(0, 10))
        return lf

    def _combo(self, parent, **kw):
        cb = ttk.Combobox(parent, state="readonly", **kw)
        self._combos.append(cb)
        return cb

    def _build_header(self):
        head = ttk.Frame(self.page, style="Page.TFrame")
        head.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 10))
        left = ttk.Frame(head, style="Page.TFrame")
        left.pack(side="left")
        ttk.Label(left, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        ttk.Label(left, text="Thumbnails, short-form cutdowns and YouTube copy from your transcript",
                  style="Sub.TLabel").pack(anchor="w")
        right = ttk.Frame(head, style="Page.TFrame")
        right.pack(side="right")
        ttk.Button(right, text="Settings…", command=self.on_open_settings).pack(side="left", padx=4)
        self.theme_btn = ttk.Button(right, text="Light mode", command=self.on_toggle_theme)
        self.theme_btn.pack(side="left", padx=4)

    def _build_columns(self):
        for i in range(3):
            self.page.columnconfigure(i, weight=1, uniform="cols")
        cols = []
        for i in range(3):
            f = ttk.Frame(self.page, style="Page.TFrame")
            f.grid(row=1, column=i, sticky="new", padx=(0 if i == 0 else 7, 0 if i == 2 else 7))
            cols.append(f)
        c0, c1, c2 = cols

        # ============ column 1: content + headlines ============
        sec = self._section(c0, "1. Your Content" + ("  (drag & drop works)" if HAS_DND else ""))
        self.pick_video = FilePicker(sec, "Video file", [("Video", "*.mp4 *.mov *.mkv")])
        self.pick_video.pack(fill="x", pady=3)
        self.pick_srt = FilePicker(sec, "Captions (.srt)", [("SubRip", "*.srt")])
        self.pick_srt.pack(fill="x", pady=3)
        self.pick_face = FilePicker(sec, "Your face (transparent .png)", [("PNG", "*.png")])
        self.pick_face.pack(fill="x", pady=3)
        self.flip_face = tk.BooleanVar(value=False)
        ttk.Checkbutton(sec, text="Flip face horizontally (if it uploaded backwards)",
                        variable=self.flip_face).pack(anchor="w")
        self.pick_prop = FilePicker(sec, "Prop image, e.g. a book cover (optional)",
                                    [("Image", "*.png *.jpg *.jpeg")])
        self.pick_prop.pack(fill="x", pady=3)

        sec = self._section(c0, "2. Headlines")
        ttk.Label(sec, text="Edit freely. They drive the thumbnails and which clips get cut.",
                  style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Button(sec, text="✨ Reprompt All with Claude", command=self.on_suggest_headlines
                   ).pack(fill="x", pady=(0, 8))
        for i in range(N_HEADLINES):
            hv = tk.StringVar(value=DEFAULT_HEADLINES[i])
            tv = tk.StringVar(value=DEFAULT_TAGS[i])
            row = ttk.Frame(sec)
            row.pack(fill="x", pady=(3, 0))
            ttk.Label(row, text=f"{i+1}.", width=2).pack(side="left")
            ttk.Entry(row, textvariable=hv).pack(side="left", fill="x", expand=True)
            ttk.Button(row, text="🔄", width=3,
                       command=lambda idx=i: self.on_reprompt_one(idx)).pack(side="left", padx=(4, 0))
            row2 = ttk.Frame(sec)
            row2.pack(fill="x", pady=(2, 3))
            ttk.Label(row2, text="tag", style="Muted.TLabel", width=4).pack(side="left", padx=(14, 0))
            ttk.Entry(row2, textvariable=tv).pack(side="left", fill="x", expand=True)
            self.headline_vars.append(hv)
            self.tag_vars.append(tv)

        # ============ column 2: thumbnail style + preview ============
        sec = self._section(c1, "3. Thumbnail Style")
        self.fmt_vars = {k: tk.BooleanVar(value=True) for k in thumbs.FORMATS}
        labels = {"youtube_wide": "YouTube wide (1280×720)",
                  "instagram_feed": "Instagram feed (1080×1350)",
                  "instagram_story": "Instagram / TikTok story (1080×1920)"}
        for k, v in self.fmt_vars.items():
            ttk.Checkbutton(sec, text=labels[k], variable=v).pack(anchor="w")
        color_row = ttk.Frame(sec)
        color_row.pack(fill="x", pady=(8, 0))
        ttk.Label(color_row, text="Background color").pack(side="left")
        self.bg_color = thumbs.DEFAULT_BG
        self.color_swatch = tk.Label(color_row, text="      ", background=_hex(self.bg_color),
                                     relief="flat", bd=0, highlightthickness=1,
                                     highlightbackground="#888888")
        self.color_swatch.pack(side="left", padx=8)
        ttk.Button(color_row, text="Choose…", command=self.on_pick_color).pack(side="left")
        ttk.Button(color_row, text="Reset", command=self.on_reset_color).pack(side="left", padx=4)
        ttk.Button(sec, text="Generate Thumbnails", style="Accent.TButton",
                   command=self.on_generate_thumbs).pack(fill="x", pady=(10, 0))

        sec = self._section(c1, "4. Preview")
        sel = ttk.Frame(sec)
        sel.pack(fill="x")
        ttk.Label(sel, text="Headline").pack(side="left")
        self.headline_combo = self._combo(sel, values=[str(i + 1) for i in range(N_HEADLINES)], width=3)
        self.headline_combo.current(0)
        self.headline_combo.pack(side="left", padx=(4, 10))
        self.headline_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_preview())
        ttk.Label(sel, text="Format").pack(side="left")
        self.preview_fmt = tk.StringVar(value="youtube_wide")
        self.fmt_combo = self._combo(sel, values=list(thumbs.FORMATS.keys()), width=16,
                                     textvariable=self.preview_fmt)
        self.fmt_combo.pack(side="left", padx=4)
        self.fmt_combo.bind("<<ComboboxSelected>>", lambda e: self._refresh_preview())
        self.preview_label = tk.Label(sec, text="(generate thumbnails to preview them here)",
                                      height=18)
        self.preview_label.pack(fill="x", pady=(8, 0))

        sec = self._section(c1, "5. Cutdowns")
        self.want_headline_cuts = tk.BooleanVar(value=True)
        self.want_full_cut = tk.BooleanVar(value=True)
        ttk.Checkbutton(sec, text="One vertical sequence per headline",
                        variable=self.want_headline_cuts).pack(anchor="w")
        row = ttk.Frame(sec)
        row.pack(fill="x", pady=(2, 0))
        ttk.Checkbutton(row, text="Full cutdown under", variable=self.want_full_cut).pack(side="left")
        self.target_seconds = tk.IntVar(value=170)
        ttk.Spinbox(row, from_=30, to=180, textvariable=self.target_seconds, width=4).pack(side="left", padx=4)
        ttk.Label(row, text="seconds").pack(side="left")
        ttk.Label(sec, text="Cuts snap to sentence ends so they don't land mid-dialogue.",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

        # ============ column 3: description + export ============
        sec = self._section(c2, "6. YouTube Description & Tags")
        ttk.Button(sec, text="✨ Generate Description & Tags with Claude", style="Accent.TButton",
                   command=self.on_suggest_description).pack(fill="x")
        ttk.Label(sec, text="Description teaser (3–4 sentences)", style="Bold.TLabel"
                  ).pack(anchor="w", pady=(8, 2))
        self.desc_text = tk.Text(sec, height=5, wrap="word")
        self.desc_text.pack(fill="x")
        self.style_text(self.desc_text)
        ttk.Label(sec, text="Hashtags (max 3)", style="Muted.TLabel").pack(anchor="w", pady=(6, 2))
        self.hashtags_var = tk.StringVar()
        ttk.Entry(sec, textvariable=self.hashtags_var).pack(fill="x")

        foot_head = ttk.Frame(sec)
        foot_head.pack(fill="x", pady=(8, 2))
        ttk.Label(foot_head, text="Footer: recent uploads + social links", style="Bold.TLabel"
                  ).pack(side="left")
        ttk.Button(foot_head, text="↻ Refresh uploads", command=self.on_refresh_footer
                   ).pack(side="right")
        self.footer_text = tk.Text(sec, height=9, wrap="word")
        self.footer_text.pack(fill="x")
        self.style_text(self.footer_text)
        self.include_footer = tk.BooleanVar(value=True)
        ttk.Checkbutton(sec, text="Include footer in the description", variable=self.include_footer
                        ).pack(anchor="w", pady=(4, 0))

        tags_head = ttk.Frame(sec)
        tags_head.pack(fill="x", pady=(8, 2))
        ttk.Label(tags_head, text="Tags (comma-separated)", style="Bold.TLabel").pack(side="left")
        self.tag_count_label = ttk.Label(tags_head, text="0 / 500", style="Muted.TLabel")
        self.tag_count_label.pack(side="right")
        self.tags_var = tk.StringVar()
        self.tags_var.trace_add("write", lambda *_: self._update_tag_count())
        ttk.Entry(sec, textvariable=self.tags_var).pack(fill="x")

        copy_row = ttk.Frame(sec)
        copy_row.pack(fill="x", pady=(10, 0))
        ttk.Button(copy_row, text="Copy Description", command=self.on_copy_description
                   ).pack(side="left", expand=True, fill="x", padx=(0, 3))
        ttk.Button(copy_row, text="Copy Tags", command=self.on_copy_tags
                   ).pack(side="left", expand=True, fill="x", padx=(3, 0))

        sec = self._section(c2, "7. Export")
        self.pick_out = FilePicker(sec, "Output folder", [], is_dir=True)
        self.pick_out.pack(fill="x", pady=(0, 8))
        ttk.Button(sec, text="Export Both (thumbnails + XML + description)", style="Accent.TButton",
                   command=lambda: self.on_export("both")).pack(fill="x")
        r2 = ttk.Frame(sec)
        r2.pack(fill="x", pady=(6, 0))
        ttk.Button(r2, text="Thumbnails Only", command=lambda: self.on_export("thumbs")
                   ).pack(side="left", expand=True, fill="x", padx=(0, 3))
        ttk.Button(r2, text="XML + Captions Only", command=lambda: self.on_export("xml")
                   ).pack(side="left", expand=True, fill="x", padx=(3, 0))

        # ============ activity log across the bottom ============
        logf = ttk.LabelFrame(self.page, text="Activity", padding=8)
        logf.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(4, 0))
        self.log = LogBox(logf)
        self.log.pack(fill="both", expand=True)

    # ------------------------------------------------------- thread plumbing
    def _log(self, msg):
        """Thread-safe: Tk must only be touched from the main thread."""
        self.root.after(0, self.log.log, msg)

    def _headlines(self):
        return [(hv.get().strip(), tv.get().strip()) for hv, tv in zip(self.headline_vars, self.tag_vars)]

    def _srt_or_warn(self, why):
        srt = self.pick_srt.get()
        if not srt or not os.path.exists(srt):
            messagebox.showwarning(APP_TITLE, f"Load an .srt transcript first. {why}")
            return None
        return srt

    def _key_or_prompt(self):
        if ai.get_api_key():
            return True
        if messagebox.askyesno(APP_TITLE, "No Anthropic API key set yet. Open Settings now?"):
            self.on_open_settings()
        return False

    # --------------------------------------------------------------- actions
    def on_pick_color(self):
        rgb, _ = colorchooser.askcolor(color=_hex(self.bg_color), title="Thumbnail background color")
        if rgb:
            self.bg_color = tuple(int(c) for c in rgb)
            self.color_swatch.configure(background=_hex(self.bg_color))

    def on_reset_color(self):
        self.bg_color = thumbs.DEFAULT_BG
        self.color_swatch.configure(background=_hex(self.bg_color))

    def on_open_settings(self):
        SettingsDialog(self)

    # ---- headlines
    def on_suggest_headlines(self):
        srt = self._srt_or_warn("Claude reads that to suggest headlines; it doesn't watch the video.")
        if not srt or not self._key_or_prompt():
            return
        self.log.log("Asking Claude for headline suggestions from the transcript…")
        threading.Thread(target=self._suggest_worker, args=(srt,), daemon=True).start()

    def _suggest_worker(self, srt):
        try:
            transcript = " ".join(tx for _, _, _, tx in cuts.parse_srt(srt))
            suggestions = ai.suggest_headlines(transcript, n=N_HEADLINES)
            self.root.after(0, self._apply_suggestions, suggestions)
        except Exception as e:
            msg = str(e)
            self._log("ERROR getting suggestions: " + msg)
            self.root.after(0, lambda: messagebox.showerror(APP_TITLE, msg))

    def _apply_suggestions(self, suggestions):
        for i, (h, t) in enumerate(suggestions[:N_HEADLINES]):
            self.headline_vars[i].set(h)
            self.tag_vars[i].set(t)
        self.log.log(f"Filled in {len(suggestions)} headline suggestion(s) from Claude. "
                     "Review and edit them before generating thumbnails.")

    def on_reprompt_one(self, idx):
        srt = self._srt_or_warn("")
        if not srt or not self._key_or_prompt():
            return
        existing = [h for h, _ in self._headlines() if h]   # read Tk vars on the main thread
        self.log.log(f"Asking Claude for a new headline #{idx+1}…")
        threading.Thread(target=self._reprompt_one_worker, args=(srt, idx, existing), daemon=True).start()

    def _reprompt_one_worker(self, srt, idx, existing):
        try:
            transcript = " ".join(tx for _, _, _, tx in cuts.parse_srt(srt))
            suggestions = ai.suggest_headlines(transcript, n=1, avoid=existing)
            if suggestions:
                self.root.after(0, self._apply_one, idx, suggestions[0])
        except Exception as e:
            msg = str(e)
            self._log(f"ERROR reprompting headline #{idx+1}: " + msg)
            self.root.after(0, lambda: messagebox.showerror(APP_TITLE, msg))

    def _apply_one(self, idx, suggestion):
        h, t = suggestion
        self.headline_vars[idx].set(h)
        self.tag_vars[idx].set(t)
        self.log.log(f"Headline #{idx+1} replaced: {h}")

    # ---- description / tags / footer
    @staticmethod
    def _set_text(widget, text):
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _update_tag_count(self):
        tags = [t.strip() for t in self.tags_var.get().split(",") if t.strip()]
        n = ai.tags_length(tags)
        over = n > ai.TAGS_CHAR_LIMIT
        self.tag_count_label.configure(text=f"{len(tags)} tags · {n} / {ai.TAGS_CHAR_LIMIT}"
                                       + ("  (too long!)" if over else ""),
                                       foreground="#e5484d" if over else self.colors["muted"])

    def _full_description(self):
        parts = [self.desc_text.get("1.0", "end").strip(), self.hashtags_var.get().strip()]
        if self.include_footer.get():
            parts.append(self.footer_text.get("1.0", "end").strip())
        return "\n\n".join(p for p in parts if p)

    def _fetch_recent_uploads(self):
        """Background-thread safe: reads settings only, never Tk widgets."""
        chan = settings.get("channel", "")
        cid = settings.get("channel_id") if settings.get("channel_id_for") == chan else None
        videos, cid = channel.latest_long_form(chan, count=2, channel_id=cid)
        settings.set_value("channel_id", cid)
        settings.set_value("channel_id_for", chan)
        return videos

    def on_suggest_description(self):
        srt = self._srt_or_warn("Claude reads that to write the description and tags.")
        if not srt or not self._key_or_prompt():
            return
        fetch = self.include_footer.get()
        self.log.log("Asking Claude for a description teaser and tags…"
                     + (" Also fetching your latest uploads." if fetch else ""))
        threading.Thread(target=self._description_worker, args=(srt, fetch), daemon=True).start()

    def _description_worker(self, srt, fetch_footer):
        try:
            transcript = " ".join(tx for _, _, _, tx in cuts.parse_srt(srt))
            description, tags, hashtags = ai.suggest_description_and_tags(transcript)
        except Exception as e:
            msg = str(e)
            self._log("ERROR getting description/tags: " + msg)
            self.root.after(0, lambda: messagebox.showerror(APP_TITLE, msg))
            return
        footer = None
        if fetch_footer:
            try:
                footer = channel.build_footer(self._fetch_recent_uploads(), settings.get("socials", ""))
            except Exception as e:
                footer = channel.build_footer([], settings.get("socials", ""))
                self._log(f"Couldn't fetch your latest uploads ({e}). The footer has your social "
                          "links only. Click 'Refresh uploads' to retry.")
        self.root.after(0, self._apply_description, description, tags, hashtags, footer)

    def _apply_description(self, description, tags, hashtags, footer):
        self._set_text(self.desc_text, description)
        self.hashtags_var.set(" ".join(hashtags))
        self.tags_var.set(", ".join(tags))
        if footer is not None:
            self._set_text(self.footer_text, footer)
        self.log.log(f"Filled in a description teaser, {len(tags)} tag(s) and "
                     f"{len(hashtags)} hashtag(s). Review and edit before you post.")

    def on_refresh_footer(self):
        self.log.log("Fetching your two most recent uploads (Shorts skipped)…")
        threading.Thread(target=self._footer_worker, daemon=True).start()

    def _footer_worker(self):
        try:
            videos = self._fetch_recent_uploads()
        except Exception as e:
            # leave the footer exactly as it is so a failed refresh never wipes your edits
            self._log(f"Couldn't fetch your latest uploads: {e}")
            return
        footer = channel.build_footer(videos, settings.get("socials", ""))
        self._log("Latest uploads: " + "; ".join(v["title"] for v in videos)
                  if videos else "No regular uploads found on that channel.")
        self.root.after(0, lambda: self._set_text(self.footer_text, footer))

    def _copy(self, text, what):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log.log(f"Copied {what} to the clipboard.")

    def on_copy_description(self):
        self._copy(self._full_description(), "the description")

    def on_copy_tags(self):
        self._copy(", ".join(t.strip() for t in self.tags_var.get().split(",") if t.strip()), "the tags")

    # ---- thumbnails
    def on_generate_thumbs(self):
        face = self.pick_face.get() or None
        prop = self.pick_prop.get() or None
        if not face and not messagebox.askyesno(
                APP_TITLE, "No face PNG selected — generate placeholder thumbnails without a face?"):
            return
        fmts = [k for k, v in self.fmt_vars.items() if v.get()]
        if not fmts:
            messagebox.showwarning(APP_TITLE, "Pick at least one thumbnail format.")
            return
        self.thumb_cache.clear()
        try:
            for i, (h, tag) in enumerate(self._headlines()):
                if not h:
                    continue
                for fmt in fmts:
                    self.thumb_cache[(i, fmt)] = thumbs.render_thumbnail(
                        fmt, h, tag=tag or None, face_path=face, prop_path=prop,
                        bg_color=self.bg_color, flip_face=self.flip_face.get())
            self.log.log(f"Generated {len(self.thumb_cache)} thumbnail(s).")
            self._refresh_preview()
        except Exception as e:
            self.log.log("ERROR generating thumbnails: " + str(e))
            messagebox.showerror(APP_TITLE, str(e))

    def _refresh_preview(self):
        try:
            i = int(self.headline_combo.get()) - 1
        except Exception:
            return
        im = self.thumb_cache.get((i, self.preview_fmt.get()))
        if im is None:
            self.preview_label.configure(image="", text="(not generated yet)", height=18)
            return
        from PIL import ImageTk
        disp = im.copy()
        disp.thumbnail((470, 470))
        self._tkimg = ImageTk.PhotoImage(disp)
        self.preview_label.configure(image=self._tkimg, text="", height=disp.height)

    # ---- export
    def on_export(self, scope="both"):
        """scope: 'thumbs', 'xml', or 'both'."""
        out = self.pick_out.get()
        if not out:
            messagebox.showwarning(APP_TITLE, "Choose an output folder first.")
            return
        if scope in ("thumbs", "both") and not self.thumb_cache:
            if scope == "thumbs":
                messagebox.showwarning(APP_TITLE, "No thumbnails generated yet — click "
                                       "'Generate Thumbnails' first.")
                return
            if not messagebox.askyesno(APP_TITLE, "No thumbnails generated yet — export cutdowns only?"):
                return
        # Read every Tk value here, on the main thread, then hand plain data to the worker.
        opts = dict(
            out=out, scope=scope, video=self.pick_video.get(), srt=self.pick_srt.get(),
            headlines=[h for h, _ in self._headlines() if h],
            want_headline_cuts=self.want_headline_cuts.get(), want_full_cut=self.want_full_cut.get(),
            target_seconds=self.target_seconds.get(), thumbs=dict(self.thumb_cache),
            description=self._full_description(),
            tags=[t.strip() for t in self.tags_var.get().split(",") if t.strip()],
        )
        threading.Thread(target=self._export_worker, args=(opts,), daemon=True).start()

    def _export_worker(self, o):
        out, scope, srt, video = o["out"], o["scope"], o["srt"], o["video"]
        try:
            os.makedirs(out, exist_ok=True)

            if scope in ("thumbs", "both") and o["thumbs"]:
                thumb_dir = os.path.join(out, "thumbnails")
                os.makedirs(thumb_dir, exist_ok=True)
                for (i, fmt), im in o["thumbs"].items():
                    im.save(os.path.join(thumb_dir, f"{i+1:02d}_{fmt}.png"))
                self._log(f"Saved {len(o['thumbs'])} thumbnails to {thumb_dir}")

            if scope in ("xml", "both") and srt and os.path.exists(srt) and \
                    (o["want_headline_cuts"] or o["want_full_cut"]):
                cue_list = cuts.parse_srt(srt)
                headlines = o["headlines"]
                sequences = []
                if o["want_headline_cuts"]:
                    for i, h in enumerate(headlines):
                        segs = cuts.pick_segments_for_headline(cue_list, h)
                        if segs:
                            sequences.append((f"Short {i+1:02d} - {h[:40]}", segs))
                            self._log(f"Headline {i+1}: {len(segs)} clip(s), "
                                      f"{sum(s['end']-s['start'] for s in segs):.0f}s")
                if o["want_full_cut"]:
                    full = cuts.pick_full_cutdown(cue_list, headlines, target_seconds=o["target_seconds"])
                    if full:
                        sequences.append(("Full Cutdown - Under 3min", full))
                        self._log(f"Full cutdown: {len(full)} clip(s), "
                                  f"{sum(s['end']-s['start'] for s in full):.0f}s")

                if sequences and video and os.path.exists(video):
                    try:
                        info = cuts.probe_video(video)
                    except Exception as e:
                        info = None
                        self._log("Couldn't read the video with ffprobe, so cutdowns.xml was skipped "
                                  "(is ffmpeg installed and on your PATH?). Captions and the rest "
                                  f"will still export. Detail: {e}")
                    if info:
                        xml = cuts.build_xmeml(os.path.basename(video), info["fps"], info["width"],
                                               info["height"], sequences)
                        xml_path = os.path.join(out, "cutdowns.xml")
                        with open(xml_path, "w", encoding="utf-8") as f:
                            f.write(xml)
                        self._log(f"Wrote Premiere XML: {xml_path}")
                    cap_dir = os.path.join(out, "captions")
                    os.makedirs(cap_dir, exist_ok=True)
                    for name, segs in sequences:
                        safe = "".join(ch if ch.isalnum() else "_" for ch in name).strip("_")
                        with open(os.path.join(cap_dir, safe + ".srt"), "w", encoding="utf-8") as f:
                            f.write(cuts.build_srt(cue_list, segs))
                    self._log(f"Wrote {len(sequences)} caption file(s) to {cap_dir}")
                elif sequences:
                    self._log("Skipped XML export — select the video file to generate it.")
                else:
                    self._log("No clips matched any headline — none of the headline words appear "
                              "in the transcript. Reword the headlines to use phrases you actually "
                              "say in the video, then export again.")
            elif scope in ("xml", "both") and not (srt and os.path.exists(srt)):
                self._log("Skipped cutdowns — load an .srt transcript first.")

            if o["description"] or o["tags"]:
                lines = []
                if o["description"]:
                    lines.append(o["description"])
                if o["tags"]:
                    lines += ["", "Tags: " + ", ".join(o["tags"])]
                with open(os.path.join(out, "youtube_description.txt"), "w", encoding="utf-8") as f:
                    f.write("\n".join(lines))
                self._log(f"Wrote youtube_description.txt ({len(o['tags'])} tag(s)).")

            zpath = os.path.join(out, "ThumbCutter_export.zip")
            with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
                for root_, _, files in os.walk(out):
                    for fn in files:
                        if fn == "ThumbCutter_export.zip":
                            continue
                        full = os.path.join(root_, fn)
                        z.write(full, os.path.relpath(full, out))
            self._log(f"Packaged everything into {zpath}")
            self._log("Done.")
        except Exception as e:
            self._log("ERROR: " + str(e))
            self._log(traceback.format_exc())


def selftest():
    """Headless smoke test used by CI and after packaging: `ThumbCutter --selftest`."""
    import tempfile
    from core import thumbs
    try:
        import tkinter  # noqa: F401
        im = thumbs.render_thumbnail("youtube_wide", "Self Test Headline", tag="OK")
        assert im.size == (1280, 720), im.size
        with tempfile.TemporaryDirectory() as d:
            srt = os.path.join(d, "t.srt")
            with open(srt, "w", encoding="utf-8") as f:
                f.write("1\n00:00:00,000 --> 00:00:04,000\nHello there, this is a test.\n\n"
                        "2\n00:00:04,500 --> 00:00:09,000\nSecond line about the test.\n")
            cue_list = cuts.parse_srt(srt)
            assert len(cue_list) == 2
            xml = cuts.build_xmeml("t.mp4", 30, 1920, 1080, [("Seq", [{"start": 0.0, "end": 4.0}])])
            assert "<xmeml" in xml
        print(f"ThumbCutter {APP_VERSION} selftest OK")
        return 0
    except Exception as e:
        print("selftest FAILED:", repr(e))
        return 1


def main():
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    ThumbCutterApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
