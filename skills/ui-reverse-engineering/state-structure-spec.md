# State Structure Spec — Step 6-state

Compact state-machine evidence for page-load, scroll, hover, and click interactions.

## Rule

The scripts are orchestration wrappers. The event source of truth is the live `agent-browser` page:

- splash/page-load: open a fresh browser session and observe DOM/class/style state during navigation;
- scroll: move the real page with `window.scrollTo` or the detected scroll engine inside the browser context, then wait for stability;
- hover: prefer real pointer hover (`agent-browser hover <selector>`) for settled/visual proof; CSSOM and synthetic mouse events are only candidate/runtime-handler probes;
- click: use `agent-browser click <selector>` in a throwaway session per safe candidate; record non-HTTP schemes, downloads, and `_blank` targets as declared navigation without activating them.

Never infer a dynamic state from raw HTML alone. Static HTML/CSS/JS can explain why a state exists, but only browser-observed state artifacts can claim that the state fires.

## Producer

Run after any state capture phase:

```bash
python3 scripts/extract/state-structure-spec.py tmp/ref/<component>
```

The phase scripts call this post-pass automatically:

- `capture-states.sh` → `states/splash/*`
- `capture-scroll.sh` → `states/scroll/*`
- `capture-hover.sh` → `states/hover/*`
- `capture-click.sh` → `states/click/*`

Output:

```txt
tmp/ref/<component>/state-structure-spec.json
```

## Schema contract

`state-structure-spec.json` is derived, compact, and safe for the main context:

```json
{
  "schemaVersion": 1,
  "producer": "scripts/extract/state-structure-spec.py",
  "contract": "derived-from-agent-browser-state-artifacts",
  "events": [
    {
      "id": "click:nav",
      "phase": "click",
      "trigger": "click",
      "eventDriver": "agent-browser.click",
      "selector": "a.external",
      "navigationType": "external",
      "navigationOnly": true,
      "guard": {
        "isolatedSession": true,
        "urlBefore": "https://ref.example/",
        "urlAfter": "https://external.example/",
        "restored": true
      },
      "domMutation": { "changed": false },
      "artifacts": ["states/click/manifest.json", "states/click/click-nav.json"]
    }
  ],
  "phases": {
    "splash": { "present": true, "eventCount": 1 },
    "scroll": { "present": true, "eventCount": 6 },
    "hover": { "present": true, "eventCount": 4 },
    "click": { "present": true, "eventCount": 2 }
  }
}
```

Do not place `outerHTML`, `fullHTML`, full CSS, bundle text, screenshots, or videos in this file. Keep those in phase artifacts and delegate raw inspection to `source-forensics` when compact evidence is insufficient.

## Click guard

Click capture must be defensive:

1. Discover candidates in a browser session.
2. For each candidate, open a fresh `agent-browser` session (`<session>-click-N`).
3. Skip `mailto:`, `tel:`, `sms:`, `javascript:`, `data:`, `blob:`, `file:`, downloads, and `_blank` targets; write them as `declaredOnly` / `navigationOnly` so they do not open external apps, downloads, or tabs.
4. Snapshot before click.
5. Run `agent-browser click <selector>`.
6. Snapshot after click and read `agent-browser get url`.
7. If URL changed, classify it:
   - `external`: different origin; record as `navigationOnly`, do not claim DOM mutation.
   - `same-origin-navigation`: different path; record navigation, not same-page state.
   - `hash-navigation`: fragment-only movement; record navigation-only unless DOM mutation is separately observed.
   - `same-page`: eligible for DOM/class/style mutation proof.
8. Restore via `agent-browser back`; if the origin or path is still wrong, reopen the reference URL.

This prevents a click candidate from polluting later candidates or accidentally crawling off-site.

## Consumers

- Main clone generation reads `state-structure-spec.json` before raw `states/**`.
- `transition-spec-rules.md` consults it for transitions that depend on DOM/class/content swaps.
- `source-forensics.md` reads it before raw bundles/CSS/HTML when compact state evidence cannot explain a failing gate.
- `verification-plan.sh` treats it as a staleness input so downstream checks regenerate after state rollup changes.
