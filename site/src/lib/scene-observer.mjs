export function observeScenes(scenes, activate, viewport = window) {
  let observer;
  const observe = () => {
    observer?.disconnect();
    const middle = Math.floor(viewport.innerHeight / 2);
    observer = new viewport.IntersectionObserver(
      (entries) => {
        const current = entries.find((entry) => entry.isIntersecting);
        if (current) activate(current.target);
      },
      {
        root: null,
        rootMargin: `-${middle}px 0px -${viewport.innerHeight - middle - 1}px 0px`,
        threshold: 0,
      },
    );
    scenes.forEach((scene) => observer.observe(scene));
  };
  observe();
  viewport.addEventListener('resize', observe);
  return () => {
    observer.disconnect();
    viewport.removeEventListener('resize', observe);
  };
}
