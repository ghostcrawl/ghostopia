# Security policy

We take security reports seriously and appreciate your help.

## Reporting a vulnerability

Please report security issues **privately** using GitHub's private vulnerability reporting:

[Report a vulnerability](https://github.com/ghostcrawl/ghostopia/security/advisories/new)

Include a description of the issue, steps to reproduce, the affected version and any potential impact. You should receive an acknowledgement within a few business days.

**Do not open a public issue or discussion for a security vulnerability.**

## Secrets

Never include API keys, tokens, JWT secrets or other credentials in issues, pull requests, commits or screenshots. If you believe a credential has been exposed, rotate it immediately and report it privately using the link above.

Keep your own `.env` file out of version control; the repository `.gitignore` already excludes it.

## Supported versions

Security fixes are applied to the latest published release. Please update to the current version before reporting an issue.
