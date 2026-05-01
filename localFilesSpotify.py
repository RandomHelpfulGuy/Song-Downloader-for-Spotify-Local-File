# Dependencies: pip install yt-dlp Pillow mutagen customtkinter pyinstaller
# Also requires ffmpeg on PATH: https://ffmpeg.org/download.html
# Build exe: pyinstaller --onefile --windowed --collect-all customtkinter --icon icon.ico --add-data "icon.ico;." --name "LocalFilesSpotify" localFilesSpotify.py

import ctypes
import io
import re
import os
import shutil
import subprocess
import sys
import threading
import tkinter as tk
import tkinter.messagebox as mb
from tkinter import filedialog
import urllib.request
import winsound
from pathlib import Path

import customtkinter as ctk
from PIL import Image
from mutagen.id3 import ID3, APIC, TPE1, TPE2, TIT2, error as ID3Error
import yt_dlp

TARGET_SIZE = 1600
OUTPUT_DIR  = Path("downloads")

SPOTIFY_GREEN = "#1DB954"
BG_DARK       = "#121212"
BG_CARD       = "#1E1E1E"
BG_INPUT      = "#2A2A2A"
TEXT_MAIN     = "#FFFFFF"
TEXT_DIM      = "#B3B3B3"
THUMB_SIZE    = 120


# ── Core logic ────────────────────────────────────────────────────────────────

def process_thumbnail(img: Image.Image, size: int = TARGET_SIZE, crop: bool = True) -> Image.Image:
    img = img.convert("RGB")
    if crop:
        w, h = img.size
        side = min(w, h)
        left = (w - side) // 2
        top  = (h - side) // 2
        img  = img.crop((left, top, left + side, top + side))
    return img.resize((size, size), Image.LANCZOS)


def process_thumbnail_path(image_path: Path, size: int = TARGET_SIZE, crop: bool = True) -> Image.Image:
    return process_thumbnail(Image.open(image_path), size, crop)


def clean_title(stem: str, artists: list[str]) -> str:
    title = stem
    for artist in artists:
        title = re.sub(re.escape(artist), "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s*\(.*?\)\s*", " ", title)
    title = re.sub(r"\s*-\s*", " ", title)
    return re.sub(r"\s+", " ", title).strip()


def embed_tags(mp3_path: str, thumbnail_img: Image.Image, artists: list[str], title: str) -> None:
    img_bytes = io.BytesIO()
    thumbnail_img.save(img_bytes, format="JPEG", quality=90)
    img_bytes.seek(0)

    try:
        tags = ID3(mp3_path)
    except ID3Error:
        tags = ID3()

    artist_str = ", ".join(artists)
    tags.delall("APIC")
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=img_bytes.read()))
    tags.add(TPE1(encoding=3, text=artist_str))
    tags.add(TPE2(encoding=3, text=artist_str))
    tags.add(TIT2(encoding=3, text=title))
    tags.save(mp3_path, v2_version=3)


def find_thumbnail(stem: str, directory: Path) -> Path | None:
    for ext in ("jpg", "webp", "png"):
        candidate = directory / f"{stem}.{ext}"
        if candidate.exists():
            return candidate
    return None


# ── Tag input widget ──────────────────────────────────────────────────────────

class TagInput(ctk.CTkFrame):
    """Gmail-style chip/tag input. Press comma or Enter to create a tag."""

    def __init__(self, parent, placeholder: str = "Type name and press comma...", **kwargs):
        super().__init__(parent, fg_color=BG_INPUT, corner_radius=8, **kwargs)
        self._tags: list[str] = []
        self._widgets: dict[str, tk.Frame] = {}

        self._text = tk.Text(
            self, height=2, wrap="word",
            bg=BG_INPUT, fg=TEXT_MAIN,
            insertbackground=TEXT_MAIN,
            font=("Segoe UI", 12),
            relief="flat", bd=0,
            highlightthickness=0,
            padx=8, pady=8,
        )
        self._text.pack(fill="both", expand=True)
        self._text.bind("<Key>",       self._on_key,       add=True)
        self._text.bind("<BackSpace>", self._on_backspace, add=True)

        self._placeholder = placeholder
        self._showing_placeholder = False
        self._show_placeholder()
        self._text.bind("<FocusIn>",  self._hide_placeholder,       add=True)
        self._text.bind("<FocusOut>", self._maybe_show_placeholder, add=True)

    def _show_placeholder(self):
        if not self._tags and not self._typed():
            self._text.insert("1.0", self._placeholder)
            self._text.configure(fg="#6A6A6A")
            self._showing_placeholder = True

    def _hide_placeholder(self, _=None):
        if self._showing_placeholder:
            self._text.delete("1.0", "end")
            self._text.configure(fg=TEXT_MAIN)
            self._showing_placeholder = False

    def _maybe_show_placeholder(self, _=None):
        if not self._tags and not self._typed():
            self._show_placeholder()

    def _typed(self) -> str:
        if self._showing_placeholder:
            return ""
        parts = [v for k, v, _ in self._text.dump("1.0", "end", text=True)]
        return "".join(parts).strip()

    def _delete_typed(self):
        ranges = [(pos, f"{pos}+{len(v)}c")
                  for k, v, pos in self._text.dump("1.0", "end", text=True)]
        for start, end in reversed(ranges):
            self._text.delete(start, end)

    def _commit(self):
        self._hide_placeholder()
        text = self._typed()
        if not text:
            return
        self._delete_typed()
        self._add_tag(text)

    def _add_tag(self, text: str):
        if not text or text in self._tags:
            return
        self._tags.append(text)

        outer = tk.Frame(self._text, bg=SPOTIFY_GREEN, bd=0, pady=1)
        tk.Label(outer, text=text, bg=SPOTIFY_GREEN, fg="black",
                 font=("Segoe UI", 11), padx=5, pady=1).pack(side="left")
        tk.Button(outer, text="×", bg=SPOTIFY_GREEN, fg="black",
                  font=("Segoe UI", 12), bd=0, padx=3, pady=0,
                  activebackground="#17a349", activeforeground="black",
                  cursor="hand2", relief="flat",
                  command=lambda t=text: self._remove_tag(t)).pack(side="left")

        self._widgets[text] = outer
        self._text.window_create("end-1c", window=outer, padx=3, pady=2)
        self._text.insert("end-1c", " ")

    def _remove_tag(self, text: str):
        if text not in self._tags:
            return
        self._tags.remove(text)
        frame = self._widgets.pop(text, None)
        if frame:
            for key, value, pos in self._text.dump("1.0", "end", window=True):
                if value == str(frame):
                    self._text.delete(pos, f"{pos}+2c")
                    break
            frame.destroy()
        self._maybe_show_placeholder()

    def _on_key(self, event):
        if self._showing_placeholder:
            self._hide_placeholder()
        if event.char == ",":
            self._commit()
            return "break"
        if event.keysym == "Return":
            self._commit()
            return "break"

    def _on_backspace(self, event):
        if self._showing_placeholder:
            return "break"
        if not self._typed() and self._tags:
            self._remove_tag(self._tags[-1])
            return "break"

    def get_tags(self) -> list[str]:
        result = self._tags[:]
        current = self._typed()
        if current:
            result.append(current)
        return result

    def clear(self):
        for tag in list(self._tags):
            self._remove_tag(tag)
        self._delete_typed()
        self._show_placeholder()


# ── Main app ──────────────────────────────────────────────────────────────────

def is_youtube(url: str) -> bool:
    return "youtube.com" in url or "youtu.be" in url


def resource_path(filename: str) -> str:
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, filename)


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("green")


class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Local Files Spotify")
        self.geometry("520x750")
        self.resizable(False, False)
        self.configure(fg_color=BG_DARK)
        self.iconbitmap(resource_path("icon.ico"))
        self._preview_thumb: Image.Image | None = None
        self._custom_thumb:  Path | None        = None
        self._build_ui()
        self.after(200, self._check_ffmpeg)

    # ── ffmpeg check ──

    def _check_ffmpeg(self):
        if shutil.which("ffmpeg"):
            return
        answer = mb.askyesno(
            "ffmpeg Not Found",
            "ffmpeg is required but wasn't found on your system.\n\n"
            "Would you like to install it automatically via winget?\n"
            "(This takes about a minute)",
        )
        if not answer:
            return
        self._log("Installing ffmpeg, please wait...")
        self.download_btn.configure(state="disabled")
        threading.Thread(target=self._install_ffmpeg, daemon=True).start()

    def _install_ffmpeg(self):
        try:
            result = subprocess.run(
                ["winget", "install", "Gyan.FFmpeg",
                 "--accept-source-agreements", "--accept-package-agreements"],
                capture_output=True, text=True,
            )
            if result.returncode == 0:
                self.after(0, self._log, "ffmpeg installed! Please restart the app.")
            else:
                self.after(0, self._log, f"Install failed: {result.stderr.strip()}")
        except Exception as e:
            self.after(0, self._log, f"Error during install: {e}")
        finally:
            self.after(0, lambda: self.download_btn.configure(state="normal"))

    # ── UI build ──

    def _build_ui(self):
        ctk.CTkLabel(
            self, text="Local Files Spotify",
            font=ctk.CTkFont(size=22, weight="bold"),
            text_color=TEXT_MAIN,
        ).pack(pady=(28, 4))
        ctk.CTkLabel(
            self, text="Download and tag music for Spotify local files",
            font=ctk.CTkFont(size=12), text_color=TEXT_DIM,
        ).pack(pady=(0, 20))

        card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=14)
        card.pack(padx=28, fill="x")

        # URL
        ctk.CTkLabel(card, text="URL", font=ctk.CTkFont(size=12), text_color=TEXT_DIM).pack(anchor="w", padx=18, pady=(0, 4))
        self.url_entry = ctk.CTkEntry(card, width=464, height=38,
                                       placeholder_text="Paste YouTube or SoundCloud link...",
                                       fg_color=BG_INPUT, border_color=BG_INPUT,
                                       font=ctk.CTkFont(size=13))
        self.url_entry.pack(padx=18, pady=(0, 14))

        # Artist tag input
        ctk.CTkLabel(card, text="Artist(s)  —  press comma to add each one",
                     font=ctk.CTkFont(size=12), text_color=TEXT_DIM).pack(anchor="w", padx=18, pady=(0, 4))
        self.artist_input = TagInput(card, width=464)
        self.artist_input.pack(padx=18, pady=(0, 14), fill="x")

        # Title row
        title_row = ctk.CTkFrame(card, fg_color="transparent")
        title_row.pack(padx=18, fill="x", pady=(0, 4))
        ctk.CTkLabel(title_row, text="Title", font=ctk.CTkFont(size=12), text_color=TEXT_DIM).pack(side="left")
        self.preview_btn = ctk.CTkButton(
            title_row, text="Preview", width=80, height=24,
            font=ctk.CTkFont(size=11),
            fg_color=BG_INPUT, hover_color="#3A3A3A",
            text_color=TEXT_DIM, corner_radius=6,
            command=self.start_preview,
        )
        self.preview_btn.pack(side="right")
        self.title_entry = ctk.CTkEntry(
            card, width=464, height=38,
            placeholder_text="Click Preview to see the cleaned title (editable)",
            fg_color=BG_INPUT, border_color=BG_INPUT,
            font=ctk.CTkFont(size=13),
        )
        self.title_entry.pack(padx=18, pady=(0, 14))

        # Thumbnail row
        thumb_row = ctk.CTkFrame(card, fg_color="transparent")
        thumb_row.pack(padx=18, fill="x", pady=(0, 8))
        ctk.CTkLabel(thumb_row, text="Thumbnail", font=ctk.CTkFont(size=12), text_color=TEXT_DIM).pack(side="left")
        self.upload_btn = ctk.CTkButton(
            thumb_row, text="Upload Custom", width=120, height=24,
            font=ctk.CTkFont(size=11),
            fg_color=BG_INPUT, hover_color="#3A3A3A",
            text_color=TEXT_DIM, corner_radius=6,
            command=self._pick_custom_thumbnail,
        )
        self.upload_btn.pack(side="right")

        placeholder = self._make_placeholder()
        self._thumb_ctk = ctk.CTkImage(placeholder, size=(THUMB_SIZE, THUMB_SIZE))
        self._thumb_label = ctk.CTkLabel(
            card, image=self._thumb_ctk, text="",
            width=THUMB_SIZE, height=THUMB_SIZE,
            fg_color=BG_INPUT, corner_radius=8,
        )
        self._thumb_label.pack(padx=18, pady=(0, 18), anchor="w")

        # Buttons
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(padx=28, pady=18, fill="x")
        self.download_btn = ctk.CTkButton(
            btn_row, text="Download", height=44, width=360,
            font=ctk.CTkFont(size=14, weight="bold"),
            fg_color=SPOTIFY_GREEN, hover_color="#17a349",
            text_color="#000000", corner_radius=10,
            command=self.start_download,
        )
        self.download_btn.pack(side="left")
        self.reset_btn = ctk.CTkButton(
            btn_row, text="Reset", height=44, width=96,
            font=ctk.CTkFont(size=13),
            fg_color=BG_CARD, hover_color="#2A2A2A",
            text_color=TEXT_DIM, corner_radius=10,
            command=self.reset,
        )
        self.reset_btn.pack(side="right")

        # Log
        self.log = ctk.CTkTextbox(self, width=464, height=148,
                                   fg_color=BG_CARD, text_color=TEXT_DIM,
                                   font=ctk.CTkFont(family="Consolas", size=12),
                                   corner_radius=10, state="disabled")
        self.log.pack(padx=28, pady=(0, 20))

    # ── Thumbnail helpers ──

    def _make_placeholder(self) -> Image.Image:
        img = Image.new("RGB", (THUMB_SIZE, THUMB_SIZE), BG_INPUT)
        return img

    def _set_thumb_display(self, img: Image.Image):
        display = img.convert("RGB").resize((THUMB_SIZE, THUMB_SIZE), Image.LANCZOS)
        self._thumb_ctk = ctk.CTkImage(display, size=(THUMB_SIZE, THUMB_SIZE))
        self._thumb_label.configure(image=self._thumb_ctk)

    def _pick_custom_thumbnail(self):
        path = filedialog.askopenfilename(
            title="Select Thumbnail Image",
            filetypes=[("Image files", "*.jpg *.jpeg *.png *.webp"), ("All files", "*.*")],
        )
        if path:
            self._custom_thumb = Path(path)
            self._set_thumb_display(Image.open(path))

    # ── Preview ──

    def start_preview(self):
        url = self.url_entry.get().strip()
        artists = self.artist_input.get_tags()
        if not url:
            self._log("Error: No URL provided.")
            return
        self.preview_btn.configure(state="disabled", text="Fetching...")
        threading.Thread(target=self._fetch_preview, args=(url, artists), daemon=True).start()

    def _fetch_preview(self, url: str, artists: list[str]):
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
                info = ydl.extract_info(url, download=False)

            raw_title = info.get("title", "")
            title = clean_title(raw_title, artists) if artists else raw_title
            self.after(0, self._apply_preview_title, title)

            thumb_url = info.get("thumbnail")
            if thumb_url:
                with urllib.request.urlopen(thumb_url) as resp:
                    data = resp.read()
                img = Image.open(io.BytesIO(data))
                crop = is_youtube(url)
                if crop:
                    w, h = img.size
                    side = min(w, h)
                    left = (w - side) // 2
                    top  = (h - side) // 2
                    img  = img.crop((left, top, left + side, top + side))
                self._preview_thumb = img
                self.after(0, self._set_thumb_display, img)

        except Exception as e:
            self.after(0, self._log, f"Preview error: {e}")
        finally:
            self.after(0, lambda: self.preview_btn.configure(state="normal", text="Preview"))

    def _apply_preview_title(self, title: str):
        self.title_entry.delete(0, "end")
        self.title_entry.insert(0, title)

    # ── Download ──

    def _log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _flash_taskbar(self):
        from ctypes import wintypes
        class FLASHWINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize",    wintypes.UINT),
                ("hwnd",      wintypes.HANDLE),
                ("dwFlags",   wintypes.DWORD),
                ("uCount",    wintypes.UINT),
                ("dwTimeout", wintypes.DWORD),
            ]
        FLASHW_ALL      = 0x00000003
        FLASHW_TIMERNOFG = 0x0000000C
        hwnd = ctypes.windll.user32.FindWindowW(None, "Local Files Spotify")
        if not hwnd:
            return
        fwi = FLASHWINFO(
            cbSize=ctypes.sizeof(FLASHWINFO),
            hwnd=hwnd,
            dwFlags=FLASHW_ALL | FLASHW_TIMERNOFG,
            uCount=0,
            dwTimeout=0,
        )
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(fwi))

    def _set_downloading(self, downloading: bool):
        state = "disabled" if downloading else "normal"
        self.preview_btn.configure(state=state)
        self.reset_btn.configure(state=state)
        self.upload_btn.configure(state=state)
        if downloading:
            self.download_btn.configure(state="disabled", text="Downloading...")
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self.log.configure(state="disabled")
        else:
            self.download_btn.configure(state="normal", text="Download")

    def reset(self):
        self.url_entry.delete(0, "end")
        self.artist_input.clear()
        self.title_entry.delete(0, "end")
        self._preview_thumb = None
        self._custom_thumb  = None
        self._set_thumb_display(self._make_placeholder())
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def start_download(self):
        url            = self.url_entry.get().strip()
        artists        = self.artist_input.get_tags()
        override_title = self.title_entry.get().strip()

        if not url:
            self._log("Error: No URL provided.")
            return
        if not artists:
            self._log("Error: No artist name provided.")
            return

        self._set_downloading(True)
        threading.Thread(
            target=self._run,
            args=(url, artists, override_title),
            daemon=True,
        ).start()

    def _run(self, url: str, artists: list[str], override_title: str):
        try:
            OUTPUT_DIR.mkdir(exist_ok=True)
            last_pct = [-1]

            def progress_hook(d):
                if d["status"] == "downloading":
                    downloaded = d.get("downloaded_bytes", 0)
                    total = d.get("total_bytes") or d.get("total_bytes_estimate", 0)
                    if total:
                        pct = int(downloaded / total * 100)
                        if pct != last_pct[0] and pct % 10 == 0:
                            last_pct[0] = pct
                            self.after(0, self._log, f"Downloading... {pct}%")
                elif d["status"] == "finished":
                    self.after(0, self._log, "Converting to MP3...")

            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": str(OUTPUT_DIR / "%(title)s.%(ext)s"),
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320"}],
                "writethumbnail": self._custom_thumb is None,
                "progress_hooks": [progress_hook],
                "quiet": True,
                "no_warnings": True,
            }

            self.after(0, self._log, "Starting download...")
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

            mp3_path: Path | None = None
            for dl in info.get("requested_downloads", []):
                fp = dl.get("filepath", "")
                if fp.endswith(".mp3"):
                    mp3_path = Path(fp)
                    break

            if not mp3_path or not mp3_path.exists():
                self.after(0, self._log, "Error: Could not locate downloaded MP3.")
                return

            title    = override_title if override_title else clean_title(mp3_path.stem, artists)
            new_path = mp3_path.with_name(f"{title}.mp3")

            if new_path != mp3_path and new_path.exists():
                mp3_path.unlink(missing_ok=True)
                thumb = find_thumbnail(mp3_path.stem, OUTPUT_DIR)
                if thumb:
                    thumb.unlink(missing_ok=True)
                self.after(0, mb.showwarning, "File Already Exists",
                           f'"{title}.mp3" already exists.\n\nDelete it or edit the title before downloading.')
                return

            mp3_path.rename(new_path)
            mp3_path = new_path
            self.after(0, self._log, f"Title:   {title}")
            self.after(0, self._log, f"Artists: {', '.join(artists)}")

            # Resolve thumbnail source
            if self._custom_thumb and self._custom_thumb.exists():
                thumb_img = Image.open(self._custom_thumb)
                crop = False  # User chose it — don't crop
            else:
                thumb_path = find_thumbnail(mp3_path.stem, OUTPUT_DIR)
                if not thumb_path:
                    for ext in ("jpg", "webp", "png"):
                        for f in OUTPUT_DIR.glob(f"*.{ext}"):
                            thumb_path = f
                            break
                        if thumb_path:
                            break
                if thumb_path:
                    thumb_img = Image.open(io.BytesIO(thumb_path.read_bytes()))
                    thumb_path.unlink()
                    crop = is_youtube(url)
                else:
                    thumb_img = None

            if thumb_img is None:
                self.after(0, self._log, "Warning: No thumbnail found.")
                self.after(0, self._log, f"Done!  {mp3_path.name}")
                winsound.MessageBeep(winsound.MB_ICONASTERISK)
                self.after(0, self._flash_taskbar)
                return

            self.after(0, self._log, "Processing thumbnail...")
            final_img = process_thumbnail(thumb_img, TARGET_SIZE, crop=crop)

            self.after(0, self._log, "Embedding metadata...")
            embed_tags(str(mp3_path), final_img, artists, title)

            self.after(0, self._log, f"Done!  {mp3_path.name}")
            winsound.MessageBeep(winsound.MB_ICONASTERISK)

        except Exception as e:
            self.after(0, self._log, f"Error: {e}")
        finally:
            self.after(0, self._set_downloading, False)


if __name__ == "__main__":
    app = App()
    app.mainloop()
