import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import { StoreProvider } from './store';
import './styles.css';
import './shell/tokens.css';
import './shell/shell.css';
import './shell/chrome.css';
import './shell/a11y.css';
import './shell/chrome-panels.css';
import './shell/window-layout.css';
import './shell/states-mapbar.css';
import './shell/density.css';
import './shell/narrow-layout.css';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <StoreProvider>
      <App />
    </StoreProvider>
  </React.StrictMode>,
);
