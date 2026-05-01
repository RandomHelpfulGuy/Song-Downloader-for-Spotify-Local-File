# Local Files Spotify

A desktop app that downloads audio from YouTube or SoundCloud, cleans the title, and embeds artist/thumbnail metadata — formatted for Spotify's local files feature.

## Requirements

### 1. Python

Download and install Python 3.10+ from [python.org](https://www.python.org/downloads/).

### 2. Python packages

```
pip install -r requirements.txt
```

### 3. ffmpeg

ffmpeg is required to convert audio to MP3. The app will offer to install it automatically via winget on first launch, or you can install it manually:

**Option A — winget (Windows):**
```
winget install Gyan.FFmpeg
```

**Option B — manual:** Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add the `bin` folder to your system PATH.

## Running

```
python localFilesSpotify.py
```

## Usage

1. Paste a YouTube or SoundCloud URL
2. Add one or more artist names (press comma or Enter after each)
3. Click **Preview** to fetch the title and thumbnail
4. Edit the title if needed, or upload a custom thumbnail
5. Click **Download** — the MP3 will be saved to a `downloads/` folder
