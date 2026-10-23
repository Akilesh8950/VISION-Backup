import { animate } from "../vendor/anime.esm.min.js";

const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

export function prefersReducedMotion() {
  return reducedMotionQuery.matches;
}

function run(target, parameters) {
  if (!target || prefersReducedMotion()) return null;
  try {
    return animate(target, parameters);
  } catch (_error) {
    return null;
  }
}

export function animateIn(element) {
  if (!element || prefersReducedMotion()) return;
  element.style.opacity = "0";
  element.style.transform = "translateY(8px)";
  const animation = run(element, {
    opacity: [0, 1],
    y: [8, 0],
    duration: 180,
    ease: "outCubic",
    onComplete: () => {
      element.style.removeProperty("opacity");
      element.style.removeProperty("transform");
    },
  });
  if (!animation) {
    element.style.removeProperty("opacity");
    element.style.removeProperty("transform");
  }
}

export function animateSheet(element) {
  if (!element || prefersReducedMotion()) return;
  run(element, {
    opacity: [0, 1],
    y: [16, 0],
    duration: 200,
    ease: "outCubic",
    onComplete: () => {
      element.style.removeProperty("opacity");
      element.style.removeProperty("transform");
    },
  });
}

export function animateToast(element) {
  animateIn(element);
}

export function expandElement(element, open) {
  if (!element) return;
  if (prefersReducedMotion()) {
    element.style.display = open ? "block" : "none";
    element.style.removeProperty("height");
    element.style.removeProperty("opacity");
    element.style.removeProperty("overflow");
    return;
  }

  if (open) {
    element.style.display = "block";
    const height = element.scrollHeight;
    element.style.height = "0px";
    element.style.opacity = "0";
    element.style.overflow = "hidden";
    run(element, {
      height: [0, height],
      opacity: [0, 1],
      duration: 200,
      ease: "outCubic",
      onComplete: () => {
        element.style.removeProperty("height");
        element.style.removeProperty("opacity");
        element.style.removeProperty("overflow");
      },
    });
    return;
  }

  const height = element.scrollHeight;
  element.style.height = `${height}px`;
  element.style.overflow = "hidden";
  run(element, {
    height: [height, 0],
    opacity: [1, 0],
    duration: 180,
    ease: "outCubic",
    onComplete: () => {
      element.style.display = "none";
      element.style.removeProperty("height");
      element.style.removeProperty("opacity");
      element.style.removeProperty("overflow");
    },
  });
}

export function bindPressFeedback(root = document) {
  root.addEventListener(
    "pointerdown",
    (event) => {
      const button = event.target.closest("button");
      if (!button || button.disabled || prefersReducedMotion()) return;
      run(button, { scale: [1, 0.98], duration: 120, ease: "outQuad" });
    },
    { passive: true },
  );
  root.addEventListener(
    "pointerup",
    (event) => {
      const button = event.target.closest("button");
      if (!button || button.disabled || prefersReducedMotion()) return;
      run(button, {
        scale: [0.98, 1],
        duration: 120,
        ease: "outQuad",
        onComplete: () => button.style.removeProperty("transform"),
      });
    },
    { passive: true },
  );
}
