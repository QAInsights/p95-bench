export function taskTitle(task) {
  return task.title ?? task.name ?? task.id.replace(/^v2-[a-z]\d+-/i, '').replaceAll('-', ' ');
}

export function taskHeading(task) {
  return `${task.id} · ${taskTitle(task)}`;
}

export function replayTaskColumns(taskIds, groups) {
  return groups.flatMap(group => taskIds.slice(group.offset, group.offset + group.count)
    .map((id, index) => ({ id, column: group.column + index,
      ordinal: group.offset + index + 1, tool: group.label })));
}

export function taskCategoryGroups(tasks) {
  const groups = [];
  for (const task of tasks) {
    const category = task.category ?? 'tasks';
    const previous = groups.at(-1);
    if (previous?.category === category && previous.tool === task.tool) previous.count++;
    else groups.push({ category, tool: task.tool, label: category[0].toUpperCase() + category.slice(1), count: 1 });
  }
  return groups;
}
