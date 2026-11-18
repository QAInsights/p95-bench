export function sectionMetadata(sections) {
  return sections.map((section, index) => ({ id: section.id,
    label: section.dataset.sectionLabel ?? section.querySelector('h1,h2').textContent.trim(),
    number: String(index + 1).padStart(2, '0'), index }));
}

export function sectionShortcut(key, count) {
  return /^[1-9]$/.test(key) && Number(key) <= count ? Number(key) - 1 : null;
}
