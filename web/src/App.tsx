import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { Layout } from "@/components/layout";
import { ProtectedRoute } from "@/components/protected-route";
import { BookPage } from "@/routes/book";
import { JobPage } from "@/routes/job";
import { LibraryPage } from "@/routes/library";
import { LoginPage } from "@/routes/login";
import { PreviewPage } from "@/routes/preview";
import { SectionPage } from "@/routes/section";
import { UploadPage } from "@/routes/upload";
import { UsagePage } from "@/routes/usage";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30_000, retry: 1 },
  },
});

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
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
            <Route index element={<UploadPage />} />
            <Route path="/library" element={<LibraryPage />} />
            <Route path="/usage" element={<UsagePage />} />
            <Route path="/book/:id" element={<BookPage />} />
            <Route path="/book/:bookId/section/:sectionId" element={<SectionPage />} />
            <Route path="/job/:id" element={<JobPage />} />
            <Route path="/preview/:id" element={<PreviewPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
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
