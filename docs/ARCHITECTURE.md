# Architecture

## Rules

- Application source code is located in `src/nwfh`.
- Tests are located in `tests`.
- Secrets must exist only in the server-side `.env` file.
- Never commit `.env`, credentials, logs, databases, cache, or runtime data.
- Run lint and tests before every push.
