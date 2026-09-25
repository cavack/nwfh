# NWFH

Python application project.

## Setup

1. Copy env file:

   cp .env.example .env

2. Create virtual environment:

   python3 -m venv .venv

3. Activate it:

   source .venv/bin/activate

4. Install development dependencies:

   pip install -e ".[dev]"

## Quality checks

ruff check .
pytest
