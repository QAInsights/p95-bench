export const sectionNumber = number => String(number).padStart(2, '0');

export function sectionMetadata(sections) {
  return sections.map((section, index) => ({ id: section.id,
    label: section.dataset.sectionLabel ?? section.querySelector('h1,h2').textContent.trim(),
    number: sectionNumber(index + 1), index }));
}

export function sectionShortcut(key, count) {
  const number = key === '0' ? 10 : /^[1-9]$/.test(key) ? Number(key) : null;
  return number !== null && number <= count ? number - 1 : null;
}
