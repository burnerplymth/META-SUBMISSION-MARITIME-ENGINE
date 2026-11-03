/**
 * Opening a rail tool from the command palette.
 * A tool belongs to shell modes (shell/tools.ts): from another mode the
 * palette first switches to the tool's first mode, then asks the shell
 * (through the nav bridge) to open the slot. Pure, so the mode rule is
 * testable without the store.
 */
import type { ShellMode } from '../shell/mode';
import { requestTool } from './navBridge';

export interface ToolOpenPlan { switchTo: ShellMode | null; tool: string }

export function planToolOpen(action: { tool: string; modes: ShellMode[] }, current: ShellMode): ToolOpenPlan {
  return { switchTo: action.modes.includes(current) ? null : (action.modes[0] ?? null), tool: action.tool };
}

/** Opening a panel or a layout from the palette hands the right column back
 *  from an open tool slot (an empty tool id is the bridge's "close"). */
export const closeToolSlot = (): void => requestTool(TOOL_NONE);
/** The bridge value that closes the tool slot instead of opening one. */
export const TOOL_NONE = '';
