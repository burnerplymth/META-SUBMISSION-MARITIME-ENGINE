/* Concrete colours for the report: the light institutional skin, regardless of the app theme. Chart builders write
 * var(--token) paint values; resolving them here makes every figure a standalone drawing. */
export const REPORT_TOKENS: Readonly<Record<string, string>> = {
  '--bg-panel': '#ffffff', '--bg-inset': '#f5f6f7', '--bg-raised': '#ffffff', '--bg-bar': '#eceef0', '--bg-win': '#f1f2f4',
  '--text': '#1b1d1f', '--text-2': '#4a5158', '--text-3': '#565e66',
  '--line': '#c3c7cc', '--line-soft': '#dfe2e5', '--line-strong': '#8d939a',
  '--accent': '#0b79a3', '--accent-text': '#07607f',
  '--range': '#d9710f', '--range-text': '#8f4600', '--range-fill': '#f9e2cf', '--sel-fill': '#dbe8f2',
  '--st-live': '#136d3d', '--st-snapshot': '#7d5608', '--st-sim': '#6d3fd6', '--st-model': '#1f5f9a', '--st-down': '#b23a37', '--st-notserved': '#5a6168',
  '--bar-queue': '#c65a55', '--bar-congestion': '#c65a55', '--bar-reroute': '#bfa04e', '--bar-inventory': '#5da271', '--bar-cost': '#6fc3df', '--bar-mode-shift': '#8f83b8',
  '--cs-1': '#2a78d6', '--cs-2': '#eb6834', '--cs-3': '#1baf7a', '--cs-4': '#eda100', '--cs-5': '#e87ba4', '--cs-6': '#008300', '--cs-7': '#6250d6', '--cs-8': '#e34948',
  '--c-sim': '#8a4fb5', '--c-base': '#7a8594',
};

export const lookupReportToken = (name: string): string | undefined => REPORT_TOKENS[name];

/** Colour of a chip tone: text, tint and outline. Text colours reach 4.5:1 on white. */
export const TONE: Readonly<Record<string, { fg: string; bg: string; line: string }>> = {
  modelled: { fg: '#1f5f9a', bg: '#e6eff8', line: '#9bbbdc' },
  simulated: { fg: '#5a32b4', bg: '#efeafb', line: '#b8a4e6' },
  recorded: { fg: '#7d5608', bg: '#f7efdc', line: '#d9bd7c' },
  snapshot: { fg: '#7d5608', bg: '#f7efdc', line: '#d9bd7c' },
  live: { fg: '#136d3d', bg: '#e3f3ea', line: '#8fcaa8' },
  sourced: { fg: '#136d3d', bg: '#e3f3ea', line: '#8fcaa8' },
  info: { fg: '#3d454c', bg: '#f1f2f4', line: '#c3c7cc' },
  flag: { fg: '#8f2a28', bg: '#fbeaea', line: '#e0a5a3' },
  notserved: { fg: '#4f565c', bg: '#f1f2f4', line: '#b4b9be' },
  approx: { fg: '#7d5608', bg: '#f7efdc', line: '#d9bd7c' },
  descriptive: { fg: '#3d454c', bg: '#f1f2f4', line: '#c3c7cc' },
  detected: { fg: '#7d5608', bg: '#f7efdc', line: '#d9bd7c' },
};
