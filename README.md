# insta_online
<p align="center">
  <img src="https://img.shields.io/badge/Instagram-Automation-E4405F?style=for-the-badge&logo=instagram&logoColor=white" alt="Instagram Automation"/>
  <img src="https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+"/>
  <img src="https://img.shields.io/badge/License-MIT-green?style=for-the-badge" alt="MIT License"/>
  <img src="https://img.shields.io/badge/Platform-Windows%20|%20Linux%20|%20macOS-blue?style=for-the-badge" alt="Cross Platform"/>
</p>

<h1 align="center">📷 Instagram Reel Automation Bot</h1>

<p align="center">
  <b>Fully automated Instagram Reels downloader, re-uploader & scheduler — powered by AI</b>
  <br/>
  <i>Monitor target accounts → Download viral reels → Auto-upload to your account → 24/7 hands-free</i>
</p>

<p align="center">
  <a href="#-features">Features</a> •
  <a href="#-quick-start">Quick Start</a> •
  <a href="#%EF%B8%8F-configuration">Configuration</a> •
  <a href="#-web-dashboard">Dashboard</a> •
  <a href="#-how-it-works">How It Works</a> •
  <a href="#-faq">FAQ</a>
</p>

---

## 🔥 What Is This?

The most advanced **open-source Instagram Reel automation tool** available. It monitors any Instagram account, automatically downloads their best-performing reels, rewrites captions using AI, and re-uploads them to your account — all on autopilot with human-like behavior to avoid detection.

**No coding required.** Edit one config file, double-click `run.bat`, and walk away.

### Use Cases
- 🚀 **Grow your Instagram** by reposting viral content from niche accounts
- 📊 **Content curation** — automatically collect reels above a view threshold
- 🤖 **Hands-free content pipeline** — schedule uploads at optimal times
- 🔄 **Multi-account monitoring** — track unlimited target accounts simultaneously

---

## ✨ Features

### Core Automation
| Feature | Description |
|---------|-------------|
| **Auto-Download Reels** | Monitors target accounts and downloads new reels automatically |
| **Auto-Upload to Instagram** | Uploads downloaded reels to your account with zero interaction |
| **Smart Scheduling** | Upload at configurable times (e.g., 10:00, 14:00, 18:00) with human-like gaps |
| **View Count Filtering** | Only download reels above a minimum view threshold (e.g., 1,000+ views) |
| **Multi-Account Monitoring** | Track unlimited Instagram accounts simultaneously |
| **24/7 Continuous Operation** | Runs non-stop with auto-restart on crash via `run.bat` |

### AI-Powered Intelligence
| Feature | Description |
|---------|-------------|
| **AI Caption Rewriting** | Automatically rephrases captions using NVIDIA AI (Mistral model) |
| **AI Error Recovery** | AI analyzes errors and suggests fixes in real-time |
| **AI Page Analysis** | Screenshots + AI to navigate Instagram's changing UI |
| **Smart Navigation** | AI detects login walls, cookie banners, 2FA, and handles them |

### Anti-Detection & Safety
| Feature | Description |
|---------|-------------|
| **Human-Like Behavior** | Random scrolling, pausing, hovering, and explore visits |
| **Smart Upload Delays** | 20+ minute randomized gaps between uploads |
| **Cookie Session Management** | Saves & restores login sessions to minimize logins |
| **Anti-Bot Warm-up/Cooldown** | Performs natural browsing before and after scraping |
| **Stealth Browser Mode** | Undetectable Chrome via SeleniumBase UC mode |

### Reliability & Self-Healing
| Feature | Description |
|---------|-------------|
| **Failed Upload Auto-Retry** | Automatically retries failed uploads (configurable attempts & delay) |
| **Catch-Up Upload Loop** | Finds and uploads any missed/downloaded reels periodically |
| **Duplicate Prevention** | Triple-layer deduplication (DB + queue + filesystem) |
| **Rejected Reel Cooldown** | Re-checks rejected reels after configurable days |
| **Crash Recovery** | `run.bat` auto-restarts on any crash with 10s cooldown |
| **DB Reconciliation** | Syncs database with actual files on startup |

### Storage & Cleanup
| Feature | Description |
|---------|-------------|
| **Auto Storage Cleanup** | Deletes uploaded files after configurable days (default: 7) |
| **Immediate Cleanup Option** | Delete files right after successful upload |
| **Smart Filename Convention** | `username_shortcode.mp4` for easy identification |

### Web Dashboard
| Feature | Description |
|---------|-------------|
| **Real-Time Dashboard** | Beautiful dark-mode web UI at `http://localhost:8000` |
| **Daily Stats** | Downloads/uploads/retries/failures tracked per day |
| **Batch Upload** | Select and upload multiple reels from the dashboard |
| **Live Progress** | Real-time batch upload progress bar with wait timers |
| **Toggle Auto-Upload** | Enable/disable auto-upload from the UI |
| **Reel Database View** | Browse all discovered reels with status, captions, hashtags |

### Developer-Friendly
| Feature | Description |
|---------|-------------|
| **REST API** | Full JSON API for status, uploads, refresh, stats |
| **Rotating Logs** | Auto-rotating log files (10MB × 5 backups) |
| **Modular Architecture** | Clean separation: scraper, downloader, uploader, queue, monitor |
| **Async Everything** | Built on `asyncio` for maximum performance |
| **Task Queue** | In-memory job queue with configurable worker pool |

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.10+** ([Download](https://www.python.org/downloads/))
- **Google Chrome** (installed automatically by SeleniumBase if missing)

### 1. Clone the Repository

```bash
git clone https://github.com/YOUR_USERNAME/instagram-reel-bot.git
cd instagram-reel-bot
```

### 2. Configure

Edit `config.json` with your details:

```json
{
  "my_account": {
    "username": "your_instagram_username",
    "password": "your_instagram_password"
  },
  "targets": [
    {
      "username": "target_account",
      "min_views": 1000
    }
  ]
}
```

### 3. Run

**Windows (recommended):**
```
run.bat
```

**Any OS:**
```bash
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
# .venv\Scripts\activate.bat     # Windows
pip install -r requirements.txt
python -m src run
```

### 4. Open Dashboard

Visit **http://localhost:8000** to monitor everything in real-time.

---

## ⚙️ Configuration

All settings are in `config.json`. Here's every option explained:

### Account Settings

```json
{
  "my_account": {
    "username": "",        // Your Instagram username
    "password": ""         // Your Instagram password
  },
  "targets": [
    {
      "username": "target_account",   // Account to monitor
      "min_views": 1000,              // Only download reels with 1000+ views
      "recheck_rejected_days": 10     // Re-check rejected reels after 10 days
    }
  ]
}
```

### Monitoring

```json
{
  "poll_interval_seconds": 14400,  // Check for new reels every 4 hours
  "max_reels_per_scan": 200        // Max reels to scan per account per poll
}
```

### Upload (Full Automation)

```json
{
  "upload": {
    "enabled": true,              // Master switch for uploading
    "auto_upload": true,          // Upload immediately after download
    "schedule_enabled": true,     // Also upload at scheduled times
    "schedule_times": ["10:00", "14:00", "17:10"],
    "reels_per_schedule": 3,      // Max uploads per schedule slot
    "delay_between_uploads": 1200,// 20 min gap between uploads (seconds)
    "headless": true,             // Run browser invisibly

    "retry_failed": true,         // Auto-retry failed uploads
    "retry_max_attempts": 3,      // Max retry attempts
    "retry_delay_minutes": 30,    // Wait between retries

    "cleanup_after_upload": false, // Delete file right after upload
    "cleanup_after_days": 7,       // Delete uploaded files after 7 days

    "catchup_enabled": true,       // Find and upload missed reels
    "catchup_interval_minutes": 60 // Check every 60 minutes
  }
}
```

### AI Assistant (Optional)

```json
{
  "ai": {
    "enabled": false,     // Enable AI features
    "api_key": "",        // Your NVIDIA API key (free at build.nvidia.com)
    "model": "mistralai/mistral-large-3-675b-instruct-2512"
  }
}
```

> Get a free API key at [build.nvidia.com](https://build.nvidia.com/) — the bot works perfectly without AI too.

### Advanced

```json
{
  "retry": {
    "max_attempts": 5,         // Scraping retry attempts
    "base_delay": 3.0,         // Base delay between retries (seconds)
    "max_delay": 120.0,        // Max delay cap
    "exponential_base": 2.0    // Exponential backoff multiplier
  },
  "queue": {
    "backend": "memory",       // Job queue backend
    "max_workers": 2           // Parallel download workers
  },
  "web": {
    "enabled": true,           // Enable web dashboard
    "host": "127.0.0.1",
    "port": 8000
  }
}
```

---

## 📊 Web Dashboard

The built-in web dashboard provides complete visibility into the system:

- **Stats Cards** — Discovered, Downloaded, Uploaded, Pending, Failed, Today's activity
- **Reels Database** — Full table of all tracked reels with status indicators
- **Batch Upload** — Select multiple reels and upload with one click
- **Job Queue** — Live view of active download/upload jobs
- **Auto-Upload Toggle** — Enable/disable auto-upload in real-time

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/status` | System status and job list |
| `GET` | `/api/stats` | Daily statistics |
| `GET` | `/api/reels-db` | All tracked reels |
| `GET` | `/api/batch-status` | Batch upload progress |
| `POST` | `/api/upload/{job_id}` | Upload a specific job |
| `POST` | `/api/upload-reel/{shortcode}` | Upload by shortcode |
| `POST` | `/api/upload-batch` | Batch upload selected reels |
| `POST` | `/api/upload-all` | Upload all pending reels |
| `POST` | `/api/toggle-auto` | Toggle auto-upload |
| `POST` | `/api/refresh` | Trigger immediate poll |

---

## 🧠 How It Works

```
┌─────────────────────────────────────────────────────────┐
│                    MONITORING LOOP                       │
│  Poll Target Accounts → Find New Reels → Enqueue Jobs  │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   DOWNLOAD WORKER                        │
│  yt-dlp (with cookies) → Extract metadata & view count  │
│  → Filter by min_views → Save to ./storage/             │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                   UPLOAD PIPELINE                        │
│  AI Caption Rewrite → Playwright Browser → Upload Reel  │
│  → Human-like actions → Save session cookies            │
└──────────────────────┬──────────────────────────────────┘
                       │
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   Auto-Upload    Scheduler      Catch-Up
   (immediate)   (10:00,14:00)  (every 60m)
                       │
                       ▼
              Failed? → Auto-Retry
              (every 30 min, max 3x)
```

### Background Tasks Running 24/7

| Task | Purpose |
|------|---------|
| **Monitor** | Polls target accounts at configured intervals |
| **Queue Workers** | Process download jobs in parallel |
| **Upload Scheduler** | Uploads at scheduled times |
| **Retry Loop** | Retries failed uploads every 30 minutes |
| **Catch-Up Loop** | Uploads any missed reels every 60 minutes |
| **Cleanup Loop** | Deletes old uploaded files every hour |
| **Web Server** | Dashboard at localhost:8000 |

---

## 📁 Project Structure

```
instagram-reel-bot/
├── config.json          # All settings (edit this)
├── run.bat              # One-click launcher (Windows)
├── requirements.txt     # Python dependencies
├── README.md
│
├── src/
│   ├── main.py              # Orchestrator – wires everything together
│   ├── cli.py               # Command-line interface
│   ├── config.py            # Configuration management
│   ├── instagram_client.py  # SeleniumBase scraper + AI navigation
│   ├── downloader.py        # yt-dlp download manager
│   ├── uploader.py          # Playwright async uploader
│   ├── monitor.py           # Background reel polling
│   ├── queue_manager.py     # Async task queue
│   ├── reels_db.py          # Persistent reel tracking database
│   ├── state_manager.py     # Crash-resilient state persistence
│   ├── ai_helper.py         # NVIDIA AI integration
│   ├── web_interface.py     # FastAPI dashboard + REST API
│   └── logger.py            # Rotating log configuration
│
├── storage/             # Downloaded reels (auto-created)
├── cookies/             # Browser session cookies
├── screenshots/         # Debug screenshots
└── logs/                # Application logs
```

---

## 🛡️ Safety & Anti-Detection

This bot is designed to mimic real human behavior:

- **Random delays** between all actions (scrolling, clicking, typing)
- **20+ minute gaps** between uploads (configurable)
- **Random jitter** added to poll intervals (0–30 minutes)
- **Human warm-up** — visits Explore page, scrolls feed before scraping
- **Human cool-down** — random browsing after scraping
- **Session persistence** — reuses cookies to minimize login frequency
- **Stealth browser** — SeleniumBase UC mode bypasses bot detection
- **No API abuse** — uses browser automation, not private APIs

---

## 🔧 CLI Usage

```bash
# Start fully automated (default)
python -m src run

# Start with visible browser (for debugging)
python -m src run --visible

# Use custom config
python -m src run --config my_config.json

# Check system status
python -m src status

# View job queue
python -m src jobs

# Trigger immediate refresh
python -m src refresh
```

---

## ❓ FAQ

**Q: Does it work without AI?**
> Yes! AI is optional. Without it, the bot downloads and uploads using the original caption. Enable AI for smart caption rewriting and error recovery.

**Q: Is it safe? Will my account get banned?**
> The bot uses extensive anti-detection measures (human-like delays, stealth browser, session persistence). However, Instagram automation always carries risk. Use responsibly and consider using a secondary account.

**Q: Can I monitor multiple accounts?**
> Yes! Add as many targets as you want in `config.json`:
> ```json
> "targets": [
>   {"username": "account1", "min_views": 1000},
>   {"username": "account2", "min_views": 5000},
>   {"username": "account3", "min_views": 500}
> ]
> ```

**Q: How do I get the NVIDIA AI key?**
> Visit [build.nvidia.com](https://build.nvidia.com/), create a free account, and generate an API key. It's free for personal use.

**Q: Does it work on Linux/Mac?**
> Yes! Use `python -m src run` instead of `run.bat`. All features work cross-platform.

**Q: What if Instagram changes their UI?**
> The AI page analysis adapts to UI changes automatically. Without AI, the bot uses multiple fallback selectors.

**Q: Can I upload without downloading first?**
> No, the workflow is: Monitor → Download → Upload. But you can manually add `.mp4` files to `./storage/` and they'll be picked up.

---

## 📜 License

This project is licensed under the MIT License — see [LICENSE](LICENSE) for details.

---

## ⚠️ Disclaimer

This tool is for **educational and research purposes only**. Automating Instagram may violate their [Terms of Service](https://help.instagram.com/581066165581870). Use at your own risk. The authors are not responsible for any account bans, legal issues, or damages resulting from the use of this software.

---

## 🌟 Star This Repo!

If this project helped you, please give it a ⭐ on GitHub — it helps others discover it!

---

<p align="center">
  <b>Built with ❤️ using Python, Playwright, SeleniumBase, yt-dlp, FastAPI & NVIDIA AI</b>
</p>

<!-- SEO keywords for GitHub search discovery -->
<!-- instagram bot, instagram automation, instagram reels bot, instagram reel downloader,
instagram reel uploader, instagram auto upload, instagram repost bot, instagram scheduler,
instagram content automation, reels automation tool, instagram growth bot, instagram ai bot,
auto post instagram reels, instagram reel scraper, download instagram reels, upload instagram reels,
instagram automation python, instagram bot python, reels downloader, reels uploader,
instagram auto poster, instagram scheduling tool, instagram viral reels, instagram repost,
social media automation, instagram growth tool, instagram marketing bot, reels bot,
instagram content bot, auto repost instagram, instagram reel scheduler, instagram automation tool,
free instagram bot, open source instagram bot, instagram reel monitor, instagram monitoring tool -->
