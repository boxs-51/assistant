const TRIGGERS = ['@', '#'];

export function handleMentionDetection(textarea, event, callback) {
  if (['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight', 'Enter'].includes(event.key)) return;

  const cursorPosition = textarea.selectionStart;
  const textBeforeCursor = textarea.value.substring(0, cursorPosition);
  const words = textBeforeCursor.split(/\s+/);
  const currentWord = words[words.length - 1];

  if (!currentWord) return;

  for (const trigger of TRIGGERS) {
    if (currentWord.startsWith(trigger)) {
      callback(trigger, currentWord.substring(trigger.length));
      return;
    }
  }
}