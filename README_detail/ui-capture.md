# `ui-capture` — Visual Capture & Reference

Captures baseline screenshots and transition videos. With a local URL, it captures matching implementation evidence for downstream verification, then hands mismatch diagnosis to `visual-debug`.

Classifies each effect by trigger type before recording — prevents blank videos from wrong activation methods.

**Usage:**

```
Capture the transitions from https://example.com
Record the hover effects on https://example.com
Capture matching implementation evidence for https://example.com and http://localhost:3000
Take a baseline of https://example.com before I start cloning
```

**Pipeline:**

```
Phase 1:  Full page capture        — section screenshots + full scroll video
                                     auto-detects custom scroll (Lenis, Locomotive)
Phase 2:  Transition detection     — classify all effects by trigger type → regions.json
Phase 2B-2E: Capture per trigger type:
  2B scroll-driven   — exploration video → clip screenshot before/mid/after
  2C css-hover       — eval + clip screenshot: idle + active
     js-class        — eval classList.add + clip screenshot: idle + active
     intersection    — eval classList.add + clip screenshot: before + after
  2D mousemove       — raster-path sweep video
  2E auto-timer      — passive recording for 2-3 cycles

local-url provided?
├── YES → Phase 3: Implementation evidence capture
│         Phase 4A: Handoff to `visual-debug` Phase D pixel-perfect gate
│         Phase 4B: compare.html human review artifact
│         Phase 5:  User review
└── NO  → Phase R:  report.html (overlay-based analysis report)
          Phase 5:  User review
```
