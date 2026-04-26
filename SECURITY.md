# Security Policy

## Supported Versions

AgenticQueue 2.0 is under active early development. Security reports should target the current `main` branch unless a released version is explicitly named in the report.

## Reporting a Vulnerability

Please report suspected vulnerabilities through GitHub private vulnerability reporting for this repository:

https://github.com/agenticqueue/agenticqueue-v2/security/advisories/new

If GitHub private reporting is unavailable, email Mario Watson at mariomillions414@gmail.com with a concise description, reproduction steps, affected commit or version, and any relevant logs.

## Response Expectations

We aim to acknowledge valid reports within 7 days. Critical vulnerabilities are prioritized for remediation within 30 days when a fix is under project control. Other reports are handled on a best-effort basis according to severity and project capacity.

## In Scope

- Vulnerabilities in code, workflows, Dockerfiles, or dependency configuration in this repository.
- Leaked secrets, unsafe defaults, authentication bypasses, or security-sensitive data exposure.
- Supply-chain risks that affect this repository's build, test, or release path.

## Out of Scope

- Social engineering, spam, denial-of-service testing, or physical attacks.
- Reports requiring access to systems outside this public repository.
- Vulnerabilities in third-party services unless this repository's configuration directly causes the risk.
- Bug bounty, reward, or CVE assignment requests.

## Coordinated Disclosure

Please do not publicly disclose a vulnerability until we have had a reasonable chance to investigate and, when needed, ship a fix.
