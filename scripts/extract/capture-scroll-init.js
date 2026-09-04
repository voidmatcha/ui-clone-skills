(() => {
  if (Array.isArray(window.__uiCloneScrollWheelListeners)) return;

  const listeners = [];
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
  EventTarget.prototype.addEventListener = function patchedAddEventListener(
    type,
    listener,
    options,
  ) {
    const target = rootTargetName(this);
    if (type === "wheel" && target && !isPassive(options)) {
      listeners.push({ target, passive: false });
    }
    return originalAddEventListener.call(this, type, listener, options);
  };
})()
