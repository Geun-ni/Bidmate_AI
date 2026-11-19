import { useEffect, useRef, useState } from "react";
import Lenis from "lenis";

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** 부드러운(관성) 스크롤. 동작 줄이기 설정이면 켜지 않는다. */
export function useSmoothScroll() {
  useEffect(() => {
    if (reducedMotion()) return undefined;
    const lenis = new Lenis({ duration: 1.15, easing: (t) => 1 - Math.pow(1 - t, 4), smoothWheel: true });
    let frame = requestAnimationFrame(function raf(time) {
      lenis.raf(time);
      frame = requestAnimationFrame(raf);
    });
    window.__lenis = lenis; // 화면 이동 시 맨 위로 올릴 때 사용
    return () => {
      cancelAnimationFrame(frame);
      lenis.destroy();
      delete window.__lenis;
    };
  }, []);
}

export function scrollToTop() {
  if (window.__lenis) window.__lenis.scrollTo(0, { immediate: true });
  else window.scrollTo(0, 0);
}

/**
 * 스크롤 등장: [data-reveal] 요소가 화면에 들어오면 .in을 붙인다.
 * 화면이 바뀌거나 목록이 다시 그려져 새로 생긴 요소도 자동으로 잡는다.
 */
export function useScrollReveal(deps = []) {
  useEffect(() => {
    if (reducedMotion() || !("IntersectionObserver" in window)) {
      document.querySelectorAll("[data-reveal]").forEach((el) => el.classList.add("in"));
      return undefined;
    }
    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          entry.target.classList.add("in");
          io.unobserve(entry.target);
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -6% 0px" },
    );
    const watch = () => document.querySelectorAll("[data-reveal]:not(.in)").forEach((el) => io.observe(el));
    watch();
    const mo = new MutationObserver(watch);
    mo.observe(document.body, { childList: true, subtree: true });
    return () => {
      io.disconnect();
      mo.disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

/** 화면에 들어오면 0부터 목표 값까지 숫자를 올린다. */
export function useCountUp(target, { duration = 1100, decimals = 0 } = {}) {
  const ref = useRef(null);
  const [value, setValue] = useState(reducedMotion() ? target : 0);

  useEffect(() => {
    if (target === null || target === undefined || Number.isNaN(target)) return undefined;
    if (reducedMotion()) {
      setValue(target);
      return undefined;
    }
    let frame = 0;
    let from = 0;
    const run = () => {
      const start = performance.now();
      const tick = (now) => {
        const p = Math.min(1, (now - start) / duration);
        const eased = 1 - Math.pow(1 - p, 4);
        setValue(from + (target - from) * eased);
        if (p < 1) frame = requestAnimationFrame(tick);
        else from = target;
      };
      frame = requestAnimationFrame(tick);
    };
    const el = ref.current;
    if (!el || !("IntersectionObserver" in window)) {
      run();
      return () => cancelAnimationFrame(frame);
    }
    const io = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) {
        run();
        io.disconnect();
      }
    });
    io.observe(el);
    return () => {
      io.disconnect();
      cancelAnimationFrame(frame);
    };
  }, [target, duration]);

  if (target === null || target === undefined) return [ref, "–"];
  return [ref, decimals ? value.toFixed(decimals) : Math.round(value)];
}
