# Local patches

Source: github.com/michaelshimeles/skills at 4b72f46b045e6fef52e6a98d4c162dd309826aed, fetched 2026-10-04.
Installed: new-feature, code-structure, evidence-driven-testing, before-and-after. Not installed: unslop (the humanizer MCP covers it), greploop and greploop-apps (no third-party review of this code, decided 2026-10-04).

Changes from upstream, all in before-and-after, so screenshots are never public by default:
- scripts/upload-and-copy.sh: default IMAGE_ADAPTER is gist, not 0x0st.
- scripts/adapters/gist.sh: creates secret (unlisted) gists, not --public. Secret gists are readable by anyone with the URL.
- SKILL.md: never pass --markdown to the CLI without --upload-url; upload through the script instead.
