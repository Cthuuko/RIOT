# Moved → [DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md)

Examples A–F and the Variant cookbook from this file now live in one
per-device guide, as a single **combination matrix** with steps:

| You want | Go to |
|---|---|
| The samr21-xpro walkthrough + full combination matrix | **[DEVICE_SAMR21_XPRO.md](DEVICE_SAMR21_XPRO.md)** |
| Host prerequisites, keys, publish/notify | [SETUP_COMMON.md](SETUP_COMMON.md) |
| Concepts and board choice | [GUIDE.md](GUIDE.md) |
| Pitfalls and error codes (the old "Gotchas" sections) | [GOTCHAS.md](GOTCHAS.md) |
| RAM feasibility tables and verification status | [FINDINGS.md](FINDINGS.md) |

Mapping from the old example labels:

| Old | New |
|---|---|
| Example A (Ed25519) | matrix rows 1–4 |
| Example B (ML-DSA-65) | rows 10–12 + the worked example |
| Example C (ML-DSA-44) | rows 8–9 |
| Example D (ML-DSA-87) | rows 17–18 — ❌ confirmed infeasible |
| Example E (encrypted manifests) | rows 2, 4–7, 9, 11 (step D.1) |
| Example F (encrypted payloads) | rows 3–7, 9 (payload-enc column) |
| Variant cookbook / Quick reference | the matrix itself |
