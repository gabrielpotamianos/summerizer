# Mattermost Summarizer

A desktop service for macOS that continuously monitors your Mattermost account
for unread group conversations, stores the raw messages, generates summaries
via Groq's hosted LLaMA-3.3 70B model, and displays them in a native-feeling
Python (PyQt) application.

## Features

- Prompts for your Mattermost credentials and reuses the issued token for API calls
- Persists unread channel transcripts and summaries on disk
- Uses Groq's `llama-3.3-70b-versatile` chat model to build concise recaps
- macOS-friendly Qt user interface that highlights unread channels

## Getting started

1. **Install dependencies**

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure the service**

   Copy `config.example.json` to `config.json` and update the values:

   - `mattermost.base_url`: Base Mattermost URL (the script appends `/api/v4`)
   - `mattermost.polling_interval`: How often to check for new messages (seconds)
   - `mattermost.storage_dir`: Where to store transcripts and summaries
   - `llm.api_key`: (Optional) Groq API key; falls back to the `GROQ_API_KEY` env var
   - `llm.endpoint`: Groq-compatible chat completion endpoint
   - `llm.model_name`: Chat model name (defaults to `llama-3.3-70b-versatile`)
   - `llm.request_timeout` / `llm.max_retries`: Network tuning knobs for the Groq calls
   - `llm.inter_request_delay`: Minimum seconds to wait between Groq calls
   - `llm.ca_bundle`: (Optional) Path to a custom CA bundle for HTTPS verification

3. **Run the desktop application**

   ```bash
   python main.py config.json
   ```

   The application first opens a login window so you can provide your Mattermost
   username/email and password. After a successful login the background service
   runs alongside the summary UI. As unread conversations arrive, the summaries
   are refreshed automatically.

## Project layout

- `main.py` – Application entry point
- `summarizer/config.py` – Configuration dataclasses
- `summarizer/mattermost.py` – Mattermost REST client
- `summarizer/llm.py` – Groq chat wrapper and message collation helper
- `summarizer/storage.py` – File-system persistence helpers
- `summarizer/service.py` – Background worker thread orchestrating everything
- `summarizer/ui.py` – PyQt-based user interface

## Dependencies

See `requirements.txt` for the full list. Notable packages:

- `requests` for Groq/OpenAI-compatible HTTP calls
- `PyQt6` for the macOS desktop UI

## Notes

- The application stores per-channel data in the configured `storage_dir` using
  safe filesystem names so that emojis or spaces do not break persistence.
- Channel acknowledgement is best-effort; adjust the logic in
  `MattermostClient.acknowledge_channel` to fit your workflow.
