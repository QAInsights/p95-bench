export function scrollWithinSection(rect, viewportHeight, backwards) {
  return rect.height > viewportHeight + 1 &&
    (backwards ? rect.top < -1 : rect.bottom > viewportHeight + 1);
}

export function regionNeedsScrollKey(region, key) {
  return !!region && region.scrollHeight > region.clientHeight + 1 &&
    ['PageDown', 'PageUp', ' ', 'ArrowDown', 'ArrowUp', 'Home', 'End'].includes(key);
}
