# Injection fixtures (roadmap M5.12)

Each file under `src/main/java/com/acme/` carries one hostile pattern that the MCP
safety envelope must flag (and, for `Config.java`, mask) when its source is served:

| File | Pattern | Expected |
|---|---|---|
| `Billing.java` | instruction to an AI agent + `curl … \| sh` in a comment | `instruction` flag |
| `Hidden.java` | right-to-left override (U+202E) hiding text in a comment | `hidden_unicode` flag |
| `Blob.java` | long base64 run in a comment | `base64_comment` flag |
| `Config.java` | hard-coded password literal | secret masked, no flag |
| `Clean.java` | nothing | no flag, nothing masked |
