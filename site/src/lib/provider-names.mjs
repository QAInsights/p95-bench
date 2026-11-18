const names = new Map([
  ['anthropic', 'Anthropic'], ['deepseek', 'DeepSeek'], ['gemini', 'Google'],
  ['meta', 'Meta'], ['openai', 'OpenAI'], ['qwen', 'Qwen'],
  ['thinkingmachines', 'Thinking Machines'],
]);

export const providerName = provider => names.get(provider) ?? provider;
