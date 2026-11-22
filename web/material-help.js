export function materialHelpPanel(help, showPart) {
  const panel = document.createElement('section');
  panel.className = 'materialHelp';
  const heading = document.createElement('h3');
  heading.textContent = 'How to fix ' + help.material;
  const parts = document.createElement('p');
  parts.textContent = help.part_count > 1
    ? 'Used on ' + help.part_count + ' parts, including ' + help.part_name + '.'
    : 'Used on ' + help.part_name + '.';
  const steps = document.createElement('ol');
  for (const instruction of help.steps) {
    const item = document.createElement('li');
    item.textContent = instruction;
    steps.append(item);
  }
  panel.append(heading, parts);
  if (help.texture_note) {
    const note = document.createElement('p');
    note.textContent = help.texture_note;
    panel.append(note);
  }
  panel.append(steps);
  if (showPart) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'secondary';
    button.textContent = 'Show in Parts';
    button.onclick = showPart;
    panel.append(button);
  }
  return panel;
}
