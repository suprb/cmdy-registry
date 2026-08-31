# Security policy

## Reporting a vulnerability

Please do not open a public issue for a vulnerability or suspected credential
exposure. Use GitHub's private vulnerability reporting for this repository once
the public repository enables it, or contact the maintainer through an existing
private channel. Include affected entry IDs and versions, reproduction steps,
impact, and any proposed mitigation. Do not include live credentials.

Reports will be acknowledged as soon as practical. A fix may remove an entry,
publish a new version and archive hash, or revoke a compromised integration.
Published archives are immutable; they are never silently replaced under an
existing version.

## Scope

The registry validator, native archives, Channel source, shader compiler
boundary, and configuration handling are in scope. Provider service behavior
and vulnerabilities in the cmdy application itself should be reported to their
respective maintainers.

The launch catalog intentionally preserves `dev.termite.*`, `termite.*`, and
selected `TERMITE_*` identifiers for compatibility. Their presence alone is not
a vulnerability or evidence of a retired runtime dependency.
