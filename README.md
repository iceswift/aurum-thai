# 🏆 Thai Gold Price API

> **High‑Performance • Auto‑Scaling • Market‑Aware**

![Python](https://img.shields.io/badge/Python-3.10%2B-blue?style=for-the-badge\&logo=python)
![FastAPI](https://img.shields.io/badge/FastAPI-0.109-green?style=for-the-badge\&logo=fastapi)
![Playwright](https://img.shields.io/badge/Playwright-Async-orange?style=for-the-badge\&logo=playwright)
![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge\&logo=docker)
![License](https://img.shields.io/badge/License-MIT-purple?style=for-the-badge)

**The Ultimate Gold Price API for Developers.**
Scrape once, cache smartly, and serve Thai gold prices at lightning speed—built to handle **100k+ concurrent users** with CDN offloading.

🌐 **Live Demo:** [https://api.thaigoldview.com](https://api.thaigoldview.com)
*(Replace with your actual URL)*

---

## 🚀 Key Features

* ⚡ **High Performance** — Powered by **FastAPI** + **Async Playwright**
* 🧠 **Smart In‑Memory Caching** — No DB required; microsecond responses
* 🤖 **Auto‑Scraping Worker** — Background refresh every **60 seconds**
* 🛡️ **Cloudflare Ready** — `Cache-Control: s-maxage=60`
* 🕒 **Market‑Aware** — Sleeps on **Sundays** & outside **09:00–17:30 (UTC+7)**
* ♻️ **Resource Optimized** — **Singleton browser** to minimize RAM

---

## 🏗️ Architecture

**Fetch Once, Serve Many** — scrape a single time, then fan‑out via cache + CDN.

```mermaid
graph TD
    User[📱 Client / App] -- Request --> CF[☁️ Cloudflare CDN]

    subgraph "Server Layer (Railway)"
        CF -- Cache Miss --> API[🚀 FastAPI]
        API -- Read --> Cache[(📦 Global In‑Memory Cache)]
    end

    subgraph "Worker Layer"
        Worker[🤖 Scheduler (60s)] --> Browser[🕷️ Playwright (Chromium)]
        Browser --> Source[🌐 GoldTraders.or.th]
        Worker --> Cache
    end

    style CF fill:#f38020,stroke:#333,stroke-width:2px,color:white
    style API fill:#009688,stroke:#333,stroke-width:2px,color:white
    style Browser fill:#DD344C,stroke:#333,stroke-width:2px,color:white
```

---

## 🔌 API Endpoints

**Base URL:** `https://api.thaigoldview.com`

### 1) Get Latest Prices (Full Data)

Returns market status, all prices, and timestamps.

`GET /api/latest`

```json
{
  "status": "success",
  "market_status": "Open",
  "data": {
    "date": "10/06/2567",
    "time": "14:30",
    "bullion_buy": "40,100",
    "bullion_sell": "40,200",
    "ornament_buy": "39,385.00",
    "ornament_sell": "40,700",
    "change": "+50"
  },
  "updated_at": "2024-06-10 14:30:15"
}
```

### 2) Gold Bar Only (Simplified)

`GET /api/gold`

### 3) Jewelry Prices (By Weight)

`GET /api/weight_jewelry`

### 4) Jewelry Prices (By Percentage)

`GET /api/percent_jewelry`

---

## 🛠️ Installation & Local Run

### Prerequisites

* Python **3.10+**
* Docker *(optional, recommended)*

### Option A: Run with Python

```bash
# Clone
https://github.com/your-username/thai-gold-api.git
cd thai-gold-api

# Install deps
pip install -r requirements.txt
playwright install chromium

# Run
uvicorn main:app --reload
```

### Option B: Run with Docker (Recommended)

```bash
# Build
docker build -t gold-api .

# Run (auto‑restart)
docker run -d -p 8000:8000 --restart always gold-api
```

---

## ☁️ Deployment (Railway + Cloudflare)

1. **Push to GitHub** — Upload your repository
2. **Deploy on Railway** — Auto‑detects `Dockerfile`
3. **Custom Domain** — Map `api.thaigoldview.com`
4. **Cloudflare**

   * Add **CNAME** → Railway
   * Enable **Proxy (Orange Cloud)**
   * Page Rule: **Cache Everything** for `api.thaigoldview.com/*`

---

## 📝 Disclaimer

This API scrapes data from publicly available sources for **educational and personal use**. Please respect the source website’s **Terms of Service**.

---

### ❤️ Made with love by **Suwiwat Sinsomboon**
