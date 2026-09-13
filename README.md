# Multi-Agent AI Research Assistant

A pipeline of AI agents that turns a research question into a well-cited
markdown report, with a **live progress trail** showing each agent's work
in real time.

Built entirely on **free tools**:

| Concern      | Choice                                             |
| ------------ | -------------------------------------------------- |
| LLM          | [Groq](https://console.groq.com) free tier (Llama 3.1 8B + 3.3 70B) |
| Web search   | [Tavily](https://app.tavily.com) free tier, DuckDuckGo fallback |
| Backend      | Python / FastAPI                                   |
| Frontend     | Vanilla HTML/JS + anime.js (served by FastAPI, no build step) |
| Progress     | Server-Sent Events (SSE)                           |
| Persistence  | SQLite                                             |
| Hosting      | Oracle Cloud free tier (Ubuntu 22.04 LTS, Ampere ARM) |

## The pipeline

```
User question
   │
   ▼
┌───────────────┐   ResearchPlan          ┌──────────────────┐
│ Manager agent │ ──────────────────────► │ Researcher agent │
│ decompose into│   (sub_questions[])     │ search + extract │
│ sub-questions │                         │ findings per sq  │
└───────────────┘                         └────────┬─────────┘
                                                    │ ResearchBundle
                                                    ▼
                                          ┌────────────────────┐
                                          │ Synthesizer agent  │
                                          │ markdown report    │
                                          │ + inline citations │
                                          └────────┬───────────┘
                                                   │ SynthesizedReport
                                     (optional)    ▼
                                          ┌────────────────────┐
                                          │  Critic agent      │  ← stretch
                                          │  approve / revise  │
                                          └────────────────────┘
```

Each agent is its own class with a single responsibility and can be
tested independently. The typed handoff contract between stages lives in
[`app/schemas.py`](app/schemas.py).

## Project structure

```
project/
├── app/
│   ├── config.py          # all tunables (models, caps, timeouts) from .env
│   ├── schemas.py         # THE typed handoff contract (Pydantic models)
│   ├── llm.py             # Groq wrapper: timeout, retry, 429 backoff, JSON mode
│   ├── llm.py             # Groq wrapper: timeout, retry, 429 backoff
│   ├── agents/
│   │   ├── base.py        # BaseAgent + robust JSON parse/validate/retry
│   │   ├── manager.py     # Manager agent
│   │   ├── researcher.py  # Researcher agent
│   │   ├── synthesizer.py # Synthesizer agent
│   │   └── critic.py      # Critic agent (stretch, off by default)
│   ├── search/            # Tavily + DuckDuckGo providers + service
│   ├── pipeline.py        # orchestration + progress events
│   ├── runmanager.py      # per-run event queue bridged to SSE
│   ├── store.py           # SQLite persistence
│   └── main.py            # FastAPI app + SSE endpoint + static frontend
├── scripts/
│   ├── smoke_test.py      # offline test suite (no API key needed)
│   ├── test_manager.py    # standalone Manager harness
│   ├── test_researcher.py # standalone Researcher harness
│   └── test_synthesizer.py# standalone Synthesizer harness
├── frontend/
│   ├── index.html         # glassmorphic live-progress UI
│   ├── app.js             # SSE consumer + anime.js motion
│   ├── markdown.js        # tiny vendored markdown renderer
│   ├── styles.css         # glass + depth + animated mesh background
│   └── vendor/
│       └── anime.min.js   # vendored anime.js v3 (no CDN, no build step)
├── requirements.txt
├── .env.example
└── README.md
```

## Run the app

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000, ask a question, and watch the pipeline work.

## Deployment

To deploy on a server (e.g. Oracle Cloud free tier, Ubuntu 22.04) as a
systemd service behind an SSE-aware nginx proxy, follow
[`deploy/DEPLOY.md`](deploy/DEPLOY.md). Deployment artifacts live in
`deploy/` (systemd unit + nginx config), and `requirements.lock.txt`
pins exact dependency versions for a reproducible install.

## Offline test suite

Runs without any API key — covers JSON parsing, search fallback,
researcher error handling, synthesizer citations, SQLite round-trip, and
full pipeline orchestration with fake agents:

```bash
python -m scripts.smoke_test
```

## Setup

```bash
# 1. Create a virtual environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure secrets
cp .env.example .env             # then edit .env, add your GROQ_API_KEY
```

Get a free Groq key at https://console.groq.com/keys.
A Tavily key is optional — without it, search falls back to DuckDuckGo.

> **Model names:** Groq's model catalog changes over time. If a run fails
> with a 404 `model does not exist`, list the models your key can access:
> ```bash
> curl https://api.groq.com/openai/v1/models -H "Authorization: Bearer $GROQ_API_KEY"
> ```
> then set `MANAGER_MODEL` / `RESEARCHER_MODEL` / `SYNTHESIZER_MODEL` /
> `CRITIC_MODEL` in `.env` accordingly. Defaults target `openai/gpt-oss-20b`
> (volume stages) and `openai/gpt-oss-120b` (synthesis).

> **Note:** `.env` is read once at startup. If you edit it while the
> server is running, restart the server to pick up the changes.

## Test the Manager agent standalone

Once your `.env` has a `GROQ_API_KEY`:

```bash
# Built-in sample questions:
python -m scripts.test_manager

# Or your own question:
python -m scripts.test_manager "How does intermittent fasting affect metabolism?"
```

You'll see each sample question broken into stable-id sub-questions
(`sq1`, `sq2`, ...) with rationales.

## The handoff contract at a glance

| Stage        | Input             | Output                         |
| ------------ | ----------------- | ------------------------------ |
| Manager      | `ResearchRequest` | `ResearchPlan`                 |
| Researcher   | `SubQuestion`     | `SubQuestionFindings` (× N)    |
| Synthesizer  | `ResearchBundle`  | `SynthesizedReport`            |
| Critic       | `SynthesizedReport` + `ResearchBundle` | `CritiqueResult` |

Progress is streamed as `ProgressEvent` objects over SSE. See
[`app/schemas.py`](app/schemas.py) for full field definitions.
