# 🏆 Aurum Thai API (Gold Price Service)

[![FastAPI](https://img.shields.io/badge/FastAPI-005571?style=for-the-badge&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![Playwright](https://img.shields.io/badge/Playwright-45ba4b?style=for-the-badge&logo=Playwright&logoColor=white)](https://playwright.dev/)
[![Docker](https://img.shields.io/badge/Docker-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)

> **The Ultimate Async Gold Price Scraper & API for Thai Gold Markets.**  
> Fast, Reliable, and Smart. Built for developers who need real-time data.

---

## 🚀 Features

-   **⚡ Hybrid Scraping Engine**: Automatically switches between **New Website** and **Classic Website** sources if one fails.
-   **🛡️ Sticky Session**: "Locks" onto a working source to maximize stability and speed.
-   **🚄 Parallel Execution**: Scrapes 5 major gold shops **simultaneously** using Async/Await & Playwright.
-   **🔄 Auto Scheduler**: Updates data automatically every 60 seconds.
-   **💾 Centralized Cache**: Serves data instantly from memory (Zero Latency for clients).
-   **🐳 Docker Ready**: Deploy anywhere with a single command.

---

## 🛍️ Supported Shops

We track 5 major Thai gold traders in real-time:

1.  **Aurora**
2.  **MTS Gold**
3.  **Hua Seng Heng** (ฮั่วเซ่งเฮง)
4.  **Chin Hua Heng** (จินฮั้วเฮง)
5.  **Ausiris**

---

## 🛠️ Tech Stack

-   **Core**: Python 3.11+
-   **API Framework**: FastAPI (High performance)
-   **Browser Automation**: Playwright (Async)
-   **Server**: Uvicorn (ASGI)

---

## 🔌 API Endpoints

### 1. System Status
`GET /`
Returns API status, source used, and last update time.

### 2. Latest Gold Bar Price
`GET /api/latest`
Get the most recent Gold Bar price (96.5%).

### 3. All Gold Shops Data (✨ New)
`GET /api/shops`
Returns price data from all 5 supported shops independently.

### 4. Jewelry / Ornament Prices
`GET /api/percent_jewelry`
Get 96.5% Gold Ornament prices (Buy/Sell).

---

## 📦 Installation & Setup

### Option A: Docker (Recommended)

```bash
# 1. Build the image
docker build -t aurum-thai .

# 2. Run container
docker run -d -p 8000:8000 --name gold-api aurum-thai
```

### Option B: Local Development

```bash
# 1. Clone repository
git clone https://github.com/iceswift/aurum-thai.git
cd aurum-thai

# 2. Create Virtual Environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. Install Dependencies
pip install -r requirements.txt
playwright install chromium

# 4. Run Server
python main.py
```

---

## 📂 Project Structure

```
📦 aurum-thai
 ┣ 📜 Dockerfile.txt       # Deployment Config
 ┣ 📜 main.py              # API Server & Scheduler Logic
 ┣ 📜 shop.py              # Async Scraping Modules (The Core)
 ┣ 📜 requirements.txt     # Python Dependencies
 ┗ 📜 README.md            # This file
```

---

## ⚠️ Notes

-   **Ausiris Scraping**: The Ausiris website requires a 15-second load time. Our async engine handles this in the background, so it **does not block** other shops or the API.
-   **Timezone**: All times are reported in **Asia/Bangkok (UTC+7)**.

---

Made with ❤️ by **Mix Official**
