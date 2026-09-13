## Features

- **Live Galactic War Telemetry (`/war`, `/major_order`):**
  - Continuous background polling of `api.helldivers2.dev` with automatic fallback mirrors.
  - Active Major Order tracking with resolved target planet names, liberation/defense progress percentages, dynamic Discord countdowns (`<t:unix:R>`), and medal rewards.
  - Automatic alerts for High Command Dispatches and new Major Orders with role pings.

- **Post-Mission Extraction & Debrief (`/debrief`):**
  - End-of-mission scoreboard screenshot parsing powered by **Gemini Flash Vision**.
  - Structured extraction of player names, kills, casualties, accuracy %, stims injected, and friendly fire damage.
  - Computes per-mission **Democratic Valor Rating (DVR)** and updates rolling career Exponential Moving Averages (EMA).
  - Patriotic Ministry of Truth performance evaluations with automated sanitization pipelines to guarantee clean, unclipped commentary.

- **Ministry of Truth Tactical Terminal (`/ask`, `/stratagem`, `/stats`):**
  - **`/ask <question>`:** Ask any tactical, lore, or gameplay question. Grounded in real-time MediaWiki scraped intelligence with a 15-second cooldown and chunked embeds adhering to Discord limits.
  - **`/stratagem <name>`:** Requisitions official stratagem call-in codes (directional arrows: ➡ ⬆ ⬇ ⬅), cooldowns, uses, and procurement costs.
  - **`/stats <query>`:** Exhaustive tactical breakdowns of weapons (ballistics, traits, recoil), enemies (body HP, armor values, fatal weakpoints), planets, and boosters.

- **Official Steam News & Patch Log:**
  - Background polling for Helldivers 2 Steam announcements.
  - Deduplicated, formatted rich embed broadcasts for official patch notes, hotfixes, and Warbond updates.

- **Wall of Heroes & Career Dossiers (`/profile`, `/leaderboard`):**
  - Rolling career profiles tracking total extractions, kills, casualties, K/D ratio, accuracy, and friendly fire.
  - 18 Patriotic military rank tiers spanning Enlisted, Field Officers, and High Command:
    - **Enlisted / NCOs:** Line Helldiver $\rightarrow$ Lance Helldiver $\rightarrow$ Corporal Helldiver $\rightarrow$ Sergeant Helldiver $\rightarrow$ Staff Sergeant Helldiver $\rightarrow$ Gunnery Sergeant Helldiver $\rightarrow$ Master Sergeant Helldiver $\rightarrow$ Command Sergeant Helldiver
    - **Commissioned Officers:** Second Lieutenant $\rightarrow$ First Lieutenant $\rightarrow$ Captain Helldiver $\rightarrow$ Major Helldiver $\rightarrow$ Lieutenant Colonel Helldiver $\rightarrow$ Colonel Helldiver
    - **Marshals & High Command:** Brigadier Marshal $\rightarrow$ Major Marshal $\rightarrow$ High Marshal $\rightarrow$ Supreme Marshal
  - Server leaderboards sorted by DVR or shot accuracy.

---

## Setup

### Prerequisites
- **Python 3.12+**
- **Git**
- A **Discord Bot Token** with `Message Content` and `Server Members` Privileged Gateway Intents enabled.
- A **Google AI Studio API Key** (for Gemini Flash models).

---

### 1. Clone the Repository

```bash
git clone https://github.com/Chemist3-Lab/helldivers-democratic-bot.git
cd helldivers-democratic-bot
```

---

### 2. Set Up Virtual Environment & Dependencies

#### Using standard `venv` (PowerShell / Windows):
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev]"
```

#### Using standard `venv` (macOS / Linux):
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e ".[dev]"
```

#### Using `uv` (Recommended for fast package resolution):
```bash
uv sync --all-groups
```

---

### 3. Configure Environment Variables

Copy the example environment file:

```bash
cp .env.example .env
```

Open `.env` in your editor and configure your credentials:

```ini
# ─── Required Credentials ─────────────────────────────────────
DISCORD_TOKEN=your_discord_bot_token_here
GEMINI_API_KEY=your_google_ai_studio_api_key_here

# ─── Discord Channel & Role Configuration ─────────────────────
# Single unified channel for all alerts, patch dispatches, and debriefs
HELLDIVER_CHANNEL_ID=123456789012345678
# Role ID to ping on urgent dispatches and new Major Orders
HELLDIVER_ROLE_ID=123456789012345678

# ─── Slash Command Synchronization ────────────────────────────
# Set GUILD_ID to your server snowflake for instant dev command sync.
# Leave commented out or set to 0 to sync globally across all servers (production).
# GUILD_ID=123456789012345678

# ─── Optional Configuration (Defaults Shown) ───────────────────
# DATABASE_URL=sqlite+aiosqlite:///democracy.db
# WAR_POLL_SECONDS=300
# NEWS_POLL_SECONDS=600
# LOG_LEVEL=INFO
# GEMINI_MODEL=gemini-3.6-flash
```

---

### 4. Run the Bot

```bash
# Via Python module
python -m src.main

# Or via installed console script
helldivers-bot
```

When connected, the bot will log:
```
2026-09-13 12:00:00 | INFO     | helldivers | 🦅 Initializing Helldivers Democratic Bot...
2026-09-13 12:00:01 | INFO     | helldivers | Database engine initialized with WAL mode
2026-09-13 12:00:02 | INFO     | src.bot.bot | Loaded extension: src.bot.cogs.war
2026-09-13 12:00:02 | INFO     | src.bot.bot | Loaded extension: src.bot.cogs.news
2026-09-13 12:00:02 | INFO     | src.bot.bot | Loaded extension: src.bot.cogs.wiki
2026-09-13 12:00:02 | INFO     | src.bot.bot | Loaded extension: src.bot.cogs.scoreboard
2026-09-13 12:00:03 | INFO     | src.bot.bot | 🦅 Helldivers Bot#1234 is online — spreading Managed Democracy!
```

---

### 5. Running Automated Tests

Run the test suite with `pytest`:

```bash
pytest -v
```

All 26 unit and regression tests will execute against live formulas, markdown splitting boundaries, and parser fixtures.

---

## 🐳 Docker & Container Deployment

### Running with Docker

```bash
# Build Docker image
docker build -t helldivers-bot .

# Run container with environment file and persistent volume mount
docker run -d \
  --name helldivers-bot \
  --env-file .env \
  -v helldivers_data:/app/data \
  --restart unless-stopped \
  helldivers-bot
```

### Railway Deployment
1. Connect your repository to **Railway**.
2. Add a persistent storage volume mounted at `/app/data`.
3. In Railway **Variables**, add:
   - `DISCORD_TOKEN`
   - `GEMINI_API_KEY`
   - `HELLDIVER_CHANNEL_ID`
   - `HELLDIVER_ROLE_ID`
   - `DATABASE_URL=sqlite+aiosqlite:////app/data/democracy.db`
4. The bot will automatically initialize SQLite WAL tables and sync commands globally.

---

## 📖 Slash Command Reference

| Command | Parameters | Description |
|---|---|---|
| `/war` | None | Displays active planetary campaigns, defense events, and live liberation percentages. |
| `/major_order` | None | Displays active Major Order directives, target planet progress, dynamic countdown, and rewards. |
| `/debrief` | `image: Attachment`<br>`difficulty: [1-10]` *(Optional)* | Submits end-of-mission extraction scoreboard screenshot for Gemini vision parsing and career DVR commit. |
| `/leaderboard` | `sort_by: ["dvr", "accuracy"]` | Displays the top 10 decorated Helldivers on the server Wall of Heroes. |
| `/profile` | `user: Member` *(Optional)* | Displays lifetime combat records, K/D ratio, accuracy %, and current DVR military rank tier. |
| `/reset_leaderboard` | `confirm: bool` | *(Admin Only)* Purges all career dossiers and mission records from the database. |
| `/stratagem` | `name: str` | Displays stratagem call-in directional arrow code, cooldown, and permit classification. |
| `/stats` | `query: str` | Queries exhaustive tactical dossier on weapons, enemies, armor, boosters, or planets. |
| `/ask` | `question: str` | Asks the Ministry of Truth Tactical Terminal any Helldivers question (15s cooldown). |

---

## 🏛️ Project Architecture

```
helldivers-democratic-bot/
├── ARCHITECTURE.md             # In-depth architectural blueprint, ERD & DVR math specs
├── pyproject.toml              # Build system, dependencies, and tooling configs
├── Dockerfile                  # Production container definition
├── .env.example                # Example environment variables
├── src/
│   ├── main.py                 # Application bootstrapper and async entrypoint
│   ├── config.py               # Pydantic Settings environment configuration
│   ├── ai/
│   │   ├── persona.py          # Ministry of Truth persona & commentary sanitization
│   │   └── vision.py           # Gemini Flash Vision scoreboard extraction pipeline
│   ├── bot/
│   │   ├── bot.py              # Custom commands.Bot subclass with sync guard
│   │   ├── cogs/               # Pure Discord command controllers
│   │   │   ├── war.py          # /war, /major_order and background war polling
│   │   │   ├── news.py         # Steam patch background polling loop
│   │   │   ├── wiki.py         # /stratagem, /stats, /ask
│   │   │   └── scoreboard.py   # /debrief, /leaderboard, /profile
│   │   └── ui/                 # Dedicated Presentation Layer
│   │       ├── markdown.py     # Safe markdown splitting and delimiter repair
│   │       ├── wiki_embeds.py  # Weapon, enemy, stratagem & Q&A embed builders
│   │       ├── scoreboard_embeds.py # Debrief, profile & leaderboard embeds
│   │       ├── war_embeds.py   # Galactic war, dispatch & major order embeds
│   │       └── news_embeds.py  # Steam patch embed builder
│   ├── database/
│   │   ├── engine.py           # Async SQLite engine (WAL mode)
│   │   ├── models.py           # SQLModel table schemas
│   │   └── repos.py            # Async CRUD repository pattern
│   └── services/
│       ├── _retry.py           # Exponential backoff retry decorator
│       ├── dvr.py              # Democratic Valor Rating calculation formulas
│       ├── hd2_api.py          # Helldivers 2 Community API client
│       ├── steam_api.py        # Steam Web API client
│       ├── wiki_client.py      # MediaWiki API client & disk cache
│       ├── wiki_parser.py      # BeautifulSoup DOM parsing engine
│       └── wiki_models.py      # Data models for wiki articles & briefs
└── tests/
    ├── test_refactored_modules.py     # Unit tests for newly extracted modular components
    └── test_wiki_and_debrief_fixes.py # Regression test suite for fixes and formatters
```

For complete technical specifications, mathematical formulas, and data flow pipelines, refer to [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## ⚖️ License

Distributed under the **MIT License**. For Freedom and Managed Democracy!
