# Contributing to OpsPilot

Thank you for your interest in contributing to OpsPilot!

## Development Setup

```bash
git clone https://github.com/iitdeveloper-git/opspilot.git
cd opspilot

# Create venv & install dev dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[ai,dev]"

# Run tests
pytest
```

## Pull Request Guidelines

1. Fork the repo and create your branch from `main`.
2. Ensure all tests pass (`pytest`).
3. Follow conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
4. Open a PR with clear description of the problem solved.
