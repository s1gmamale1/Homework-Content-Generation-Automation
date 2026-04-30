import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";
import { Layout } from "@/components/layout";
import { BookPage } from "@/routes/book";
import { JobPage } from "@/routes/job";
import { UploadPage } from "@/routes/upload";

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
          <Route element={<Layout />}>
            <Route index element={<UploadPage />} />
            <Route path="/book/:id" element={<BookPage />} />
            <Route path="/job/:id" element={<JobPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "oklch(0.13 0.018 45)",
            border: "1px solid oklch(0.99 0.005 80 / 18%)",
            color: "oklch(0.97 0.012 80)",
            backdropFilter: "blur(20px)",
          },
        }}
      />
    </QueryClientProvider>
  );
}
