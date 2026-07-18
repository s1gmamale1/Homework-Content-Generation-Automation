// True when this bundle was built via `npm run build:viewer` (dashboard-only SPA served at :8001).
export const IS_VIEWER = import.meta.env.VITE_VIEWER === "1";
