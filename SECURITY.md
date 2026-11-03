# Security Policy

## Supported Versions

Security fixes are made against the latest version on the `main` branch. Older releases may not receive fixes.

## Reporting A Vulnerability

Please do not report security vulnerabilities in public issues. Use GitHub's private vulnerability reporting for this repository when it is available. If private reporting is unavailable, contact the repository maintainers privately before sharing technical details.

Do not include live cookies, tokens, passwords, proxy credentials, or private request data in a report unless the maintainers provide a secure channel and the information is necessary.

## Local Credential Storage

CurlDesk currently stores request collections and history in a local SQLite database without encrypting credentials. Treat `%APPDATA%\CurlDesk\curldesk.sqlite3` as sensitive and protect it accordingly.
