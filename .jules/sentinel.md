## 2024-03-24 - [Replace Weak Randomness with Cryptographic Secrets]
**Vulnerability:** [Weak random number generation used in backoff delay]
**Learning:** [Using `random` module for generating backoff delay jitter is flagged as a generic security anti-pattern (CWE-322/B311: "Weak random number generation"). While not strictly used for cryptographic secrets here, the presence of `random` import is poor hygiene and replacing it with `secrets.SystemRandom().uniform()` ensures cryptographically strong randomness.]
**Prevention:** [Always use `secrets` instead of `random` to generate random values, especially when working on security-related tasks.]

## 2024-05-24 - [Strict URL Credential Redaction]
**Vulnerability:** URL credentials were only redacted if a password attribute existed on the parsed URL. In HTTP Basic Auth, tokens are frequently provided as the username field without a password (e.g., https://[token-example]/). The previous redaction function leaked these token-based credentials into error logs.
**Learning:** Checking for just password is insufficient to protect bearer tokens or API keys passed via URL.
**Prevention:** Always verify both username and password components, and mask both entirely to prevent token leakage in structured logs.
