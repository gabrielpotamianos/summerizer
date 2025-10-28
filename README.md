# Mattermost Summarizer

A desktop service for macOS that continuously monitors your Mattermost account
for unread group conversations, stores the raw messages, generates summaries
with a local LLaMA model, and displays them in a native-feeling Python (PyQt)
application.

## Features

- Polls the Mattermost REST API with a personal access token
- Persists unread channel transcripts and summaries on disk
- Uses a local LLaMA model (via `llama-cpp-python`) to build concise recaps
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
   - `mattermost.token`: Your personal access token
   - `mattermost.polling_interval`: How often to check for new messages (seconds)
   - `mattermost.storage_dir`: Where to store transcripts and summaries
   - `llm.model_path`: Path to your local LLaMA compatible GGUF/GGML model
   - `llm.threads`: Optional number of CPU threads for inference

3. **Run the desktop application**

   ```bash
   python main.py config.json
   ```

   The background service runs alongside the UI. As unread conversations arrive,
   the summaries are refreshed automatically.

## Project layout

- `main.py` – Application entry point
- `summarizer/config.py` – Configuration dataclasses
- `summarizer/mattermost.py` – Mattermost REST client
- `summarizer/llm.py` – Local LLaMA wrapper and message collation helper
- `summarizer/storage.py` – File-system persistence helpers
- `summarizer/service.py` – Background worker thread orchestrating everything
- `summarizer/ui.py` – PyQt-based user interface

## Dependencies

See `requirements.txt` for the full list. Notable packages:

- `requests` for HTTP calls
- `llama-cpp-python` for local LLaMA inference
- `PyQt6` for the macOS desktop UI

## Notes

- The application stores per-channel data in the configured `storage_dir` using
  safe filesystem names so that emojis or spaces do not break persistence.
- Channel acknowledgement is best-effort; adjust the logic in
  `MattermostClient.acknowledge_channel` to fit your workflow.
