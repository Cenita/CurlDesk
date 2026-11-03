# Contributing

Thank you for considering a contribution to CurlDesk.

## Before You Start

- Search existing issues before opening a new one.
- For a significant change, open an issue first to discuss the intended behavior.
- Never include real cookies, tokens, passwords, proxy credentials, private URLs, or request database files in issues, pull requests, screenshots, or tests.

## Development Setup

CurlDesk currently targets Windows and Python 3.10.

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Run the application with `./run.ps1`. Run the test suite with:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Pull Requests

- Keep changes focused and describe the user-visible behavior.
- Add or update tests for behavior changes.
- Run the test suite before submitting and include the result in the pull request.
- Update both `README.md` and `README_EN.md` when user-facing behavior or setup changes.
- Do not commit virtual environments, build output, local databases, logs, or generated request data.
