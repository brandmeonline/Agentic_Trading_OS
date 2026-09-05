# Runbook — Credential Incident

Covers any credential that reached a place it should not have: a git commit, a
log, an issue, a screenshot, a chat message, a CI artifact, or a third party.

**Governing rule:** a credential that has ever been exposed is compromised.
Deleting the file, force-pushing, or rewriting history does not undo exposure.
Rotation at the provider is mandatory and is the only step that actually
removes the risk. Everything else is cleanup.

This applies to paper-only keys. A paper key still identifies the account,
still permits API enumeration, and is frequently reused or upgraded to live.

---

## 1. Revoke (owner, immediate)

Only the account owner can do this. It cannot be done from the repository.

1. Sign in to the provider (for Alpaca: <https://app.alpaca.markets/>).
2. Delete the exposed key. Do not merely regenerate alongside it — the old key
   must stop working.
3. Confirm the old key is gone from the provider's key list.

Do this before any cleanup work. Cleanup without revocation leaves the risk in
place while creating the appearance that it was handled.

## 2. Assess exposure

- How long was the credential exposed, and in what (public repo, log, chat)?
- What could it do — read-only, paper trading, live trading, withdrawal?
- Check the provider's account activity for the exposure window: unexpected
  orders, positions, transfers, API calls from unknown addresses.
- If the account holds real capital, treat unexplained activity as an
  incident in its own right and freeze trading (see the kill/flatten runbook).

## 3. Replace

1. Generate a new key only if it is still needed.
2. Store it outside git:
   - environment variables (`ALPACA_API_KEY`, `ALPACA_API_SECRET`), or
   - GitHub Actions secrets for CI, or
   - an OS keyring / production secret manager.
3. Never paste the replacement into chat, an issue, a commit, a PR
   description, a log line, a screenshot, or a test fixture.

## 4. Clean the repository

1. Confirm no credential file is tracked:
   `git ls-files | grep -i credential`
   Only `*.example.json` files may appear.
2. Confirm the real paths are ignored:
   `git check-ignore -v "Alpha IO/config/alpaca_credentials.json"`
3. Run the scanner: `python -m pytest -q "Alpha IO/tests/test_secret_hygiene.py"`
4. Grep logs and CI artifacts for the exposed value; purge what you find.

## 5. History (optional, after revocation)

Rewriting public git history with `git filter-repo` or BFG may be desirable to
reduce casual discovery, but:

- it does not un-publish anything already cloned, forked, or indexed;
- it breaks every existing clone and open pull request;
- it is **not** a substitute for step 1.

Only consider it after revocation is confirmed, and coordinate with anyone
holding a clone.

## 6. Close out

- Record what leaked, when, for how long, and what the key could do.
- Note whether any account activity was unexplained.
- Confirm the secret scan is green and gating CI.
- If the leak path was structural (a loader defaulting to a tracked file, a log
  line printing a secret, an error message interpolating config), fix the path
  itself. A rotated key committed the same way next month is the same incident.

---

## Known incident — ATOS-P0-SEC-001

`Alpha IO/config/alpaca_credentials.json` was committed to the public
repository containing a real Alpaca paper key (26 chars) and secret (44 chars).

Repository-side remediation is complete: the file is deleted and gitignored,
loaders read from the environment, error paths no longer interpolate
credential content, and a blocking secret scan runs in CI.

**Step 1 remains outstanding and only the owner can perform it.** Until that
key is revoked at Alpaca, treat the account as compromised. Per ULTRAPLAN
Gate 0, no live-capital work proceeds until revocation is confirmed.
