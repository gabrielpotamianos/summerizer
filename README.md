# summerizer

A desktop service for macOS that monitors your Mattermost account, archives unread channel messages, and summarizes them with a local LLaMA model. The summaries are displayed in a native Python UI built with PySide6.

## Features

- Polls Mattermost with a personal access token to find only group conversations you belong to that are truly unread.
- Stores raw unread messages and structured snapshots on disk using the channel name.
- Resolves author display names, stores the raw transcripts, and summarizes unread conversations locally with [`llama-cpp-python`](https://github.com/abetlen/llama-cpp-python).
  A deliberate "thinking" pass precedes each summary so the final brief is concrete and actionable.
- Presents the most recent summaries in a responsive macOS-friendly PySide6 application.

## Project layout

```
.
├── config.yml              # Optional YAML configuration
├── requirements.txt        # Python dependencies
├── src/
│   └── summerizer/
│       ├── config.py       # Configuration helpers and dataclasses
│       ├── main.py         # Application entry point
│       ├── mattermost_client.py  # REST client for Mattermost
│       ├── service.py      # Background monitor & summarization pipeline
│       ├── storage.py      # Persistence utilities
│       └── ui.py           # PySide6 desktop interface
```

## Getting started

1. Create and activate a Python 3.11 (or newer) virtual environment.
2. Install the dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Download a compatible LLaMA model and note its path on disk.
4. Provide configuration via environment variables or a `config.yml` file.

### Configuration

You can store secrets safely using environment variables, or create a YAML file with the following structure:

```yaml
mattermost:
  base_url: "https://mattermost.example.com"
  token: "YOUR_PERSONAL_ACCESS_TOKEN"
summarizer:
  model_path: "/path/to/your/llama/model.bin"
  # Optional overrides:
  # prompt_template: |
  #   Custom prompt with {content} and {analysis} placeholders
  # analysis_template: |
  #   Custom reflective prompt with {content}
  # max_tokens: 512
  # temperature: 0.2
storage:
  data_dir: "~/Library/Application Support/MattermostSummaries"
poll_interval: 120  # seconds between checks
```

Equivalent environment variables:

- `MATTERMOST_BASE_URL`
- `MATTERMOST_TOKEN`
- `LLM_MODEL_PATH`

### Running the application

```bash
python -m summerizer.main --config /path/to/config.yml --log-level INFO
```

When launched, the monitor runs in the background and the UI displays each channel with unread activity. Selecting a channel shows the most recent summary. Raw message logs and JSON snapshots are written to the configured `data_dir`.

## Notes

- The service uses the Mattermost v4 REST API. Ensure the provided token has permissions to read channel membership and posts.
- Summaries run entirely on your machine via `llama-cpp-python`. Adjust the prompt, max tokens, or temperature in the configuration if you need more concise or creative results.
- The PySide6 interface is optimized for macOS but runs anywhere PySide6 is supported.
