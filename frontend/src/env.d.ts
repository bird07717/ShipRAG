/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly APP_BUILD_INFO: {
    commit: string | null;
    built_at: string | null;
    dirty: boolean | null;
  };
}
