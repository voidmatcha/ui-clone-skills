"use strict";

const data = JSON.parse(process.argv[2]);
console.log("| # | tag | class | box | init opacity | init transform | post opacity | post transform | clipping ancestor |");
console.log("|---|-----|-------|-----|--------------|----------------|--------------|----------------|-------------------|");
data.forEach((s, i) => {
  const clipped = s.chain.find((c) => c.overflowHidden);
  const clip = clipped ? (clipped.tag + (clipped.cls ? "." + clipped.cls.split(" ")[0] : "")) : "—";
  const initT = s.init.transform.length > 28 ? s.init.transform.slice(0, 28) + "…" : s.init.transform;
  const postT = s.post.transform.length > 28 ? s.post.transform.slice(0, 28) + "…" : s.post.transform;
  const cls = s.cls.split(" ")[0] || "";
  console.log("| " + i + " | " + s.tag + " | " + cls + " | " + s.box + " | " + s.init.opacity + " | " + initT + " | " + s.post.opacity + " | " + postT + " | " + clip + " |");
});
console.log("");
console.log("Likely root cause for entries with a clipping ancestor:");
console.log("  IntersectionObserver was attached to the TRANSFORMED CHILD, not the");
console.log("  non-moving outer wrapper. The transform pushed the observed element");
console.log("  outside the overflow:hidden box, so the visible intersection rect is");
console.log("  empty and IO returns intersect:false forever.");
console.log("");
console.log("Fix: split the component into outer (IO ref + overflow:hidden) and inner");
console.log("     (the moving element). See:");
console.log("       ui-reverse-engineering/transition-implementation.md");
console.log("         → IntersectionObserver placement for masked reveals");
console.log("       ui-reverse-engineering/diagnosis.md → Root Cause E");
process.exit(1);
