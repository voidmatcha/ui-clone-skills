# `visual-debug` — All Visual Verification in One Skill

The single source of truth for "is it done?" — covers automated AE/SSIM diff, pixel-perfect gating, self-healing fix loops, and VLM sanity checks in one place.

**Two modes:**

- **Quick comparison** — `scripts/verify/auto-verify.sh` runs D0 layout health check → batch-scroll capture → AE comparison → post-implement gate in one command. Zero vision tokens (AE/SSIM only, no LLM screenshot reads).
- **Full verification** — `verification.md` with Phase A/B capture → Phase C comparison → Phase D0 layout health → Phase D pixel-perfect gate → Phase H self-healing loop → Phase E LLM review. Phase E reads a single diff image when something fails, so full verification does use vision tokens.
