export function scrollWithinSection(rect, viewportHeight, backwards) {
  return rect.height > viewportHeight + 1 &&
    (backwards ? rect.top < -1 : rect.bottom > viewportHeight + 1);
}
