# WeatherFit AI — Personal AI Weather & Fashion Assistant

WeatherFit AI turns live weather (WeatherAPI.com) into explainable outfit advice. It can detect your location
(only with your browser's permission), compare your starting point with a destination for your travel time, answer
questions in a chat, and optionally use Gemini for natural wording and FashionCLIP to rank outfit images.

This is an in-place upgrade of the earlier "Personal Stylist" app. All earlier features still exist: the rule-based
outfit engine (`recommendations.py`), `agent.py`, Gemini photo analysis, FashionCLIP photo labels, outfit image
generation and the original tests.

## What's new: wardrobe, outfit memory and AI stylist
* **Real SQL database (SQLite)** - `weatherfit.db`, created automatically with versioned, transactional migrations (the earlier app had no database;
  only the optional `user_preferences.json`, which still works).
* **Clothing inventory** - upload several photos, get label suggestions (Gemini vision if you press the button; offline colour guess otherwise; optional
  FashionCLIP category guess), correct them by hand, edit/delete any item. Category, colours, pattern, fabric, season, style, occasion, gender presentation,
  comfort, upload date, last-worn date and usage count are stored in SQL; photos stay private in `data/wardrobe/`.
* **Outfit memory** - every outfit shown or confirmed is stored with date, item IDs, colours, categories, occasion, weather and feedback. Mark outfits
  **Worn / Skipped / Favourite**. Yesterday's exact *combination* is never suggested again; combinations from the last N days (default 7, configurable in the
  sidebar) are avoided whenever alternatives exist; if your wardrobe is too small the app tells you exactly why something is being reused.
* **Two clearly separated sections** - **From Your Wardrobe** (only rows from your wardrobe table) and **New Outfit Inspiration** (AI-suggested, "you may not own
  these"). The app never says you own something that isn't in the database.
* **AI stylist chat** (tab *AI stylist chat*) - English / Hindi / Hinglish, grounded in live weather + wardrobe + history + preferences, with retry and clear
  "live AI unavailable" messages.
* **Visual previews** - item-by-item collage always; optional Gemini full-look rendering that uses your clothing photos as references.
* **Dataset import** - Fashionpedia / DeepFashion annotation mappers behind explicit licence flags plus a bundled CC0 sample. See `DATASETS.md`.

## Features
- **Location:** browser geolocation (button + browser permission prompt). If permission is denied or the component isn't
  available, type a city. The UI always says which one is in use, and never claims "detected" unless the browser really
  provided coordinates.
- **Live weather:** current temperature, feels-like, humidity, wind, rain chance (hourly forecast), UV and any alerts that
  WeatherAPI returns. 10-minute in-memory cache, 5 s connect / 15 s read timeouts, one retry, clear error messages. If
  the API is briefly unreachable, a cached copy under 1 hour old is shown and **labelled as cached**.
- **Travel-aware advice:** enter a destination, departure time and journey time. Origin window = departure → +1 h;
  destination window = arrival → arrival + time at destination. Shows both, compares them, and explains clothing, footwear,
  umbrella, jacket, sunglasses, cap, sunscreen, windbreaker, etc. with the forecast numbers behind each choice.
- **Chat assistant:** understands questions like "I live in Indore and need to travel to Bhopal. What should I wear?",
  "Will I need an umbrella or jacket?", "What should I wear for college, office or a party today?". It asks a follow-up
  only when essential (no city known, or travel with no destination). Gemini (if configured) phrases the answer from the
  fetched data only; any answer quoting a temperature/percentage not in the data is rejected and the data-based answer is used.
- **Personalisation:** style, personality, budget, occasion, favourite colours, things to avoid. Saved to
  `user_preferences.json` **only** if you tick the consent box and press Save; "Forget" deletes it. Location, chats and keys are never saved.
- **FashionCLIP images:** the "Rank outfit images" tab ranks images from `assets/outfits/` and/or photos you upload
  against the day's recommendation. Without FashionCLIP it falls back to a labelled keyword match on file names/tags.
- **Image generation (optional, separate):** Gemini image model from the recommendation. Never used for weather.

## Look and feel
See *Pastel dashboard layout* below. `ui_theme.py` still provides the weather / outfit cards (with a tint that follows how warm it feels, a rain meter and carry-item chips);
if a card ever fails to render, the original layout is shown instead (`show_plan_classic`). The styling targets Streamlit's internal `data-testid` names and `st-key-...` classes,
so after a big Streamlit upgrade a selector may need a small tweak; the app keeps working either way.

## 🎙️ Voice Assistant (new tab)
Speak (or type) a weather/outfit/travel question and get the answer as text **and spoken aloud**, e.g.
*"Today I need to travel from Bhopal to Indore. What should I wear according to the weather?"*

* **How it works:** speech-to-text and text-to-speech use your **browser's built-in Web Speech API** (`voice_component/index.html`) - free, no key, no OpenAI.
  The answer comes from the existing rule engine using real forecasts for the origin and destination (feels-like, rain chance, wind, UV, arrival-time window).
  If the destination is missing the assistant asks for it. If the journey time is not given it assumes 3 hours **and says so**.
* **Languages:** English, Hindi (Devanagari) and Hinglish (Roman letters). Choose *English / Hinglish (en-IN)* or *Hindi (hi-IN)* for the microphone. Replies come back in the
  language you used (Hindi clothing words are translated word-by-word for common items; unknown words stay in English - basic support).
* **Typed fallback:** the "Or type your question" box and example buttons always work (no microphone, unsupported browser, permission denied...).
* **Honest data:** every answer shows and speaks the weather source and the "observed at" time (local time of that place) and says when a cached copy was used.
  Nothing is invented: unknown cities, network failures and missing data are explained instead.
* **Gemini is optional and rarely used:** normal weather questions never call Gemini. It is used only if you tick *"Let Gemini make the wording more natural"* or ask a
  non-weather clothing question, and only while it is enabled and not paused. See *Protecting your Gemini quota* below.

**Browser limits (be aware):** the microphone needs Chrome or Edge (desktop/Android), works on `localhost`/HTTPS, and Chrome/Edge send the audio to their own online
speech service. Safari is partial, Firefox has no speech recognition (typing works). Spoken Hindi needs a Hindi voice installed on the device
(Windows: Settings -> Time & language -> Speech). Browsers only speak after you have clicked on the page - use **Replay answer** if you hear nothing.

## 👗 Virtual try-on & avatar (new tab)
Dress an **illustrated avatar** or **your own photo** in clothes from your wardrobe and see how colours, proportions and layers work together.

* **Avatar:** choose skin tone, hair colour and build, then *Save this avatar* (stored in the database settings table - no migration needed). The avatar is drawn in a plain grey base t-shirt and shorts.
* **Your photo (optional):** upload a front-facing photo, say whether it is full-body or waist-up, and match the neck / shoulder / ankle lines with the sliders (turn on *Show body guide lines*).
  *Save as my avatar photo* keeps it in `data/avatar/` (EXIF/GPS removed, git-ignored); *Delete my saved photo* removes it. Nothing is detected automatically and nothing leaves your computer.
* **Clothes:** pick a top, jacket/coat, bottom, dress/jumpsuit and footwear from wardrobe items that have a photo, press *Use today's suggested wardrobe outfit* (after suggesting an outfit in the Home page), or try a one-off garment photo that is not saved.
  Garment photos need a **plain background that contrasts with the garment**; otherwise the app says the cut-out failed and places the whole photo instead.
* **Honest limits:** this is an approximate **2-D fit preview** (cut-out garment placed on body lines). It does not simulate fabric or your exact pose and is not a size guarantee.
  Garments are never drawn above the neck line, so faces stay uncovered. Waist-up photos skip trousers and shoes with a note.
* **Plug in your own trained try-on model (optional):** write a function `def run(person, garment, category): ...` that takes two RGB `PIL.Image`s and a category text and returns a `PIL.Image`,
  then set `TRYON_ADAPTER=your_module:run` in `.env` (the module must be importable from the folder that contains `app.py`). A *Try-on engine* switch then appears; if your model fails, the built-in preview is shown with the reason.
  Only point this at code you wrote or trust (loading pickled weights runs code).

## Weather source (Open-Meteo or WeatherAPI)
`WEATHER_PROVIDER=auto` (default) keeps using **WeatherAPI.com when `WEATHERAPI_KEY` is set**, and otherwise uses the free key-less **Open-Meteo** forecast + geocoding APIs
(`openmeteo_service.py`), so the whole app - including the voice assistant - works with no weather key. Set `WEATHER_PROVIDER=openmeteo` to force Open-Meteo.
Open-Meteo's free API is for **non-commercial use** (under 10,000 calls/day, 5,000/hour, 600/minute) and its data is CC BY 4.0, so the app shows
"Weather data by Open-Meteo.com" with a link. City names are resolved with Open-Meteo geocoding; write "Bhopal, India" or "Bhopal, Madhya Pradesh" to pick between places with
the same name. Open-Meteo provides no weather alerts (the app says so). Results are cached (`WEATHER_CACHE_MINUTES`, default 10).

## Protecting your Gemini quota
Every Gemini call goes through `gemini_guard.py`:
* `ENABLE_GEMINI=false` (or no key) -> **no Gemini request is ever made**; the app uses its built-in rules everywhere.
* Identical requests are answered from memory for `GEMINI_CACHE_MINUTES` (30) - no repeated calls for the same question.
* A quota error (HTTP 429 / `RESOURCE_EXHAUSTED`) is **never retried**; Gemini is paused for `GEMINI_QUOTA_COOLDOWN_MINUTES` (15, longer for daily limits or if Google says so) and answers
  continue from the rules. "Resume Gemini now" in the tab clears the pause.
* Only temporary errors (timeouts, 500/502/503/504) are retried - at most `GEMINI_MAX_RETRIES` (2) times with exponential back-off (the SDK's own hidden retries are switched off).
* Burst limiter `GEMINI_MAX_CALLS_PER_MINUTE` (10) and optional `GEMINI_MAX_CALLS_PER_DAY` (off). Request timeout `GEMINI_TIMEOUT_SECONDS` (30).
* Spoken output never uses Gemini (browser voices only). Text and image models are configured separately (`GEMINI_TEXT_MODEL`, `GEMINI_IMAGE_MODEL`).
* **Model names (checked against Google's deprecations page, updated 2026-10-22):** `gemini-2.5-flash` and `gemini-2.5-flash-image` are not shut down yet, but Google says
  2.5 text models are now limited to users who already used them, and recommends `gemini-3.5-flash-lite` / `gemini-3.8-flash` for new projects; `gemini-2.5-flash-image` is
  listed to shut down on 2027-03-15 (replacement `gemini-3.1-flash-lite-image`). The defaults therefore try the newer model first and fall back to the old one if your key gets a
  "model not found" error. I could not check **your** key's access, so use the tab's *Check which models my key can use* button and set `GEMINI_TEXT_MODEL` / `GEMINI_IMAGE_MODEL`
  explicitly if you prefer. Model lists change - re-check https://ai.google.dev/gemini-api/docs/deprecations from time to time.
* Counters live in memory (restart = reset); Google counts the real quota.

Settings can live in `.env` or in `.streamlit/secrets.toml` (see `secrets.toml.example`); the environment wins. Keys are never shown in the UI or in error messages.

## Pastel dashboard layout
The look follows the supplied design (`WeatherFit_AI__Pastel_Fashion_Dashboard.png`): white / pink / lavender / sky-blue, soft cards, a sidebar with the page menu, and a three-part Home page.
Styling lives in `ui_theme.py` (base theme + weather cards) and `ui_dashboard.py` (dashboard layout, CSS and HTML builders); `.streamlit/config.toml` sets the Streamlit theme.

* **Sidebar** - brand, page menu (**Home, Try-On, Wardrobe, Style Guide, Voice Assistant**), your preferences (gender, style, occasion, clothing to avoid, *Save Preferences*), plus
  *More preferences*, *Location* and *Trip* panels.
* **Top bar** - city search, live weather chip and *Refresh weather*. Typing a city other than the default uses that city; the browser-location button is in the sidebar's *Location* panel.
* **Home** - hero with **Get started** (fetches the live forecast), **Recommended Outfit** (picture of your real pieces, items, match score, **Try On**), **More Options**, **View All**
  (all options with Worn / Skipped / Favourite), the feature strip, and on the right the live **weather card + style tip**, the **hourly forecast** and **Quick Actions**.
  Everything shown comes from the weather service, the outfit engine or your own wardrobe; before you press *Get started* the cards say so instead of showing sample values.
* **Where the old tabs went** - Today's outfit -> Home; My wardrobe + Outfit history -> Wardrobe; AI stylist chat, Weather chat, Rank outfit images, Generate outfit image, Upload my outfit -> Style Guide;
  Voice Assistant -> Voice Assistant; Virtual try-on (2-D and AR camera) -> Try-On.
* **Differences from the mock-up** - the model photograph, the user profile and the notification bell are not reproduced (no photos or accounts exist in this app); the large picture is a collage of the
  outfit's real pieces. The ring shows the outfit engine's own score in points (a relative score, not a percentage). Icons are emoji/SVG.

### Quick start on Windows (Command Prompt or double-click)
1. Install Python 3.10+ from python.org (tick *Add python.exe to PATH*).
2. Double-click `setup_windows.bat` (creates `.venv`, installs requirements, creates `.env`).
3. Optional: edit `.env` and add `WEATHERAPI_KEY=` and/or `GEMINI_API_KEY=`.
4. Double-click `run_windows.bat`, then open http://localhost:8501.

## Windows PowerShell setup
Open PowerShell in the folder that contains `app.py`:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```
If activation is blocked: `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` and activate again.

In `.env` optionally set `GEMINI_API_KEY=` (and `WEATHERAPI_KEY=` if you prefer WeatherAPI over the free Open-Meteo service). Save the file. Never share it or commit it (`.env` is git-ignored; keys are
never shown in the UI and are scrubbed from error messages by `security.redact`).

### Initialise the database (optional - the app also does this on start)
```powershell
python db.py
```
This creates `weatherfit.db` (path override: `WEATHERFIT_DB` in `.env`) and applies any pending migrations. Before upgrading an existing database a backup
`weatherfit.db.bak-v<N>` is copied next to it; a database from a *newer* app version is refused instead of being changed. Photos go to `data/`
(override: `WEATHERFIT_DATA_DIR`). Both are git-ignored and excluded from the project ZIP.

Run the app:

```powershell
python -m streamlit run app.py
```
Open the URL Streamlit prints (usually http://localhost:8501). Browser geolocation works on `localhost`; on other
hosts the browser needs HTTPS.

## Using the wardrobe and the stylist
1. **My wardrobe tab** - upload photos, review each suggested label, press *Save to my wardrobe*. Use *Edit / delete* per item or *Delete my whole wardrobe*.
2. Set **gender presentation, comfort vs polish and the "avoid repeating for (days)"** value in the sidebar. Press **Get started** on Home.
3. **Home page** - *Suggest from my wardrobe* shows wardrobe options plus an inspiration look. Use **Worn / Skipped / Favourite**
   (only "Worn" counts as worn for item statistics). *Suggest another* gives a different combination than the ones already shown today.
4. **AI stylist chat tab** - examples that work in English, Hindi (Devanagari, common words/cities) and Hinglish:
   * `Aaj Gwalior mein party ke liye kya pehnu?`
   * `Kal maine kya pehna tha? Aaj kuch different suggest karo.`
   * `Mere wardrobe mein blue shirt ke saath kya match hoga?`
   * `Kanaria se Gwalior travel ke liye outfit aur accessories suggest karo.`
   * `Mere paas suitable clothes nahi hain; ek naya look dikhao.`

   Without `GEMINI_API_KEY` (or when Gemini is rate-limited / down) the chat shows a banner and still answers from your data; temporary errors are retried
   automatically and a **Retry with AI** button appears for quota/other errors. Chat history stays in the browser session only (never written to disk).
5. **Outfit history tab** - browse, filter favourites, or clear everything.

### Image generation: what it can and cannot do
* The **item collage** always works (no network): your real photos for wardrobe looks; colour swatches (or a matching dataset reference) for inspiration.
* The **AI look image** uses Gemini's image model. For wardrobe looks your item photos are sent as references, but the model may still change colours,
  patterns, fabric, fit or even drop an accessory - treat it as a rendering, never as proof of how the real clothes look together.
* Inspiration images are labelled *AI-generated inspiration* and are not exact products. Needs a key with image-model access and quota; if it fails you get the
  collage plus a plain-language reason (quota, key, timeout).

### FashionCLIP and "training"
`fashion_clip.ipynb` is unchanged. The same pretrained model (`patrickjohncyh/fashion-clip`) is used for optional category guesses, image embeddings and
similarity search (*My wardrobe -> Visual search*). **No model is trained or fine-tuned on your wardrobe.**

### Importing fashion datasets
```powershell
python dataset_import.py sample          # bundled CC0 synthetic sample, safe
```
Larger sets (Fashionpedia, DeepFashion) require you to download them yourself and pass an explicit licence flag - read **`DATASETS.md`** first.
Imported images are reference-only (`ownership='inspiration'`), kept in `data/external/`, and never counted as clothes you own.

## Run the tests
All normal tests use mocked weather/Gemini responses and temporary databases - no network, keys or quota needed:

```powershell
pip install -r requirements-dev.txt
python -m pytest
```
Live Gemini tests (real API, uses quota) are separate and skipped by default:

```powershell
$env:WEATHERFIT_LIVE_TESTS = "1"      # also needs GEMINI_API_KEY in .env, internet, and google-genai installed
python -m pytest tests_live -v -s
```
Test files: `test_outfit_memory.py` (yesterday exclusion, diversity, wardrobe-only, ownership labels, weather/occasion rules, history),
`test_wardrobe.py` (metadata, uploads, invalid images, deletes, embeddings), `test_db_migrations.py`, `test_stylist_agent.py` (chat context, Hinglish,
API failure fallback, retry), `test_hinglish_visuals_datasets.py`, `test_app_smoke.py` (the Streamlit app renders), plus the earlier suites. New: `test_openmeteo_service.py`, `test_gemini_guard.py`, `test_voice_assistant.py`, `test_config_and_ai_features.py`.

## Optional: FashionCLIP
```powershell
pip install -r requirements-fashionclip.txt
```
Install a PyTorch build for your machine first if needed (https://pytorch.org/get-started/locally/). The model
`patrickjohncyh/fashion-clip` downloads on first use (large; slow on CPU). Your `fashion_clip.ipynb` is unchanged; its
model ID and loading code are reused in `ai_features.py`. If PyTorch has no build for your Python version yet, create
the venv with an older Python (for example `py -3.12 -m venv .venv`).

Add outfit images to `assets/outfits/` (see the README.txt there). No images are bundled.

## API / model requirements
- Weather: nothing is required (free Open-Meteo, non-commercial). Optional WeatherAPI.com key. The WeatherAPI free plan gives about 3 days of hourly forecast, so travel times further out are refused rather
  than guessed. Weather alerts depend on your plan/region; when none are returned the app says so.
- Gemini key (optional): chat wording, photo analysis and image generation. Default models: see *Protecting your Gemini quota* (override in `.env`). Image generation needs a key with access/quota for that model.
- `streamlit-geolocation` for automatic location (optional; manual city always works).

## How recommendations are decided
Base outfit: the original rules in `recommendations.py` applied to the average feels-like temperature over your windows.
Additions in `stylist.py`: umbrella if rain chance ≥ 60 % (compact one at 30–59 %); light jacket if feels-like drops below
22 °C or the temperature swings by ≥ 8 °C; warm jacket at ≤ 12 °C; sunglasses/cap if UV ≥ 6 (or ≥ 30 °C in daylight and dry);
sunscreen at UV ≥ 6; windbreaker at gusts ≥ 28 km/h; water bottle at feels-like ≥ 33 °C; scarf/beanie at ≤ 8 °C.

## Project layout
| File | Purpose |
|---|---|
| `app.py` | Streamlit UI |
| `weather_service.py` | WeatherAPI client: timeouts, retry, cache, errors |
| `location.py` | Browser-vs-manual location resolution |
| `trip.py`, `plan_service.py` | Time windows, arrival time, forecast summaries |
| `stylist.py` | Travel-aware advice with reasons |
| `chat_assistant.py` | Message parsing, follow-ups, grounded answers, Gemini check |
| `preferences.py` | Consent-gated local preference storage |
| `outfit_images.py`, `ai_features.py` | FashionCLIP/Gemini helpers |
| `recommendations.py`, `agent.py` | Original rule engine and fallback chat (kept) |
| `db.py` | SQLite + versioned migrations, transactions |
| `taxonomy.py` | Clothing vocabulary (categories, colours, occasions) shared by everything |
| `wardrobe.py` | Inventory CRUD, safe image storage, label suggestions, embeddings/similarity |
| `outfit_models.py`, `outfit_history.py` | Outfit data classes; SQL outfit memory (worn/skipped/favourite, repeat rules) |
| `outfit_engine.py` | Weather-aware wardrobe recommender + inspiration builder |
| `outfit_visuals.py` | Item collage + optional Gemini look image |
| `stylist_agent.py`, `hinglish.py` | Grounded English/Hindi/Hinglish stylist chat |
| `dataset_import.py`, `make_sample_dataset.py`, `sample_dataset/` | Dataset pipeline and CC0 sample |
| `ui_wardrobe.py` | Streamlit screens for the new features |
| `ui_theme.py`, `.streamlit/config.toml` | Light theme CSS and weather/outfit card components |
| `ui_voice.py`, `voice_component/index.html` | Voice Assistant tab and its browser widget (Web Speech API) |
| `voice_assistant.py` | Voice question understanding + short spoken/written answers (rules first, Gemini optional) |
| `openmeteo_service.py`, `weather_provider.py` | Free Open-Meteo client (same interface as `weather_service.py`) and provider selection |
| `gemini_guard.py`, `config.py` | Quota protection (cache, cool-down, retries, limits, model fallback) and central settings |
| `security.py` | Secret redaction |
| `tryon_engine.py`, `ui_tryon.py` | Avatar + 2-D virtual try-on preview (pure Pillow) and its tab; optional `TRYON_ADAPTER` model hook |

## Known limitations
* Virtual try-on is a 2-D preview with manual body lines on photos (no pose detection, no cloth simulation); see the try-on section.
* Single local user: there are no accounts; anyone using this Windows profile/app instance sees the same wardrobe. Photos aren't encrypted at rest.
* Colour harmony, warmth and formality are transparent heuristics (see `taxonomy.py` / `outfit_engine.py`), not learned taste.
* Hinglish/Hindi understanding is rule-based: common words and city names only; unknown Devanagari city names fall back to your sidebar location or a follow-up question.
  Offline (no-Gemini) fallback replies use Hinglish headings but English reasons.
* The chat saves its *primary* wardrobe and inspiration pick to history; alternative options are saved only if you choose them, so a small wardrobe isn't "used up" by options you never saw.
* Gemini wording is checked for invented weather numbers and for claiming you own inspiration items, but it cannot be fully verified; the structured cards are always the source of truth.
* Gemini label suggestions can be wrong - always check them. The offline colour guess is rough (lighting/backgrounds matter).
* DeepFashion attribute files and Fashionpedia fabric/occasion data are not mapped (not annotated); those fields use defaults.

## Troubleshooting
- `WEATHERAPI_KEY` missing/invalid: `.env` must sit beside `app.py` with `WEATHERAPI_KEY=...`.
- "Location was not detected": allow location in the browser's site settings, or type a city.
- Windows `WinError 32` during pip install: close running Streamlit/VS Code Python processes and retry.


## Merge note: AR Try-On changes
This package combines the Voice v4 project with the AR Try-On patch. The patch adds
`ar_tryon.py`, `ui_ar_tryon.py`, the browser camera component, and AR try-on tests,
and updates `ui_tryon.py` to offer a live-camera AR mode alongside the 2-D preview.
The original project README and environment template are retained to preserve the
full setup guidance and optional API settings.
