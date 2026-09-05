# Credential storage: what it protects against, and what it does not

ATOS-P0-SEC-001 / ULTRAPLAN section 29.

This document exists because `core/credentials.py` used to describe itself as
"production-grade secrets management" with "keyring support" and "automatic
credential rotation". None of the three was true. A security component whose
documentation overstates it is worse than one with no documentation at all,
because somebody reads the claim instead of the code.

## What is stored, and where

Exchange API keys and secrets, in `.credentials/`:

| File | Contents |
|---|---|
| `credentials.enc` | Fernet-encrypted JSON of every credential, including secrets |
| `credentials.salt` | The PBKDF2 salt. Not a secret, and not useful alone |
| `credentials.json` | Cleartext, **development only**, refused unless explicitly permitted and never for a credential marked production |

All three are in `.gitignore` and `.dockerignore`, and the secret scan in CI
fails the build if any of them is ever tracked.

## Where the master key comes from

In order:

1. `CREDENTIALS_PASSWORD` from the environment. This is the only supported
   source for production.
2. Failing that, a key derived from `USER` and `HOME` via PBKDF2-HMAC-SHA256,
   480,000 iterations.

The second is a **development fallback**. `EncryptionHelper.initialize()`
refuses it when called with `production=True`, and logs a warning every time
it is used.

### Why the fallback is weak, precisely

`USER` and `HOME` are readable by anyone with a shell on the host and
guessable by anyone who knows the deployment. The derivation is strong — the
input is not. Four hundred and eighty thousand PBKDF2 iterations over a value
an attacker can simply read is four hundred and eighty thousand iterations of
nothing.

### A bug this fallback used to have

The derivation also included `os.getpid()`. The process id changes every run,
so the master key changed every run, and credentials encrypted by one process
could not be decrypted by the next. The failure was silent: the load path
caught the decryption error, printed an empty message — Fernet's
`InvalidToken` stringifies to nothing — and returned an empty credential list.
A system that reported storing its keys securely and then reported having
none. Fixed; the PID is gone and the load path now says what happened.

## Threat model

### Defended

**A credential file that leaves the machine without the environment that
unlocks it.** A backup, a copied disk, a laptop, a repository commit. This is
the common exposure and the one encryption at rest actually answers. An
attacker with `credentials.enc` and `credentials.salt` but not
`CREDENTIALS_PASSWORD` has a PBKDF2 problem.

**A credential appearing in a log, an alert or an error message.** Alert
payloads are redacted on egress (`core/alerting.py`), the access log records
actions and names but never values, `list_credentials()` returns metadata
only, and the load path logs exception *types* rather than payloads.

### Not defended

**Anyone who can run code as this user.** They can read
`CREDENTIALS_PASSWORD` from the process environment, or simply ask this module
to decrypt. There is no defence here against a compromised host, and none is
claimed.

**A memory dump.** Decrypted secrets live in Python strings for the process's
lifetime.

**The development fallback against a local attacker.** See above.

## Rotation

There is none. `expires_at` marks a credential stale *locally* and changes
nothing at the provider. `CredentialsManager.rotate()` raises
`NotImplementedError` naming that fact.

This is deliberate. A `rotate()` that re-saved the same key would leave an
operator believing a leaked credential had been replaced, and they would stop
looking for the leak — which is a worse position than knowing rotation is
manual.

To rotate: revoke and reissue in the provider's console, then replace the
value here.

## What production should actually do

Read secrets from a secret manager the application can read and cannot write —
AWS Secrets Manager, GCP Secret Manager, Vault, or the platform's injected
environment. `CREDENTIALS_PASSWORD` sourced that way, with this module used
only as a local cache, is a reasonable arrangement. This module managing its
own plaintext or its own locally-derived key is not.

## Live credentials specifically

Live activation additionally requires (`core/live_authorization.py`):

- the credential source is one of the approved ones;
- the broker account fingerprint matches the one the deployment expects;
- the environment designation is explicit, never inferred.

`from_environment()` no longer guesses. It used to label a credential
"production" whenever no prefix was passed, which is a live key wearing a
label the code chose for it.
