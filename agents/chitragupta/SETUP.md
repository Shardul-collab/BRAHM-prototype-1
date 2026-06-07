# 🚀 Chitragupta — Setup Guide

Get up and running in under 5 minutes.

---

## 1. Enter Project
```bash
cd chitragupta
```

---

## 2. Create Virtual Environment
```bash
python -m venv .venv
```

Activate it:

```bash
# macOS / Linux
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

---

## 3. Install Dependencies
```bash
pip install -r requirements.txt
```

---

## 4. Configure Environment

Copy the environment template:
```bash
cp .env.example .env
```

Open `.env`:

```bash
# macOS / Linux
nano .env

# Windows
notepad .env
```

Add your Notion token:
```ini
NOTION_TOKEN=secret_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

👉 Get your token:  
https://www.notion.so/my-integrations

**Steps:**
1. Create a new integration  
2. Copy the secret token  
3. Paste it into `.env`

⚠️ Important:
- Open your target Notion page  
- Click `···` → **Add connections**  
- Select your integration  

---

## 5. Install FFmpeg (Required for Voice Input)

Chitragupta uses Whisper for speech-to-text, which requires FFmpeg.

### macOS
```bash
brew install ffmpeg
```

### Linux (Ubuntu/Debian)
```bash
sudo apt update && sudo apt install ffmpeg -y
```

### Windows
1. Download: https://ffmpeg.org/download.html  
2. Extract  
3. Add the `bin/` folder to your system `PATH`

Verify:
```bash
ffmpeg -version
```

---

## 6. Run the System
```bash
python main.py
```

---

## 7. First Test (Recommended Flow)

### Step 1 — Create Database
```
Select option: 1
```

- Name: `Daily Log`
- Paste Notion Parent Page ID
- Add fields:

| Name | Type |
|------|------|
| Title | title |
| Energy | number |
| Mood | number |
| Notes | rich_text |
| Tags | multi_select |

---

### Step 2 — Log Entry
```
Select option: 2
```

- Select `Daily Log`
- Choose voice or manual input

Example:
```
Energy was 8, mood is 7, did some reading and gym
```

- Review extracted fields  
- Confirm with `Y`

---

### Step 3 — Run Analysis
```
Select option: 3
```

- Select `Daily Log`
- View:
  - Averages
  - Trends
  - Consistency score

---

## 🛠 Troubleshooting

| Problem | Fix |
|--------|-----|
| `NOTION_TOKEN not set` | Ensure `.env` exists and token is correct |
| `No module named whisper` | Re-run `pip install -r requirements.txt` |
| `ffmpeg not found` | Install FFmpeg and add to PATH |
| No microphone | Choose manual input (`N`) |
| Schema not found | Run option `1` first |

---

## 💡 What This System Does

Chitragupta is a voice-first personal tracking system that:

- Converts speech → structured data (Whisper + NLP)
- Stores entries in Notion
- Tracks mood, energy, and productivity
- Analyzes behavioral trends over time

---

