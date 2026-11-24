# Account API cache

With `GALAXY_ACCOUNT_STORAGE=files-v1`, the backend resolves the authenticated
user through `Accounts`, creates `accounts/<registered-directory>/cache`, and
supplies `cache_directory` and `cache_identity` to trusted CLI subprocesses over
stdin. Neither field is accepted in model tool arguments. The DSH worker resolves
the same account from its runtime owner registry and mounts only this cache at
`/account-cache` using `--ro-bind`. No parent account directory is exposed.

Youzan `order-search query` with `all_pages=true` streams native page responses to
`youzan/orders/<shop-id>/<cache-id>.jsonl` inside the cache. It returns a small
receipt with `sandbox_path`, `metadata_path`, `complete`, count, size, timestamps,
and SHA-256. The adjacent atomic manifest also records the user/workspace, shop,
and normalized filters (including the time basis). It never records credentials.
Each line is a full page response; for this adapter the orders are in `data.data`.

DSH reads `sandbox_path` directly, checks the manifest and `complete=true`, and
writes generated reports to `/workspace` for normal publication. Large source
files do not consume DSH checkpoint space or the model context. A mount made
before a CLI call can see new files as the backend creates them. Same-account
conversations share the cache; their execution workspaces remain separate.

`xingxu.tool_cache` is the shared authenticated return boundary for every
business tool: Youzan, Guanjia, Feishu, account status, ERP, HAR analysis, schedules,
and future registered platforms. After normal permission checks, validation,
credential redaction, and business-status evaluation, the full original JSON goes
to `<category>/<tool>/<cache-id>.json`. The model and chat history receive only a
file receipt with format, scope/coverage, status, size, hash, and paths. This also
applies to single-page results. Original cursor/pagination fields remain in the
file; `coverage=tool_response` does **not** mean every page was fetched, and
`complete` means the response file was completely saved. Failed or partial
business results remain explicitly marked and must be inspected before use.

Existing Youzan bulk-order JSONL receipts are reused without copying. Feishu's
legacy `file-download` output is transferred into a binary cache entry, retaining
its original filename in metadata, and its redundant work copy is removed. DSH
can parse that binary directly; to publish it, copy it into `/workspace` first.

Agent/UI control responses stay inline: help (including Feishu action help),
time, user questions, upload tickets, generated-file and publication receipts.
This keeps upload buttons and downloads functional. Successful mutations still
report completion if cache storage subsequently fails, preventing blind retries.
Cancelled or revoked returns discard newly created generic cache files. The
generic writer runs off the event loop and cooperatively cancels while waiting
for its account lock.

The manifest records a bounded, credential-redacted request plus the hash of the
full canonical arguments and shop scope. Credentials supplied as ERP input are
never recorded in plaintext in the manifest; uploaded HAR content in the private
result file remains available to its owner as required for analysis. Each call
creates a fresh snapshot; there is no implicit stale-query reuse or pagination
resume. Business CLI output is bounded at 64 MiB per call (the streaming bulk
order adapter can write a larger dataset within the account cap). This is separate
from the small amount of metadata returned to the model.

Snapshots are kept for seven days. Cleanup runs on the next write, under a
per-account lock, and skips no path-security checks. The account cache is capped
at 512 MiB including manifests, with reserved manifest overhead. A full cache
fails rather than evicting fresh data; a stopped/failed export remains incomplete.
Expired snapshots may remain on disk until another write occurs. Uploaded and
published files are independent of this disposable cache.

Verification covers native page preservation, query/scope metadata, hashes,
partial failures, expiry, quota, path/link rejection, owner mapping, and read-only
mount arguments. Deployment verification also launched the real bubblewrap jail,
created a 33 MiB synthetic snapshot after mounting, verified its hash inside the
jail, and checked that writes, deletes, and host account access were blocked. It
then removed the synthetic snapshot. The same live mount was verified with JSON
receipts from the universal Guanjia/Feishu/Youzan return boundary using synthetic
business data, without upstream business requests or mutations. These local
deployment probes are excluded from source control; portable regression coverage
is in `backend/tests/test_account_cache.py`, `backend/tests/test_tool_cache.py`,
`galaxy-cli/tests/test_order_search.py`, and `galaxy-cli/tests/test_request_cache.py`.
