import React from 'react';
import { Chip } from '../../shell/Chip';
import './history.css';
import { LazyExportCentre as ExportCentrePanel } from '../reports/lazy';
import { Icon } from '../../ui/Icon';

/** The EXP rail tool: the Export Centre in the tool slot. The centre is a
 *  regular layout panel, whose own window controls (maximize, close) act on
 *  the layout and are hidden here; a slim bar hands the slot back to the
 *  shell instead. */
export function ExportTool({ onClose }: { onClose: () => void }) {
  return (
    <div className="tool-export" data-panel="tool-exports">
      <div className="tool-export-bar mono">
        <Chip kind="meta" word="EXP" />
        <span className="spacer" />
        <button type="button" className="icon-btn" title="Close" aria-label="Close exports" onClick={onClose}><Icon name="close" size="sm" /></button>
      </div>
      <ExportCentrePanel />
    </div>
  );
}
