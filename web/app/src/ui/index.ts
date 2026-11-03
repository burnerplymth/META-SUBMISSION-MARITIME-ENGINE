/* The UI foundations: the public surface.
 *   import { Icon, Status, Button, IconButton, SegmentedControl, ... } from '../ui';
 * Styles live in ui/ui.css, imported by shell/chrome.css (loaded last). */
export { Icon, ICON_ALIAS, ICON_SIZE, resolveIcon, type IconName, type IconAlias, type IconSize, type IconProps } from './Icon';
export { ICON_NAMES } from './iconData';
export { Status, StateMark, type StatusProps } from './Status';
export { LiveStatus } from './LiveStatus';
export { Logo, Wordmark } from './Logo';
export {
  Button, IconButton, ButtonGroup, SegmentedControl, ToggleGroup, arrowTarget, Toggle, Tabs, RangeBar, Kbd, Toolbar, ToolbarSeparator, ToolbarSpacer,
  type ButtonProps, type IconButtonProps, type SegmentOption, type ControlSize, type TabOption,
} from './Controls';
export {
  DetailList, DetailRow, InspectorList, InspectorRow, InspectorSection, SectionHeader, ScrubField, Timecode, scrubValue, formatTimecode,
  type SectionHeaderProps, type ScrubFieldProps,
} from './Inspector';
export { LinkDot, LINK_GROUPS, type LinkGroup } from './LinkDot';
export { hint, isTerseHint, keyCaps, ariaKeys } from './hint';
export {
  STATE_MARK, STATE_LABEL, STATE_GROUP, STATE_KINDS, chipWord, adapterStateKind, type StateKind, type StateGroup, type StateMarkName,
} from '../shell/stateChips';
