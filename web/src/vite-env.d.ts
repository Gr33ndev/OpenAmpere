/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" builds the public demo: no server, simulated data in the browser */
  readonly VITE_DEMO?: string;
}
