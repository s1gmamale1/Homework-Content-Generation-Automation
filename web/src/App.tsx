import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MotionConfig } from "motion/react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { Layout } from "@/components/layout";
import { ProtectedRoute } from "@/components/protected-route";
import { BookPage } from "@/routes/book";
import { DashboardPage } from "@/routes/dashboard";
import { DeckPage } from "@/routes/deck";
import { FleetPage } from "@/routes/fleet";
import { MonitorPage } from "@/routes/monitor";
import { JobPage } from "@/routes/job";
import { LibraryPage } from "@/routes/library";
import { LoginPage } from "@/routes/login";
import { PreviewPage } from "@/routes/preview";
import { SectionPage } from "@/routes/section";
import { UploadPage } from "@/routes/upload";
import { SettingsPage } from "@/routes/settings";
import { UsagePage } from "@/routes/usage";
import { IS_VIEWER } from "@/lib/viewer";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <MotionConfig reducedMotion="user">
        <BrowserRouter>
          <Routes>
          {/* Public route — login is the only place reachable without a token. */}
          <Route path="/login" element={<LoginPage />} />

          {/* Everything else lives behind the auth guard. */}
          <Route
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            {IS_VIEWER ? (
              <>
                {/* Dashboard-only viewer build: no fleet/library/monitor/settings
                    routes exist in this bundle, so index and unknown paths both
                    redirect to the one page that does. */}
                <Route index element={<Navigate to="/dashboard" replace />} />
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="*" element={<Navigate to="/dashboard" replace />} />
              </>
            ) : (
              <>
                {/* Fleet is the default landing page. Upload is reachable only
                    via the Library → "Upload book" button, at an explicit path. */}
                <Route index element={<FleetPage />} />
                <Route path="/upload" element={<UploadPage />} />
                <Route path="/library" element={<LibraryPage />} />
                <Route path="/usage" element={<UsagePage />} />
                <Route path="/monitor" element={<MonitorPage />} />
                <Route path="/dashboard" element={<DashboardPage />} />
                <Route path="/book/:id" element={<BookPage />} />
                <Route path="/book/:bookId/section/:sectionId" element={<SectionPage />} />
                <Route path="/job/:id" element={<JobPage />} />
                <Route path="/preview/:id" element={<PreviewPage />} />
                <Route path="/deck/:id" element={<DeckPage />} />
                <Route path="/settings" element={<SettingsPage />} />
              </>
            )}
          </Route>
          </Routes>
        </BrowserRouter>
      </MotionConfig>
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "oklch(0.17 0.005 60)",
            border: "1px solid oklch(0.27 0.005 60)",
            color: "oklch(0.97 0.005 80)",
          },
        }}
      />
    </QueryClientProvider>
  );
}
