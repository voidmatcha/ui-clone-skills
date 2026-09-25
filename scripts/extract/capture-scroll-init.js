(() => {
  if (Array.isArray(window.__uiCloneScrollWheelListeners)) return;

  const listeners = [];
  const inputListeners = [];
  const inputTypes = new Set(["wheel", "touchstart", "touchmove", "touchend", "touchcancel", "keydown"]);
  const originalAddEventListener = EventTarget.prototype.addEventListener;
  const rootTargetName = (target) => {
    if (target === window) return "window";
    if (target === document) return "document";
    if (target === document.documentElement) return "documentElement";
    if (target === document.body) return "body";
    return null;
  };
  const isPassive = (options) => (
    typeof options === "object" && options !== null && options.passive === true
  );

  Object.defineProperty(window, "__uiCloneScrollWheelListeners", {
    configurable: false,
    enumerable: false,
    value: listeners,
    writable: false,
  });
  Object.defineProperty(window, "__uiCloneScrollInputListeners", {
    configurable: false,
    enumerable: false,
    value: inputListeners,
    writable: false,
  });
  EventTarget.prototype.addEventListener = function patchedAddEventListener(
    type,
    listener,
    options,
  ) {
    const target = rootTargetName(this);
    if (target && inputTypes.has(type)) {
      // Record declarations, not inferred cancellation or currently active
      // ownership. Browser passive defaults and later removals can differ.
      const declaredPassive = typeof options === "object" && options !== null
        && typeof options.passive === "boolean" ? options.passive : null;
      if (!inputListeners.some(row => row.target === target && row.type === type
          && row.declaredPassive === declaredPassive)) {
        inputListeners.push({ target, type, declaredPassive });
      }
    }
    if (type === "wheel" && target && !isPassive(options)) {
      listeners.push({ target, passive: false });
    }
    return originalAddEventListener.call(this, type, listener, options);
  };
})()
