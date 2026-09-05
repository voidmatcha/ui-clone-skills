(() => {
  // Only executable JavaScript. A bare <script> defaults to JS; typed ones are
  // JS only for the module/classic type tokens. application/ld+json, importmap
  // and template types carry no motion code and would just add noise.
  const JS_TYPES = new Set([
    "",
    "module",
    "text/javascript",
    "application/javascript",
    "text/ecmascript",
    "application/ecmascript",
    "module/javascript",
  ]);
  const MAX_BYTES = 2 * 1024 * 1024;

  // querySelectorAll does not cross shadow boundaries, so a script inside a
  // shadow root is invisible to a plain document query — verified in a
  // controlled probe: the plain query missed it, a piercing walk found it.
  // No site captured so far actually does this (0 shadow hosts across three
  // production pages), so this is insurance rather than a measured hit.
  const collect = (root, out) => {
    for (const el of Array.from(root.querySelectorAll("script:not([src])"))) {
      out.push(el);
    }
    for (const el of Array.from(root.querySelectorAll("*"))) {
      if (el.shadowRoot) collect(el.shadowRoot, out);
    }
    return out;
  };

  const scripts = [];
  const skipped = [];
  let index = 0;

  for (const el of collect(document, [])) {
    const rawType = (el.getAttribute("type") || "").trim().toLowerCase();
    const body = el.textContent || "";
    if (!JS_TYPES.has(rawType)) {
      skipped.push({ reason: "non-js type", type: rawType, bytes: body.length });
      continue;
    }
    if (!body.trim()) {
      continue;
    }
    if (body.length > MAX_BYTES) {
      skipped.push({ reason: "over size cap", type: rawType, bytes: body.length });
      continue;
    }
    scripts.push({
      index: index++,
      type: rawType || "text/javascript",
      module: rawType === "module",
      bytes: body.length,
      body,
    });
  }

  return { url: location.href, scripts, skipped };
})()
